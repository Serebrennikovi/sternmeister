import os
import sys
from dotenv import load_dotenv

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
# S01 template ID — still used for "first"/"second" lines via TEMPLATE_MAP
WAZZUP_TEMPLATE_ID = _require("WAZZUP_TEMPLATE_ID")

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
SEND_WINDOW_START = int(os.getenv("SEND_WINDOW_START", "9"))
SEND_WINDOW_END = int(os.getenv("SEND_WINDOW_END", "21"))
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
# Breaking change S01→S02: 93860331 (Берётар "Принято от 1й линии") теперь
# маппится на "berater_accepted" (шаблон Б1) вместо "first" (старый шаблон).
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

# Маппинг line → WABA template GUID + функция формирования переменных шаблона.
# S01: используется WAZZUP_TEMPLATE_ID из env (backward compat).
# S02: хардкоженные GUID-ы одобренных WABA-шаблонов.
# send_message() вызывает vars(**dataclasses.asdict(message_data)) — **_ поглощает лишнее.
TEMPLATE_MAP: dict[str, dict] = {
    # S01 — backward compat
    "first": {
        "template_guid": WAZZUP_TEMPLATE_ID,
        "vars": lambda name, termin_date, **_: ["SternMeister", "записи на термин", termin_date],
    },
    "second": {
        "template_guid": WAZZUP_TEMPLATE_ID,
        "vars": lambda name, termin_date, **_: ["SternMeister", "термине", termin_date],
    },
    # S02 — webhook-триггеры
    "gosniki_consultation_done": {
        "template_guid": "d253993f-e2fc-441f-a877-0c2252cb300b",
        "vars": lambda name, **_: [name],
    },
    "berater_accepted": {
        "template_guid": "18b763f8-1841-43fb-af65-669ab4c8dcea",
        "vars": lambda name, **_: [name],
    },
    # S02 — temporal-триггеры
    "berater_day_minus_7": {
        "template_guid": None,  # ЗАГЛУШКА — не прошёл WABA (>550 символов)
        "vars": None,
    },
    "berater_day_minus_3": {
        "template_guid": "140a1ed5-7047-4de1-aa0d-d3fe5e0d912a",
        "vars": lambda name, institution, weekday, date, **_: [name, institution, weekday, date],
    },
    "berater_day_minus_1": {
        "template_guid": "7732e8ac-1bcc-42d6-a723-bbb80b635c79",
        "vars": lambda name, **_: [name],
    },
    "berater_day_0": {
        "template_guid": "176a8b5b-8704-4d04-aee5-0fbd08641806",
        "vars": lambda name, **_: [name],
    },
}


def determine_line(pipeline_id: int, status_id: int) -> str | None:
    """Determine message line by pipeline and status ID."""
    statuses = PIPELINE_CONFIG.get(pipeline_id)
    if statuses is None:
        return None
    return statuses.get(status_id)
