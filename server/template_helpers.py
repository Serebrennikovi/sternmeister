"""Shared helpers for S02 template composition and fallback handling."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

BERLIN_TZ = ZoneInfo("Europe/Berlin")
CUSTOMER_FACING_BERATER = "с Бератором"
B1_FALLBACK_INSTITUTION = CUSTOMER_FACING_BERATER
B1_NO_DATE_DATETIME_TEXT = "дату и время сообщим дополнительно"
B2_CHECKLIST_TEXT = (
    "1. Удостоверение личности: Personalausweis, Reisepass или Aufenthaltstitel\n"
    "2. Приглашение на термин (Einladung)\n"
    "3. Angebot от SternMeister\n"
    "4. Ваше мотивационное письмо"
)
TIME_FALLBACK = ""
AA_DAY_MINUS_7_ALLOWED_STATUSES = frozenset({102183943, 102183947})
TEMPORAL_DAYS_TO_LINE = {
    7: "berater_day_minus_7",
    3: "berater_day_minus_3",
    1: "berater_day_minus_1",
    0: "berater_day_0",
}
STALE_BERATER_ACCEPTED_TEMPORAL_LINES = frozenset({
    "berater_day_minus_3",
    "berater_day_minus_1",
    "berater_day_0",
})


def coerce_date(value: object) -> date | None:
    """Return a ``date`` value only when the input is a real date object."""
    if isinstance(value, date):
        return value
    return None


def normalize_time_raw(value: object) -> str | None:
    """Normalize raw time-like values from Kommo into non-empty strings."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def build_gosniki_consultation_done_texts(name: str | None) -> dict[str, str]:
    """Return keyed customer-facing texts for the Г1 line."""
    safe_name = (name or "").strip() or "Клиент"
    return {
        "news_text": (
            f"{safe_name}, вы получили комплект документов, необходимых для записи на термин. "
            "Мы уже забронировали для вас место для консультации с нашим карьерным экспертом. "
            "Пожалуйста, постарайтесь сегодня записаться на термин."
        ),
    }


def pick_berater_accepted_institution_and_date(
    date_dc: date | None,
    date_aa: date | None,
    *,
    today: date | None = None,
) -> tuple[str, str | None]:
    """Select customer-facing context + template date using DC/AA priority rules for Б1."""
    if today is None:
        today = datetime.now(tz=BERLIN_TZ).date()

    if date_dc and date_aa:
        dc_distance = abs((date_dc - today).days)
        aa_distance = abs((date_aa - today).days)
        # If distances are equal, DC wins by business rule.
        if dc_distance <= aa_distance:
            return CUSTOMER_FACING_BERATER, date_dc.strftime("%d.%m.%Y")
        return CUSTOMER_FACING_BERATER, date_aa.strftime("%d.%m.%Y")
    if date_dc:
        return CUSTOMER_FACING_BERATER, date_dc.strftime("%d.%m.%Y")
    if date_aa:
        return CUSTOMER_FACING_BERATER, date_aa.strftime("%d.%m.%Y")
    return B1_FALLBACK_INSTITUTION, None


def iter_temporal_candidates(
    date_dc: date | None,
    date_aa: date | None,
    status_id: int | None,
    *,
    today: date | None = None,
) -> list[tuple[str, date]]:
    """Return active temporal candidates in DC -> AA order for a lead."""
    if today is None:
        today = datetime.now(tz=BERLIN_TZ).date()

    candidates: list[tuple[str, date]] = []
    for source_kind, termin_date_obj in (("dc", date_dc), ("aa", date_aa)):
        if termin_date_obj is None:
            continue
        days_until = (termin_date_obj - today).days
        line = TEMPORAL_DAYS_TO_LINE.get(days_until)
        if line is None:
            continue
        if (
            source_kind == "aa"
            and line == "berater_day_minus_7"
            and status_id not in AA_DAY_MINUS_7_ALLOWED_STATUSES
        ):
            continue
        candidates.append((line, termin_date_obj))
    return candidates


def has_newer_berater_temporal_state(
    date_dc: date | None,
    date_aa: date | None,
    status_id: int | None,
    *,
    today: date | None = None,
) -> bool:
    """Return True when a lead already matches a later temporal line than Б1."""
    return any(
        line in STALE_BERATER_ACCEPTED_TEMPORAL_LINES
        for line, _ in iter_temporal_candidates(
            date_dc,
            date_aa,
            status_id,
            today=today,
        )
    )


def build_berater_accepted_texts(name: str | None) -> dict[str, str]:
    """Return keyed fallback-safe template values for ``berater_accepted``."""
    safe_name = (name or "").strip() or "Клиент"
    return {
        "name": safe_name,
    }


def build_berater_day_minus_3_schedule_text(
    *,
    date_obj: date | None = None,
    weekday: str | None = None,
    date_text: str | None = None,
) -> str:
    """Build a single schedule string for the Б3 template."""
    if date_obj is not None:
        return f"{_weekday_name_ru(date_obj)}, {date_obj.strftime('%d.%m.%Y')}"

    safe_weekday = (weekday or "").strip()
    safe_date_text = (date_text or "").strip()

    if safe_weekday and safe_date_text:
        return f"{safe_weekday}, {safe_date_text}"
    if safe_date_text:
        return safe_date_text
    if safe_weekday:
        return safe_weekday
    return "дату сообщим дополнительно"


def build_berater_day_minus_1_texts(
    date_for_template: str,
    time_raw: object,
) -> dict[str, str | None]:
    """Build line-specific composite strings for ``berater_day_minus_1``."""
    normalized_time_raw = normalize_time_raw(time_raw) or TIME_FALLBACK
    if normalized_time_raw:
        datetime_text = f"{date_for_template} в {normalized_time_raw}"
    else:
        datetime_text = date_for_template
    return {
        "time_text": normalized_time_raw,
        "datetime_text": datetime_text,
    }


def _weekday_name_ru(d: date) -> str:
    """Return Russian weekday name without importing ``server.utils``."""
    return (
        "Понедельник",
        "Вторник",
        "Среда",
        "Четверг",
        "Пятница",
        "Суббота",
        "Воскресенье",
    )[d.weekday()]
