"""Tests for POST /webhook/kommo handler (T06, updated for S02 in T12).

Uses mocked external services (Kommo API, Wazzup messenger, DB).
The real PIPELINE_CONFIG is used for determine_line() tests.

S02 breaking change: status 93860331 (Берётар "Принято от первой линии")
now maps to "berater_accepted" (S02 template Б1) instead of "first" (S01).
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.app import app


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kommo_json_payload(lead_id=123, status_id=93860331, pipeline_id=12154099):
    """Build a JSON payload mimicking Kommo webhook.

    Default status_id=93860331 → berater_accepted (Бух Бератер pipeline).
    """
    return {
        "leads": {
            "status": [{
                "id": lead_id,
                "status_id": status_id,
                "pipeline_id": pipeline_id,
                "old_status_id": 99999,
            }]
        },
        "account": {"id": "12345", "subdomain": "sternmeister"},
    }


def _single_result(resp):
    """Extract the single result from a webhook response with one lead."""
    data = resp.json()
    assert data["status"] == "ok"
    assert len(data["results"]) == 1
    return data["results"][0]


def _make_lead(custom_fields=None):
    """Build a fake Kommo lead dict."""
    return {
        "id": 123,
        "pipeline_id": 12154099,
        "status_id": 93860331,
        "custom_fields_values": custom_fields or [],
    }


def _make_contact(phone="+491234567890", contact_id=456, name="Test User"):
    """Build a fake Kommo contact dict."""
    return {
        "id": contact_id,
        "name": name,
        "custom_fields_values": [
            {
                "field_code": "PHONE",
                "values": [{"value": phone}],
            }
        ],
    }


def _patch_full_happy_path(
    mock_get_kommo,
    mock_get_messenger,
    *,
    termin_date="25.02.2026",
    phone="+491234567890",
    name="Test User",
    send_result=None,
):
    """Wire up kommo + messenger mocks for full happy-path tests.

    S02: berater_accepted and gosniki_consultation_done require name extraction.
    """
    kommo = MagicMock()
    lead = _make_lead(custom_fields=[
        {"field_id": 885996, "values": [{"value": 1740441600}]},
    ])
    contact = _make_contact(phone=phone, name=name)
    kommo.get_lead_contact.return_value = (lead, contact)
    kommo.extract_phone.return_value = phone
    kommo.extract_name.return_value = name
    kommo.extract_termin_date.return_value = termin_date
    mock_get_kommo.return_value = kommo

    messenger = MagicMock()
    messenger.build_message_text.return_value = "test message text"
    if send_result is None:
        send_result = {"message_id": "wazzup-msg-123"}
    messenger.send_message.return_value = send_result
    mock_get_messenger.return_value = messenger

    return kommo, messenger


def _patch_happy_path_with_termin_fallback(
    mock_get_kommo,
    mock_get_messenger,
    *,
    termin_field_returns,
    phone="+491234567890",
    name="Test User",
):
    """Wire up mocks where extract_termin_date returns different values per field_id.

    Args:
        termin_field_returns: dict mapping field_id -> return value for extract_termin_date.
    """
    kommo = MagicMock()
    lead = _make_lead()
    contact = _make_contact(phone=phone, name=name)
    kommo.get_lead_contact.return_value = (lead, contact)
    kommo.extract_phone.return_value = phone
    kommo.extract_name.return_value = name
    kommo.extract_termin_date.side_effect = lambda lead_data, fid: termin_field_returns.get(fid)
    mock_get_kommo.return_value = kommo

    messenger = MagicMock()
    messenger.build_message_text.return_value = "test message text"
    messenger.send_message.return_value = {"message_id": "wazzup-msg-123"}
    mock_get_messenger.return_value = messenger

    return kommo, messenger


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    """GET /health endpoint."""

    def test_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "send_window" in data
        assert "in_window" in data
        assert "server_time_utc" in data
        assert "server_time_berlin" in data
        assert "failed_temporal" in data  # S02: new field

    def test_in_window_is_bool(self, client):
        resp = client.get("/health")
        assert isinstance(resp.json()["in_window"], bool)

    def test_failed_temporal_is_int(self, client):
        resp = client.get("/health")
        assert isinstance(resp.json()["failed_temporal"], int)


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

class TestWebhookValidation:
    """Early returns on invalid / irrelevant payloads."""

    def test_empty_payload(self, client):
        resp = client.post("/webhook/kommo", json={})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Not a status change event"

    def test_no_status_key(self, client):
        resp = client.post("/webhook/kommo", json={"leads": {"update": []}})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Not a status change event"

    def test_empty_status_list(self, client):
        resp = client.post("/webhook/kommo", json={"leads": {"status": []}})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Empty status list"

    def test_invalid_lead_fields(self, client):
        """Missing required fields in lead status entry."""
        resp = client.post("/webhook/kommo", json={
            "leads": {"status": [{"id": "abc"}]}
        })
        assert resp.status_code == 200
        result = _single_result(resp)
        assert result["status"] == "error"
        assert "Invalid payload" in result["message"]

    def test_unknown_pipeline(self, client):
        payload = _kommo_json_payload(pipeline_id=999999)
        resp = client.post("/webhook/kommo", json=payload)
        assert resp.status_code == 200
        result = _single_result(resp)
        assert result["message"] == "Status not relevant"

    def test_unknown_status_id(self, client):
        payload = _kommo_json_payload(status_id=999999)
        resp = client.post("/webhook/kommo", json=payload)
        assert resp.status_code == 200
        result = _single_result(resp)
        assert result["message"] == "Status not relevant"

    def test_malformed_form_body(self, client):
        """Corrupted form body -> parsed as empty dict -> 'Not a status change event'."""
        resp = client.post(
            "/webhook/kommo",
            content=b"\xff\xfe invalid bytes",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Not a status change event"

    def test_malformed_json_body(self, client):
        """Invalid JSON body -> parsed as empty dict -> 'Not a status change event'."""
        resp = client.post(
            "/webhook/kommo",
            content=b"not json at all {{{",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Not a status change event"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestWebhookDedup:
    """Deduplication — skip if same lead+line was processed recently."""

    @patch("server.app.get_recent_message")
    def test_duplicate_skipped(self, mock_recent, client):
        mock_recent.return_value = {
            "id": 42, "created_at": "2026-02-24T10:00:00+00:00",
        }
        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        assert resp.status_code == 200
        result = _single_result(resp)
        assert result["message"] == "Duplicate webhook, already processed"
        assert result["existing_message_id"] == 42


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestWebhookHappyPath:
    """Full pipeline: lead -> contact -> phone -> termin -> send -> note."""

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_successful_send(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """S02: berater_accepted — sends message, saves template_values=[name]."""
        kommo, messenger = _patch_full_happy_path(
            mock_get_kommo, mock_get_messenger,
        )

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        assert resp.status_code == 200

        result = _single_result(resp)
        assert result["message_id"] == 99
        assert result["messenger_message_id"] == "wazzup-msg-123"

        # Verify create_message called with correct args
        call_kwargs = mock_create.call_args_list[-1].kwargs
        assert call_kwargs["status"] == "sent"
        assert call_kwargs["messenger_id"] == "wazzup-msg-123"
        assert call_kwargs["kommo_lead_id"] == 123
        assert call_kwargs["kommo_contact_id"] == 456
        assert call_kwargs["phone"] == "+491234567890"
        assert call_kwargs["line"] == "berater_accepted"
        assert call_kwargs["message_text"] == "test message text"
        assert call_kwargs["sent_at"] is not None
        assert call_kwargs["next_retry_at"] is not None
        # S02: template_values includes the client name
        assert call_kwargs["template_values"] == json.dumps(["Test User"])

        # Verify note added to Kommo with correct format
        kommo.add_note.assert_called_once()
        note_args = kommo.add_note.call_args
        assert note_args[0][0] == 123  # lead_id
        note_text = note_args[0][1]
        assert "(berater_accepted)" in note_text
        assert "UTC" in note_text

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=False)
    @patch(
        "server.app.get_next_send_window_start",
        return_value="2026-02-25T08:00:00+00:00",
    )
    def test_outside_send_window_creates_pending(
        self, mock_next, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        _patch_full_happy_path(mock_get_kommo, mock_get_messenger)

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result["message"] == "Scheduled for next send window"
        assert result["next_retry_at"] == "2026-02-25T08:00:00+00:00"

        # Verify "pending" status, attempts=0, and next_retry_at
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["status"] == "pending"
        assert call_kwargs["attempts"] == 0
        assert call_kwargs["next_retry_at"] == "2026-02-25T08:00:00+00:00"

        # Messenger send must NOT be called
        mock_get_messenger.return_value.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Multiple leads in one webhook
# ---------------------------------------------------------------------------

class TestWebhookMultipleLeads:
    """Kommo may batch multiple status changes in one webhook."""

    def test_two_leads_both_irrelevant(self, client):
        """Two irrelevant statuses -> two 'Status not relevant' results."""
        payload = {
            "leads": {
                "status": [
                    {"id": 1, "status_id": 999, "pipeline_id": 12154099},
                    {"id": 2, "status_id": 888, "pipeline_id": 12154099},
                ]
            }
        }
        resp = client.post("/webhook/kommo", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        results = data["results"]
        assert len(results) == 2
        assert results[0]["message"] == "Status not relevant"
        assert results[1]["message"] == "Status not relevant"

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_batch_mixed_success_and_irrelevant(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """One relevant (success) + one irrelevant in batch."""
        kommo, messenger = _patch_full_happy_path(
            mock_get_kommo, mock_get_messenger,
        )

        payload = {
            "leads": {
                "status": [
                    {"id": 123, "status_id": 93860331, "pipeline_id": 12154099},
                    {"id": 456, "status_id": 999, "pipeline_id": 12154099},
                ]
            }
        }
        resp = client.post("/webhook/kommo", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        results = data["results"]
        assert len(results) == 2
        # First: success
        assert results[0].get("message_id") == 99
        # Second: irrelevant
        assert results[1]["message"] == "Status not relevant"

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_batch_one_error_does_not_break_other(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """One lead errors (RuntimeError), the other succeeds — both processed."""
        kommo = MagicMock()
        call_count = 0

        def side_effect_get_lead_contact(lead_id):
            nonlocal call_count
            call_count += 1
            if lead_id == 100:
                raise RuntimeError("unexpected bug")
            lead = _make_lead()
            contact = _make_contact()
            return lead, contact

        kommo.get_lead_contact.side_effect = side_effect_get_lead_contact
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_name.return_value = "Test User"
        kommo.extract_termin_date.return_value = "25.02.2026"
        mock_get_kommo.return_value = kommo

        messenger = MagicMock()
        messenger.build_message_text.return_value = "test message text"
        messenger.send_message.return_value = {"message_id": "wazzup-msg-123"}
        mock_get_messenger.return_value = messenger

        payload = {
            "leads": {
                "status": [
                    {"id": 100, "status_id": 93860331, "pipeline_id": 12154099},
                    {"id": 200, "status_id": 93860331, "pipeline_id": 12154099},
                ]
            }
        }
        resp = client.post("/webhook/kommo", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        results = data["results"]
        assert len(results) == 2
        # First: error (caught by outer try/except)
        assert results[0]["status"] == "error"
        assert results[0]["message"] == "Internal error"
        # Second: success
        assert results[1].get("message_id") == 99


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestWebhookErrors:
    """Kommo API errors, missing data, messenger failures."""

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_recent_message", return_value=None)
    def test_kommo_api_error(self, mock_recent, mock_get_kommo, client):
        from server.kommo import KommoAPIError

        kommo = MagicMock()
        kommo.get_lead_contact.side_effect = KommoAPIError("Rate limited", 429)
        mock_get_kommo.return_value = kommo

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result["status"] == "error"
        assert "Kommo API error" in result["message"]

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_recent_message", return_value=None)
    def test_no_phone(self, mock_recent, mock_get_kommo, client):
        kommo = MagicMock()
        kommo.get_lead_contact.return_value = (_make_lead(), _make_contact())
        kommo.extract_phone.return_value = None
        mock_get_kommo.return_value = kommo

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result["status"] == "error"
        assert "Phone not found" in result["message"]

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_recent_message", return_value=None)
    def test_no_name_for_berater_accepted(self, mock_recent, mock_get_kommo, client):
        """S02: berater_accepted requires name — returns error if not found."""
        kommo = MagicMock()
        kommo.get_lead_contact.return_value = (_make_lead(), _make_contact())
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_name.return_value = None  # Name not found
        kommo.extract_termin_date.return_value = None  # Termin optional for berater_accepted
        mock_get_kommo.return_value = kommo

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result["status"] == "error"
        assert "Name not found" in result["message"]

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_messenger_error_saves_failed_with_retry(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        from server.messenger import MessengerError

        kommo, messenger = _patch_full_happy_path(
            mock_get_kommo, mock_get_messenger,
        )
        messenger.send_message.side_effect = MessengerError("Wazzup24 timeout")

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result["status"] == "error"

        # Verify "failed" message created with next_retry_at for T08 cron
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["next_retry_at"] is not None

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_recent_message", return_value=None)
    def test_contact_missing_id(self, mock_recent, mock_get_kommo, client):
        kommo = MagicMock()
        contact_no_id = {"custom_fields_values": []}
        kommo.get_lead_contact.return_value = (_make_lead(), contact_no_id)
        mock_get_kommo.return_value = kommo

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result["status"] == "error"
        assert "Contact missing id" in result["message"]


# ---------------------------------------------------------------------------
# Termin date field fallback (3 field IDs)
# ---------------------------------------------------------------------------

class TestWebhookTerminFallback:
    """Verify termin date extraction tries all 3 field IDs in order.

    S02: berater_accepted (default payload) allows empty termin_date,
    but requires a name. The fallback tests verify field priority;
    when a date is found it's stored, when not found "" is stored.
    """

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_first_field_has_date(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """Primary field (885996) has date -> used."""
        _patch_happy_path_with_termin_fallback(
            mock_get_kommo, mock_get_messenger,
            termin_field_returns={885996: "25.02.2026", 887026: None, 887028: None},
        )
        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result.get("message_id") == 99
        assert mock_create.call_args.kwargs["termin_date"] == "25.02.2026"

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_fallback_to_second_field(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """Primary field empty, DC field (887026) has date -> used."""
        _patch_happy_path_with_termin_fallback(
            mock_get_kommo, mock_get_messenger,
            termin_field_returns={885996: None, 887026: "01.03.2026", 887028: None},
        )
        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result.get("message_id") == 99
        assert mock_create.call_args.kwargs["termin_date"] == "01.03.2026"

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_fallback_to_third_field(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """Primary and DC fields empty, AA field (887028) has date -> used."""
        _patch_happy_path_with_termin_fallback(
            mock_get_kommo, mock_get_messenger,
            termin_field_returns={885996: None, 887026: None, 887028: "10.03.2026"},
        )
        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result.get("message_id") == 99
        assert mock_create.call_args.kwargs["termin_date"] == "10.03.2026"

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_recent_message", return_value=None)
    def test_all_fields_empty_berater_accepted_proceeds_with_name(
        self, mock_recent, mock_get_kommo, client,
    ):
        """S02: berater_accepted — all termin fields empty is OK (termin optional).
        Error only if name is also missing.
        """
        kommo = MagicMock()
        kommo.get_lead_contact.return_value = (_make_lead(), _make_contact())
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_name.return_value = None  # Name missing → error
        kommo.extract_termin_date.return_value = None
        mock_get_kommo.return_value = kommo

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        result = _single_result(resp)
        assert result["status"] == "error"
        # berater_accepted: termin optional → error is "Name not found", not "Termin date"
        assert "Name not found" in result["message"]


# ---------------------------------------------------------------------------
# Gosniki pipeline (S02)
# ---------------------------------------------------------------------------

class TestWebhookGosnikAndBerater:
    """Verify S02 PIPELINE_CONFIG mappings (gosniki + berater_accepted)."""

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_gosniki_consultation_done(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """Бух Гос 'Консультация проведена' (status_id=95514983) → gosniki_consultation_done."""
        kommo, messenger = _patch_full_happy_path(
            mock_get_kommo, mock_get_messenger,
        )

        payload = _kommo_json_payload(status_id=95514983, pipeline_id=10935879)
        resp = client.post("/webhook/kommo", json=payload)
        assert resp.status_code == 200
        result = _single_result(resp)
        assert result.get("message_id") == 99

        call_kwargs = mock_create.call_args_list[-1].kwargs
        assert call_kwargs["line"] == "gosniki_consultation_done"

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_berater_accepted_status(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """Бух Бератер 'Принято от первой линии' (status_id=93860331) → berater_accepted."""
        kommo, messenger = _patch_full_happy_path(
            mock_get_kommo, mock_get_messenger,
        )

        payload = _kommo_json_payload(status_id=93860331, pipeline_id=12154099)
        resp = client.post("/webhook/kommo", json=payload)
        assert resp.status_code == 200
        result = _single_result(resp)
        assert result.get("message_id") == 99

        call_kwargs = mock_create.call_args_list[-1].kwargs
        assert call_kwargs["line"] == "berater_accepted"


# ---------------------------------------------------------------------------
# add_note failure is non-critical
# ---------------------------------------------------------------------------

class TestWebhookAddNoteFailure:
    """add_note failure must not break the webhook response."""

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_add_note_error_still_returns_ok(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        from server.kommo import KommoAPIError

        kommo, messenger = _patch_full_happy_path(
            mock_get_kommo, mock_get_messenger,
        )
        kommo.add_note.side_effect = KommoAPIError("Kommo 500", 500)

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        assert resp.status_code == 200

        result = _single_result(resp)
        assert result["message_id"] == 99
        assert result["messenger_message_id"] == "wazzup-msg-123"


# ---------------------------------------------------------------------------
# Form-encoded webhook (real Kommo sends x-www-form-urlencoded)
# ---------------------------------------------------------------------------

class TestWebhookFormEncoded:
    """Kommo sends PHP bracket notation, not JSON."""

    def test_form_payload_parsed(self, client):
        """Form-encoded status change for an irrelevant status."""
        body = (
            b"leads[status][0][id]=123"
            b"&leads[status][0][status_id]=999"
            b"&leads[status][0][pipeline_id]=12154099"
        )
        resp = client.post(
            "/webhook/kommo",
            content=body,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        result = _single_result(resp)
        assert result["message"] == "Status not relevant"

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=99)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_form_encoded_happy_path(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """Form-encoded webhook with relevant status triggers full pipeline.

        Important: real Kommo sends form-encoded, so string->int conversion
        in the handler must work for pipeline_id, status_id, lead_id.
        """
        kommo, messenger = _patch_full_happy_path(
            mock_get_kommo, mock_get_messenger,
        )

        # Real Kommo status_id for Берётар "Принято от первой линии" (berater_accepted)
        body = (
            b"leads[status][0][id]=123"
            b"&leads[status][0][status_id]=93860331"
            b"&leads[status][0][pipeline_id]=12154099"
            b"&leads[status][0][old_status_id]=99999"
        )
        resp = client.post(
            "/webhook/kommo",
            content=body,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        result = _single_result(resp)
        assert result["message_id"] == 99

        # Verify the string "93860331" was correctly parsed as int -> "berater_accepted"
        call_kwargs = mock_create.call_args_list[-1].kwargs
        assert call_kwargs["line"] == "berater_accepted"


# ---------------------------------------------------------------------------
# Catch-all: unexpected exceptions still return 200
# ---------------------------------------------------------------------------

class TestWebhookCatchAll:
    """Unexpected exceptions in _process_lead_status -> 200 with error body."""

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_recent_message", return_value=None)
    def test_unexpected_error_returns_200(self, mock_recent, mock_get_kommo, client):
        kommo = MagicMock()
        kommo.get_lead_contact.side_effect = RuntimeError("unexpected bug")
        mock_get_kommo.return_value = kommo

        resp = client.post("/webhook/kommo", json=_kommo_json_payload())
        assert resp.status_code == 200
        result = _single_result(resp)
        assert result["status"] == "error"
        assert result["message"] == "Internal error"
