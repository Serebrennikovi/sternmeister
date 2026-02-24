"""Telegram alerts for error notifications (T09).

Sends alerts to a Telegram chat when errors occur in webhook processing
or cron jobs.  Graceful degradation: if TELEGRAM_BOT_TOKEN or
TELEGRAM_ALERT_CHAT_ID are not configured, alerts are logged but not sent.
"""

import logging
import threading
from datetime import datetime, timezone

import requests

from server.utils import mask_phone

logger = logging.getLogger(__name__)


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown v1 special characters in user-provided text."""
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


class TelegramAlerter:
    """Sends alert messages to a Telegram chat via Bot API."""

    def __init__(self) -> None:
        from server import config  # late import: allow importing without .env

        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_ALERT_CHAT_ID
        self.enabled = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            logger.warning(
                "Telegram alerts disabled: TELEGRAM_BOT_TOKEN or "
                "TELEGRAM_ALERT_CHAT_ID not set",
            )

    def send_alert(self, message: str, level: str = "ERROR") -> bool:
        """Send an alert to Telegram.

        Args:
            message: alert text (Markdown-safe).
            level: one of "ERROR", "WARNING", "INFO".

        Returns True if sent successfully, False otherwise.
        """
        if not self.enabled:
            logger.info("Telegram alert (disabled, not sent): [%s] %s", level, message)
            return False

        emoji_map = {"ERROR": "\U0001f534", "WARNING": "\u26a0\ufe0f", "INFO": "\u2139\ufe0f"}
        emoji = emoji_map.get(level, "\U0001f4cc")
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        formatted = f"{emoji} *{level}*\n\n{message}\n\n_Time: {timestamp}_"

        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": formatted,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error(
                    "Telegram API error %d: %s", resp.status_code, resp.text[:200],
                )
                return False
            return True
        except requests.exceptions.RequestException as exc:
            logger.error("Telegram alert request failed: %s", exc)
            return False

    def alert_messenger_error(self, phone: str, error: str) -> bool:
        """Alert: WhatsApp send failure."""
        masked = mask_phone(phone)
        safe_error = _escape_md(error)
        return self.send_alert(
            f"Ошибка отправки WhatsApp\nТелефон: `{masked}`\nОшибка: {safe_error}",
        )

    def alert_kommo_error(self, lead_id: int, error: str) -> bool:
        """Alert: Kommo API error."""
        safe_error = _escape_md(error)
        return self.send_alert(
            f"Ошибка Kommo API\nLead ID: {lead_id}\nОшибка: {safe_error}",
        )

    def alert_cron_error(self, error: str) -> bool:
        """Alert: cron job fatal error."""
        safe_error = _escape_md(error)
        return self.send_alert(f"Ошибка cron-задачи\n\n{safe_error}")

    def alert_unexpected_error(self, error: str) -> bool:
        """Alert: unexpected/unhandled error in webhook processing."""
        safe_error = _escape_md(error)
        return self.send_alert(f"Неожиданная ошибка webhook\n\n{safe_error}")

    def alert_info(self, message: str) -> bool:
        """Informational alert."""
        safe_message = _escape_md(message)
        return self.send_alert(safe_message, level="INFO")


_alerter: TelegramAlerter | None = None
_alerter_lock = threading.Lock()


def get_alerter() -> TelegramAlerter:
    """Lazy singleton — config is read on first access."""
    global _alerter
    if _alerter is None:
        with _alerter_lock:
            if _alerter is None:
                _alerter = TelegramAlerter()
    return _alerter


def _reset_alerter() -> None:
    """Reset the shared alerter (for tests only)."""
    global _alerter
    with _alerter_lock:
        _alerter = None
