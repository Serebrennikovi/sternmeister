import os
import sys
from dotenv import load_dotenv

from server.template_helpers import (
    B2_CHECKLIST_TEXT,
    CUSTOMER_FACING_BERATER,
)

load_dotenv()


def _require(name: str) -> str:
    """Return env var value or exit with a clear error."""
    value = os.getenv(name)
    if not value:
        print(f"FATAL: required env var {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


# Kommo CRM
KOMMO_DOMAIN = _require("KOMMO_DOMAIN")
KOMMO_TOKEN = _require("KOMMO_TOKEN")

# Wazzup24
WAZZUP_API_KEY = _require("WAZZUP_API_KEY")
WAZZUP_API_URL = os.getenv("WAZZUP_API_URL", "https://api.wazzup24.com/v3")
WAZZUP_CHANNEL_ID = _require("WAZZUP_CHANNEL_ID")
# Kommo webhook validation (secret-in-URL, Kommo doesn't send HMAC headers)
KOMMO_WEBHOOK_SECRET = os.getenv("KOMMO_WEBHOOK_SECRET", "")
if not KOMMO_WEBHOOK_SECRET:
    print(
        "WARNING: KOMMO_WEBHOOK_SECRET is not set — "
        "webhook endpoint is unprotected!",
        file=sys.stderr,
    )

# Telegram (optional — alerts won't work without these)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")

# Settings
SEND_WINDOW_START = int(os.getenv("SEND_WINDOW_START", "8"))
SEND_WINDOW_END = int(os.getenv("SEND_WINDOW_END", "22"))
if not (0 <= SEND_WINDOW_START < SEND_WINDOW_END <= 24):
    print(
        f"FATAL: invalid send window: SEND_WINDOW_START={SEND_WINDOW_START}, "
        f"SEND_WINDOW_END={SEND_WINDOW_END} (need 0 <= START < END <= 24)",
        file=sys.stderr,
    )
    sys.exit(1)
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "2"))
RETRY_INTERVAL_HOURS = float(os.getenv("RETRY_INTERVAL_HOURS", "24"))
DEDUP_WINDOW_MINUTES = int(os.getenv("DEDUP_WINDOW_MINUTES", "10"))

# Phone whitelist (testing mode): comma-separated normalized numbers.
# If set, only these phones will receive messages; others are skipped.
# Remove this var (or leave empty) to send to everyone.
_raw_whitelist = os.getenv("PHONE_WHITELIST", "").strip()
PHONE_WHITELIST: set[str] | None = (
    {p.strip() for p in _raw_whitelist.split(",") if p.strip()}
    if _raw_whitelist else None
)

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/messages.db")

# Kommo CRM Pipeline Config (S02, актуальные status_id)
# S02: 93860331 (Бератер "Принято от 1й линии") → "berater_accepted" (шаблон Б1).
PIPELINE_CONFIG = {
    12154099: {  # Бух Бератер
        93860331: "berater_accepted",  # Принято от первой линии → шаблон Б1
    },
    10935879: {  # Бух Гос
        95514983: "gosniki_consultation_done",  # Консультация проведена → шаблон Г1
    },
}

# СТОП-этапы: лид на этих этапах → temporal-триггеры НЕ отправляются
STOP_STATUSES: dict[int, set[int]] = {
    12154099: {93860875, 93860883},  # ДЦ отменён/перенесён, АА отменён/перенесён
}

FIELD_IDS = {
    "date_termin": 885996,
    "date_termin_dc": 887026,
    "date_termin_aa": 887028,
    "time_termin": 886670,
    "language_level": 869928,
    "lead_email": 889539,
    "contact_phone": 849496,
    "contact_email": 849498,
}


def _non_empty(value: object, fallback: str) -> str:
    """Return a non-empty trimmed string for template variables."""
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _optional_text(value: object) -> str:
    """Return a trimmed string or an empty string for optional template variables."""
    if value is None:
        return ""
    return str(value).strip()


# Маппинг line → WABA template GUID + функция формирования переменных шаблона.
# send_message() вызывает vars(**dataclasses.asdict(message_data)) — **_ поглощает лишнее.
TEMPLATE_MAP: dict[str, dict] = {
    # S02 — webhook-триггеры
    "gosniki_consultation_done": {
        # Approved T17 replacement: "Информация о запросе" / informaciya_o_zaprose_3.
        "template_guid": "95ddec60-bb6b-44a8-b5fb-a98abd76f974",
        "vars": lambda news_text, **_: [
            "SternMeister",
            _non_empty(
                news_text,
                (
                    "Клиент, вы получили комплект документов, необходимых для записи на термин. "
                    "Мы уже забронировали для вас место для консультации с нашим карьерным экспертом. "
                    "Пожалуйста, постарайтесь сегодня записаться на термин."
                ),
            ),
        ],
    },
    "berater_accepted": {
        "template_guid": "47d2946c-f66a-4697-b702-eb5d138bb1f1",
        "vars": lambda name, **_: [
            _non_empty(name, "Клиент"),
        ],
    },
    # S02 — temporal-триггеры
    "berater_day_minus_7": {
        "template_guid": "b028964c-9c27-4bc9-9b97-02a5e283df16",
        "vars": lambda name, date, institution, checklist_text, **_: [
            _non_empty(name, "Клиент"),
            _non_empty(date, "дату сообщим дополнительно"),
            _non_empty(institution, CUSTOMER_FACING_BERATER),
            _non_empty(checklist_text, B2_CHECKLIST_TEXT),
        ],
    },
    "berater_day_minus_3": {
        "template_guid": "e1cb07aa-5236-4f8a-84dc-fef26b3cccf6",
        "vars": lambda name, institution, schedule_text, **_: [
            _non_empty(name, "Клиент"),
            _non_empty(institution, CUSTOMER_FACING_BERATER),
            _non_empty(schedule_text, "дату сообщим дополнительно"),
        ],
    },
    "berater_day_minus_1": {
        # Approved T17 replacement: "Напоминание за 1 день" / napominanie_za_1_den_2.
        "template_guid": "a9b04e05-6b6c-4a5f-9463-d8a0d96316f4",
        "vars": lambda name, datetime_text, **_: [
            _non_empty(name, "Клиент"),
            _non_empty(datetime_text, "дату сообщим дополнительно"),
        ],
    },
    "berater_day_0": {
        "template_guid": "176a8b5b-8704-4d04-aee5-0fbd08641806",
        "vars": lambda name, **_: [_non_empty(name, "Клиент")],
    },
}


def determine_line(pipeline_id: int, status_id: int) -> str | None:
    """Determine message line by pipeline and status ID."""
    statuses = PIPELINE_CONFIG.get(pipeline_id)
    if statuses is None:
        return None
    return statuses.get(status_id)
