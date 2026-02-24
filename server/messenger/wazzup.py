import logging
import threading
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
_429_RETRY_DELAY = 1  # seconds between 429 retries
_5XX_RETRY_DELAY = 1  # seconds between 5xx retries

_VALID_LINES = frozenset({"first", "second"})


class MessengerError(Exception):
    """Ошибка отправки сообщения."""


@dataclass
class MessageData:
    """Данные для формирования сообщения."""
    line: str           # "first" или "second"
    termin_date: str    # "25.02.2026" (DD.MM.YYYY из kommo.extract_termin_date)

    def __post_init__(self):
        if self.line not in _VALID_LINES:
            raise ValueError(f"Invalid line: {self.line!r}, expected one of {_VALID_LINES}")
        if not self.termin_date:
            raise ValueError("termin_date must not be empty")


def _mask_phone(phone: str) -> str:
    """Mask phone for logging: +491234567890 -> +49***7890."""
    if len(phone) > 7:
        return phone[:3] + "***" + phone[-4:]
    return "***"


_TEMPLATE_TEXT = (
    "Здравствуйте. Это {company}. "
    "Напоминаем о {event} в {date}. Скажите, все в силе?"
)


class WazzupMessenger:
    """Wazzup24 WABA messenger.

    Uses blocking requests + time.sleep for 429/5xx retry.
    Safe when called from sync ``def`` FastAPI handlers (runs in threadpool).
    Do NOT call from ``async def`` handlers — will block the event loop.
    """

    def __init__(self) -> None:
        from server import config  # late import: allow importing module without .env

        self.channel_id = config.WAZZUP_CHANNEL_ID
        self.base_url = config.WAZZUP_API_URL
        self.template_id = config.WAZZUP_TEMPLATE_ID
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {config.WAZZUP_API_KEY}",
            "User-Agent": "SternmeisterBot/1.0",
        })

    def _format_chat_id(self, phone: str) -> str:
        """Оставить только цифры: +491234567890 -> 491234567890."""
        return "".join(c for c in phone if c.isdigit())

    def _build_template_values(self, message_data: MessageData) -> list[str]:
        """
        Построить значения переменных для WABA-шаблона.

        Шаблон "Напоминание о записи или встрече":
        "Здравствуйте. Это {{1}}. Напоминаем о {{2}} в {{3}}. Скажите, все в силе?"

        Returns:
            ["SternMeister", "записи на термин", "25.02.2026"]
        """
        company = "SternMeister"
        event = "записи на термин" if message_data.line == "first" else "термине"
        return [company, event, message_data.termin_date]

    def build_message_text(self, message_data: MessageData) -> str:
        """Return the human-readable message text (for DB logging)."""
        vals = self._build_template_values(message_data)
        return _TEMPLATE_TEXT.format(company=vals[0], event=vals[1], date=vals[2])

    def send_message(self, phone: str, message_data: MessageData) -> dict:
        """
        Отправить WhatsApp через Wazzup24 WABA.

        Args:
            phone: номер в формате +491234567890
            message_data: параметры шаблона (line, termin_date)

        Returns:
            {"message_id": "...", "status": "sent", "message_text": "..."}

        Raises:
            MessengerError: при любой ошибке отправки

        Note:
            Wazzup24 возвращает 201 Created при успешной отправке.
        """
        url = f"{self.base_url}/message"
        chat_id = self._format_chat_id(phone)
        template_values = self._build_template_values(message_data)
        message_text = self.build_message_text(message_data)

        payload = {
            "channelId": self.channel_id,
            "chatId": chat_id,
            "chatType": "whatsapp",
            "templateId": self.template_id,
            "templateValues": template_values,
        }

        masked = _mask_phone(phone)
        logger.info("Sending WhatsApp to %s via Wazzup24", masked)

        resp = self._request_with_retry(url, payload)

        try:
            data = resp.json()
        except ValueError as exc:
            raise MessengerError(f"Invalid JSON response: {resp.text[:200]}") from exc

        message_id = data.get("messageId")
        if not message_id:
            raise MessengerError(
                f"Wazzup24 returned {resp.status_code} but no messageId: {resp.text[:200]}"
            )

        logger.info("WhatsApp sent to %s, messageId=%s", masked, message_id)
        return {"message_id": message_id, "status": "sent", "message_text": message_text}

    def _request_with_retry(self, url: str, payload: dict) -> requests.Response:
        """POST request with 429/5xx retry logic.

        Retries up to MAX_RETRIES times on transient errors (429, 5xx).
        All non-retryable HTTP errors (4xx) raise MessengerError immediately.
        """
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.post(url, json=payload, timeout=15)
            except requests.exceptions.Timeout as exc:
                # No retry on network errors — POST may have been delivered
                raise MessengerError("Wazzup24 timeout (15s)") from exc
            except requests.exceptions.RequestException as exc:
                raise MessengerError(f"Request failed: {exc}") from exc

            if resp.status_code == 429:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        "Wazzup24 429 rate limit, retrying in %ds (attempt %d/%d)",
                        _429_RETRY_DELAY, attempt + 1, MAX_RETRIES,
                    )
                    time.sleep(_429_RETRY_DELAY)
                    continue
                raise MessengerError("Wazzup24 429 rate limit, retries exhausted")

            if resp.status_code >= 500:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        "Wazzup24 %d server error, retrying in %ds (attempt %d/%d)",
                        resp.status_code, _5XX_RETRY_DELAY, attempt + 1, MAX_RETRIES,
                    )
                    time.sleep(_5XX_RETRY_DELAY)
                    continue
                raise MessengerError(
                    f"Wazzup24 HTTP {resp.status_code}: {resp.text[:200]}"
                )

            # Non-retryable client errors (order matters: specific before catchall)
            if resp.status_code == 400:
                raise MessengerError(f"Invalid request: {resp.text[:200]}")
            elif resp.status_code == 401:
                raise MessengerError("Unauthorized: проверьте WAZZUP_API_KEY")
            elif resp.status_code == 403:
                raise MessengerError("Forbidden: проверьте тип API-ключа Wazzup24")
            elif resp.status_code >= 402:
                raise MessengerError(
                    f"Wazzup24 HTTP {resp.status_code}: {resp.text[:200]}"
                )

            return resp

        raise MessengerError("Wazzup24 retries exhausted")


_messenger: WazzupMessenger | None = None
_messenger_lock = threading.Lock()


def get_messenger() -> WazzupMessenger:
    """Lazy singleton — config читается только при первом вызове."""
    global _messenger
    if _messenger is None:
        with _messenger_lock:
            if _messenger is None:
                _messenger = WazzupMessenger()
    return _messenger


def _reset_messenger() -> None:
    """Reset the shared messenger (for tests only)."""
    global _messenger
    with _messenger_lock:
        _messenger = None
