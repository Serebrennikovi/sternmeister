import dataclasses
import logging
import threading
import time
from dataclasses import dataclass, field

import requests

from server.utils import mask_phone

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
_429_RETRY_DELAY = 1  # seconds between 429 retries
_5XX_RETRY_DELAY = 1  # seconds between 5xx retries

# All valid line values — must match TEMPLATE_MAP keys in config.py
_VALID_LINES = frozenset({
    "first", "second",
    "gosniki_consultation_done", "berater_accepted",
    "berater_day_minus_7", "berater_day_minus_3",
    "berater_day_minus_1", "berater_day_0",
})


class MessengerError(Exception):
    """Ошибка отправки сообщения."""


@dataclass
class MessageData:
    """Данные для формирования сообщения."""
    line: str           # одно из значений _VALID_LINES
    termin_date: str    # "25.02.2026" (DD.MM.YYYY) или "" для Г1/Б1
    name: str | None = field(default=None)          # имя клиента ({{1}} в S02-шаблонах)
    institution: str | None = field(default=None)   # "Jobcenter" / "Agentur für Arbeit"
    weekday: str | None = field(default=None)       # "Понедельник", "Вторник", ...
    date: str | None = field(default=None)          # дата термина для шаблона "DD.MM.YYYY"

    def __post_init__(self):
        if self.line not in _VALID_LINES:
            raise ValueError(f"Invalid line: {self.line!r}, expected one of {_VALID_LINES}")
        # termin_date="" is allowed for lines where date is optional (gosniki_consultation_done,
        # berater_accepted). Non-empty check is enforced by S01 lines in app.py logic.


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
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {config.WAZZUP_API_KEY}",
            "User-Agent": "SternmeisterBot/1.0",
        })

    def _format_chat_id(self, phone: str) -> str:
        """Оставить только цифры: +491234567890 -> 491234567890."""
        return "".join(c for c in phone if c.isdigit())

    def build_message_text(
        self,
        message_data: MessageData,
        template_values: list | None = None,
    ) -> str:
        """Return the human-readable message text (for DB logging).

        S01 lines: full template text.
        S02 lines: "[template] var1, var2, ..."  (exact template text unavailable).
        berater_day_minus_7 (заглушка): "[berater_day_minus_7] (placeholder)".

        Pass pre-computed ``template_values`` (from send_message) to avoid
        calling vars_fn twice on the same send.
        """
        from server.config import TEMPLATE_MAP
        if template_values is None:
            entry = TEMPLATE_MAP.get(message_data.line, {})
            vars_fn = entry.get("vars")
            if vars_fn is None:
                return f"[{message_data.line}] (placeholder)"
            template_values = vars_fn(**dataclasses.asdict(message_data))
        return f"[template] {', '.join(str(v) for v in template_values)}"

    def send_message(self, phone: str, message_data: MessageData) -> dict:
        """
        Отправить WhatsApp через Wazzup24 WABA.

        Args:
            phone: номер в формате +491234567890
            message_data: параметры шаблона

        Returns:
            {"message_id": "...", "status": "sent", "message_text": "..."}
            или {"status": "skipped"} если template_guid is None (заглушка Б2).

        Raises:
            MessengerError: при любой ошибке отправки

        Note:
            Wazzup24 возвращает 201 Created при успешной отправке.
        """
        from server.config import TEMPLATE_MAP

        entry = TEMPLATE_MAP[message_data.line]
        template_guid = entry["template_guid"]
        vars_fn = entry["vars"]

        # Заглушка (berater_day_minus_7): шаблон не прошёл WABA → пропустить
        if template_guid is None:
            logger.info(
                "Template for line=%s is placeholder (no GUID), skipping. "
                "termin_date=%s",
                message_data.line, message_data.termin_date,
            )
            return {"status": "skipped"}

        url = f"{self.base_url}/message"
        chat_id = self._format_chat_id(phone)
        template_values = vars_fn(**dataclasses.asdict(message_data))
        message_text = self.build_message_text(message_data, template_values=template_values)

        payload = {
            "channelId": self.channel_id,
            "chatId": chat_id,
            "chatType": "whatsapp",
            "templateId": template_guid,
            "templateValues": template_values,
        }

        masked = mask_phone(phone)
        logger.info("Sending WhatsApp to %s via Wazzup24 (line=%s)", masked, message_data.line)

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
