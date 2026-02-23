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
WAZZUP_TEMPLATE_GUID = _require("WAZZUP_TEMPLATE_GUID")

# Kommo webhook validation (optional for now, required for production)
KOMMO_WEBHOOK_SECRET = os.getenv("KOMMO_WEBHOOK_SECRET", "")

# Telegram (optional — alerts won't work without these)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")

# Settings
SEND_WINDOW_START = int(os.getenv("SEND_WINDOW_START", "9"))
SEND_WINDOW_END = int(os.getenv("SEND_WINDOW_END", "21"))
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "2"))
RETRY_INTERVAL_HOURS = int(os.getenv("RETRY_INTERVAL_HOURS", "24"))

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/messages.db")

# Kommo CRM Pipeline Config (из T01)
PIPELINE_CONFIG = {
    12154099: {  # Берётар
        9386032: "first",    # Принято от первой линии
        10093587: "second",  # Термин ДЦ
    },
    10631243: {  # Госники
        8152349: "first",    # Принято от первой линии
    },
}

FIELD_IDS = {
    "date_termin": 885996,
    "date_termin_dc": 887026,
    "date_termin_aa": 887028,
    "language_level": 869928,
    "lead_email": 889539,
    "contact_phone": 849496,
    "contact_email": 849498,
}


def determine_line(pipeline_id: int, status_id: int) -> str | None:
    """Determine message line (first/second) by pipeline and status ID."""
    statuses = PIPELINE_CONFIG.get(pipeline_id)
    if statuses is None:
        return None
    return statuses.get(status_id)
