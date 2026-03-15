import logging
import re
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
DEFAULT_RETRY_AFTER = 2  # seconds
MAX_RETRY_AFTER = 10  # cap to prevent long thread blocking
_5XX_RETRY_DELAY = 1  # seconds between 5xx retries

_PHONE_STRIP_RE = re.compile(r"[\s\-\(\)\.\/]+")

_BERLIN_TZ = ZoneInfo("Europe/Berlin")


class KommoAPIError(Exception):
    """Kommo API error with optional HTTP status code."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class KommoClient:
    """Kommo CRM API v4 client.

    Uses blocking requests + time.sleep for 429/5xx retry.
    Safe when called from sync ``def`` FastAPI handlers (runs in threadpool).
    Do NOT call from ``async def`` handlers — will block the event loop.
    """

    def __init__(self):
        from server import config  # late import: allow importing module without .env

        self.base_url = f"https://{config.KOMMO_DOMAIN}/api/v4"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {config.KOMMO_TOKEN}",
            "User-Agent": "SternmeisterBot/1.0",
        })

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Make an HTTP request with 429/5xx retry and unified error handling.

        All errors (network, HTTP) are raised as KommoAPIError.
        """
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", 10)

        try:
            for attempt in range(MAX_RETRIES):
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:
                    if attempt < MAX_RETRIES - 1:
                        retry_after = DEFAULT_RETRY_AFTER
                        try:
                            retry_after = int(response.json().get("retry_after", DEFAULT_RETRY_AFTER))
                        except Exception:
                            pass
                        retry_after = min(retry_after, MAX_RETRY_AFTER)
                        logger.warning(
                            "Kommo 429 rate limit, retrying in %ds (attempt %d/%d)",
                            retry_after, attempt + 1, MAX_RETRIES,
                        )
                        time.sleep(retry_after)
                        continue
                    raise KommoAPIError(
                        f"Kommo 429 rate limit, retries exhausted on {method} {path}",
                        429,
                    )

                if response.status_code >= 500:
                    if attempt < MAX_RETRIES - 1:
                        logger.warning(
                            "Kommo %d server error on %s %s, retrying in %ds (attempt %d/%d)",
                            response.status_code, method, path,
                            _5XX_RETRY_DELAY, attempt + 1, MAX_RETRIES,
                        )
                        time.sleep(_5XX_RETRY_DELAY)
                        continue
                    raise KommoAPIError(
                        f"Kommo server error {response.status_code} on {method} {path}",
                        response.status_code,
                    )

                if response.status_code == 401:
                    logger.debug("Kommo 401 response: %.200s", response.text)
                    raise KommoAPIError(
                        f"Unauthorized: check KOMMO_TOKEN ({method} {path})", 401,
                    )
                if response.status_code == 404:
                    raise KommoAPIError(f"Not found: {method} {path}", 404)
                if response.status_code >= 400:
                    logger.debug("Kommo %d response: %.200s", response.status_code, response.text)
                    raise KommoAPIError(
                        f"Kommo API error {response.status_code} on {method} {path}",
                        response.status_code,
                    )

                return response

            raise KommoAPIError(f"Retries exhausted on {method} {path}")

        except KommoAPIError:
            raise
        except requests.exceptions.RequestException as exc:
            raise KommoAPIError(f"Request failed: {exc}") from exc

    @staticmethod
    def _parse_json(response: requests.Response) -> dict:
        """Parse JSON response body, raising KommoAPIError on failure."""
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise KommoAPIError(
                f"Invalid JSON in response from {response.request.method} {response.request.path_url}",
            ) from exc

    def get_lead_with_contacts(self, lead_id: int) -> dict:
        """Fetch a lead with embedded contacts.

        Note: embedded contacts are abbreviated (only id, is_main) —
        use get_contact() for full data including custom_fields_values.
        """
        response = self._request("GET", f"/leads/{lead_id}", params={"with": "contacts"})
        lead = self._parse_json(response)
        logger.debug(
            "Fetched lead %d (pipeline=%s, status=%s)",
            lead_id, lead.get("pipeline_id"), lead.get("status_id"),
        )
        return lead

    def get_contact(self, contact_id: int) -> dict:
        """Fetch full contact data including custom_fields_values."""
        response = self._request("GET", f"/contacts/{contact_id}")
        logger.debug("Fetched contact %d", contact_id)
        return self._parse_json(response)

    def get_lead_contact(self, lead_id: int) -> tuple[dict, dict]:
        """Fetch a lead and its main contact (full data).

        Convenience wrapper: calls get_lead_with_contacts() then
        get_contact() for the main linked contact (is_main=True).
        Falls back to the first contact if none is marked as main.

        Returns:
            (lead_data, contact_data) tuple.

        Raises:
            KommoAPIError: if lead has no linked contacts.
        """
        lead = self.get_lead_with_contacts(lead_id)
        contacts = (lead.get("_embedded") or {}).get("contacts") or []
        if not contacts:
            raise KommoAPIError(f"Lead {lead_id} has no linked contacts")
        main = next((c for c in contacts if c.get("is_main")), contacts[0])
        contact = self.get_contact(main["id"])
        return lead, contact

    @staticmethod
    def extract_phone(contact_data: dict) -> str | None:
        """Extract and normalize the first phone number from a contact.

        Uses the first value from the PHONE multitext field.
        Kommo contacts may have multiple phones — only the first is used
        (typically the primary/mobile entered by the sales manager).

        Args:
            contact_data: full contact object (from get_contact()).

        Returns:
            Normalized phone (e.g. "+491234567890") or None.
        """
        for field in contact_data.get("custom_fields_values") or []:
            if field.get("field_code") == "PHONE":
                values = field.get("values") or []
                if values:
                    raw = values[0].get("value")
                    if raw:
                        phone = _normalize_phone(raw)
                        if phone is not None:
                            return phone
        logger.warning(
            "Phone not found for contact %s", contact_data.get("id"),
        )
        return None

    @staticmethod
    def extract_name(contact_data: dict) -> str | None:
        """Extract the contact's full name from Kommo contact object.

        Kommo stores the full name in the top-level ``name`` field of the
        contact. This is used for S02 templates that include {{1}}=имя.

        Args:
            contact_data: full contact object (from get_contact()).

        Returns:
            Full name string (e.g. "Иван Иванов") or None if not found.
        """
        name = contact_data.get("name")
        if not name:
            logger.warning(
                "Name not found for contact %s", contact_data.get("id"),
            )
            return None
        return str(name)

    @staticmethod
    def extract_termin_date(lead_data: dict, field_id: int) -> str | None:
        """Extract a termin date from lead custom fields by field_id.

        Kommo stores date fields as Unix timestamps.  This method
        converts them to "DD.MM.YYYY" in the Europe/Berlin timezone
        (all Sternmeister clients are in Germany, single timezone CET/CEST).

        Args:
            lead_data: the lead dict.
            field_id: one of config.FIELD_IDS values (e.g. 885996).

        Returns:
            Date string (e.g. "25.02.2026") or None.
        """
        for field in lead_data.get("custom_fields_values") or []:
            if field.get("field_id") == field_id:
                values = field.get("values") or []
                if values:
                    raw = values[0].get("value")
                    if raw is not None:
                        try:
                            ts = int(raw)
                            dt = datetime.fromtimestamp(ts, tz=_BERLIN_TZ)
                            return dt.strftime("%d.%m.%Y")
                        except (ValueError, TypeError, OSError):
                            logger.warning(
                                "Cannot parse termin date value %r (field %d)",
                                raw, field_id,
                            )
                            return None
        logger.debug(
            "Termin date field %d not found in lead %s",
            field_id, lead_data.get("id"),
        )
        return None

    @staticmethod
    def extract_time_termin(lead_data: dict, field_id: int) -> str | None:
        """Extract a termin time from lead custom fields by field_id.

        Kommo stores date/time values as Unix timestamps.
        Returns time in Europe/Berlin timezone as ``HH:MM``.
        """
        for field in lead_data.get("custom_fields_values") or []:
            if field.get("field_id") == field_id:
                values = field.get("values") or []
                if values:
                    raw = values[0].get("value")
                    if raw is not None:
                        try:
                            ts = int(raw)
                            dt = datetime.fromtimestamp(ts, tz=_BERLIN_TZ)
                            return dt.strftime("%H:%M")
                        except (ValueError, TypeError, OSError):
                            logger.warning(
                                "Cannot parse termin time value %r (field %d)",
                                raw, field_id,
                            )
                            return None
        logger.debug(
            "Termin time field %d not found in lead %s",
            field_id, lead_data.get("id"),
        )
        return None

    def get_active_leads(self, pipeline_id: int) -> list[dict]:
        """Fetch all active leads for a pipeline with embedded contacts.

        Iterates pages of 250 until the API returns 204 No Content or an
        empty page.  Won/lost leads are excluded by Kommo by default.

        Each lead includes ``_embedded.contacts`` with contact IDs (no full
        data — use ``get_contact()`` for phone/name).

        Args:
            pipeline_id: Kommo pipeline ID (e.g. 12154099 for Бух Бератер).

        Returns:
            List of lead dicts.

        Raises:
            KommoAPIError: on any API or network error.
        """
        leads: list[dict] = []
        page = 1
        while True:
            response = self._request("GET", "/leads", params={
                "filter[pipeline_id][]": pipeline_id,
                "with": "contacts",
                "page": page,
                "limit": 250,
            })
            if response.status_code == 204:
                break
            data = self._parse_json(response)
            page_leads = (data.get("_embedded") or {}).get("leads") or []
            if not page_leads:
                break
            leads.extend(page_leads)
            if len(page_leads) < 250:
                break  # Last partial page — no need for another request
            page += 1
        logger.info(
            "Fetched %d active leads for pipeline %d", len(leads), pipeline_id,
        )
        return leads

    @staticmethod
    def _extract_date_from_field(lead: dict, field_id: int) -> date | None:
        """Extract a Unix-timestamp date field from lead custom_fields_values.

        Returns a ``datetime.date`` in Europe/Berlin timezone, or ``None``
        if the field is absent or the value cannot be parsed.
        """
        for field in lead.get("custom_fields_values") or []:
            if field.get("field_id") == field_id:
                values = field.get("values") or []
                if values:
                    raw = values[0].get("value")
                    if raw is not None:
                        try:
                            ts = int(raw)
                            return datetime.fromtimestamp(ts, tz=_BERLIN_TZ).date()
                        except (ValueError, TypeError, OSError):
                            logger.warning(
                                "Cannot parse date field %d value %r in lead %s",
                                field_id, raw, lead.get("id"),
                            )
                            return None
        logger.debug("Date field %d not found in lead %s", field_id, lead.get("id"))
        return None

    @staticmethod
    def extract_termin_date_dc(lead: dict) -> date | None:
        """Extract Jobcenter (ДЦ) termin date from field 887026 as datetime.date."""
        return KommoClient._extract_date_from_field(lead, 887026)

    @staticmethod
    def extract_termin_date_aa(lead: dict) -> date | None:
        """Extract Agentur für Arbeit (АА) termin date from field 887028 as datetime.date."""
        return KommoClient._extract_date_from_field(lead, 887028)

    def add_note(self, lead_id: int, text: str) -> dict:
        """Add a text note to a lead.

        Returns:
            The created note dict with 'id'.
        """
        payload = [{"note_type": "common", "params": {"text": text}}]
        response = self._request("POST", f"/leads/{lead_id}/notes", json=payload)
        data = self._parse_json(response)
        try:
            note = data["_embedded"]["notes"][0]
        except (KeyError, IndexError, TypeError):
            logger.error("Unexpected add_note response for lead %d: %s", lead_id, data)
            raise KommoAPIError(f"Unexpected add_note response for lead {lead_id}")
        logger.info("Added note to lead %d (note_id=%s)", lead_id, note.get("id"))
        return note


