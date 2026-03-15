import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from server.config import SEND_WINDOW_START, SEND_WINDOW_END

logger = logging.getLogger(__name__)

_BERLIN_TZ = ZoneInfo("Europe/Berlin")


def mask_phone(phone: str) -> str:
    """Mask phone for PII protection: +491234567890 -> +49***7890."""
    if len(phone) > 7:
        return phone[:3] + "***" + phone[-4:]
    return "***"


def is_in_send_window() -> bool:
    """Check if current Berlin time is within the send window (8:00-22:00)."""
    now_berlin = datetime.now(tz=_BERLIN_TZ)
    return SEND_WINDOW_START <= now_berlin.hour < SEND_WINDOW_END


def get_next_send_window_start() -> str:
    """Return next send window start (8:00 Berlin) as ISO 8601 UTC string.

    Used for scheduling pending messages when outside the send window.
    All DB timestamps are stored in UTC.

    Constructs the target datetime from date + hour to avoid
    ``timedelta(days=1)`` giving wrong wall-clock across DST transitions
    (spring-forward = 23h day, fall-back = 25h day).
    """
    now_berlin = datetime.now(tz=_BERLIN_TZ)
    today_start = now_berlin.replace(
        hour=SEND_WINDOW_START, minute=0, second=0, microsecond=0,
    )
    if now_berlin < today_start:
        next_start = today_start
    else:
        tomorrow = now_berlin.date() + timedelta(days=1)
        next_start = datetime(
            tomorrow.year, tomorrow.month, tomorrow.day,
            SEND_WINDOW_START, 0, 0,
            tzinfo=_BERLIN_TZ,
        )
    return next_start.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Kommo form-data parser
# ---------------------------------------------------------------------------

_BRACKET_KEY_RE = re.compile(r"[^\[\]]+")


def parse_bracket_form(raw_body: bytes) -> dict:
    """Parse x-www-form-urlencoded body with PHP-style bracket notation.

    Kommo webhooks send data like::

        leads[status][0][id]=123&leads[status][0][status_id]=456

    This converts it to a nested dict::

        {"leads": {"status": [{"id": "123", "status_id": "456"}]}}

    Numeric-only bracket keys create list positions; everything else
    creates dict keys.  All leaf values are strings.
    """
    flat = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    result: dict = {}
    for compound_key, values in flat.items():
        parts = _BRACKET_KEY_RE.findall(compound_key)
        if not parts:
            continue
        _set_nested(result, parts, values[0])
    return result


_WEEKDAY_NAMES_RU = [
    "Понедельник", "Вторник", "Среда",
    "Четверг", "Пятница", "Суббота", "Воскресенье",
]


def weekday_name(d: date) -> str:
    """Return Russian weekday name for a date.

    Uses hardcoded list — locale is not set in Docker.
    """
    return _WEEKDAY_NAMES_RU[d.weekday()]


def format_date_ru(d: date) -> str:
    """Format a date as DD.MM.YYYY (Russian convention)."""
    return d.strftime("%d.%m.%Y")


def _set_nested(root: dict, keys: list[str], value: str) -> None:
    """Walk *keys* path, creating dicts/lists as needed, set *value* at leaf."""
    current: dict | list = root
    for i, key in enumerate(keys[:-1]):
        next_key = keys[i + 1]
        next_is_index = next_key.isdigit()

        if isinstance(current, list):
            idx = int(key)
            while len(current) <= idx:
                current.append([] if next_is_index else {})
            current = current[idx]
        else:
            if key not in current:
                current[key] = [] if next_is_index else {}
            current = current[key]

    last = keys[-1]
    if isinstance(current, list):
        idx = int(last)
        while len(current) <= idx:
            current.append(None)
        current[idx] = value
    else:
        current[last] = value