_MIN_PHONE_DIGITS = 7


def _normalize_phone(raw: str) -> str | None:
    """Normalize a phone number for WhatsApp delivery.

    Handles common German formats:
      "+49 176 1234 5678"    → "+4917612345678"
      "004917612345678"      → "+4917612345678"
      "+49 (0) 176 1234567"  → "+491761234567"
      "0176 1234 5678"       → "+4917612345678"  (local → +49 assumed)

    Returns None if the result has fewer than 7 digits (invalid phone).
    """
    cleaned = raw.strip()

    # Remove (0) trunk prefix notation: "+49 (0) 176..." → "+49 176..."
    # Must be done BEFORE stripping parens, otherwise "(0)" becomes "0"
    cleaned = cleaned.replace("(0)", "")

    # Strip whitespace, dashes, parens, dots, slashes
    cleaned = _PHONE_STRIP_RE.sub("", cleaned)

    # Replace international 00 prefix with +
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif not cleaned.startswith("+"):
        # Local German number (e.g. "017612345678") → prepend +49, drop leading 0
        if cleaned.startswith("0"):
            cleaned = "+49" + cleaned[1:]
        else:
            cleaned = "+" + cleaned

    digit_count = sum(c.isdigit() for c in cleaned)
    if digit_count < _MIN_PHONE_DIGITS:
        logger.warning("Phone too short after normalization: %r -> %r", raw, cleaned)
        return None
    return cleaned


_client: KommoClient | None = None
_client_lock = threading.Lock()


def get_kommo_client() -> KommoClient:
    """Return the shared KommoClient instance (lazy init, thread-safe).

    Use this instead of importing a module-level instance — allows
    importing KommoAPIError and other names without triggering config
    loading (important for tests).
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = KommoClient()
    return _client


def _reset_client() -> None:
    """Reset the shared client (for tests only)."""
    global _client
    with _client_lock:
        _client = None
