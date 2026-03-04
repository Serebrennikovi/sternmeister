"""Tests for S02 webhook changes: gosniki_consultation_done, berater_accepted (T12)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.config import determine_line


@pytest.fixture
def client():
    return TestClient(app)


def _payload(lead_id=100, status_id=95514983, pipeline_id=10935879):
    return {
        "leads": {
            "status": [{
                "id": lead_id,
                "status_id": status_id,
                "pipeline_id": pipeline_id,
            }]
        }
    }


def _make_contact(contact_id=200, phone="+491234567890", name="Анна Мюллер"):
    c = {
        "id": contact_id,
        "name": name,
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": phone}]},
        ],
    }
    return c


def _make_lead(lead_id=100, pipeline_id=10935879, status_id=95514983):
    return {
        "id": lead_id,
        "pipeline_id": pipeline_id,
        "status_id": status_id,
        "custom_fields_values": [],
    }


def _single_result(resp):
    data = resp.json()
    assert data["status"] == "ok"
    assert len(data["results"]) == 1
    return data["results"][0]


# -----------------------------------------------------------------------
# Pipeline config coverage
# -----------------------------------------------------------------------

class TestNewStatusMappings:
    def test_gosniki_consultation_done_line(self):
        """Status 95514983 (pipeline 10935879) → gosniki_consultation_done."""
        assert determine_line(10935879, 95514983) == "gosniki_consultation_done"

    def test_berater_accepted_line(self):
        """Status 93860331 (pipeline 12154099) → berater_accepted."""
        assert determine_line(12154099, 93860331) == "berater_accepted"

    def test_unknown_status_ignored(self, client):
        resp = client.post("/webhook/kommo", json=_payload(
            status_id=999999, pipeline_id=12154099,
        ))
        result = _single_result(resp)
        assert result["message"] == "Status not relevant"

    def test_old_pipeline_10631243_not_recognized(self, client):
        """Old Gosniki pipeline 10631243 is no longer in PIPELINE_CONFIG."""
        resp = client.post("/webhook/kommo", json=_payload(
            status_id=95514983, pipeline_id=10631243,
        ))
        result = _single_result(resp)
        assert result["message"] == "Status not relevant"


# -----------------------------------------------------------------------
# Gosniki happy path (Г1)
# -----------------------------------------------------------------------

class TestGosnikWebhook:
    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=11)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_gosniki_sends_message_with_name(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """Gosniki webhook: name extracted, MessageData.name filled, message sent."""
        kommo = MagicMock()
        contact = _make_contact(name="Иван Петров")
        kommo.get_lead_contact.return_value = (_make_lead(), contact)
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_name.return_value = "Иван Петров"
        kommo.extract_termin_date.return_value = None  # optional for Г1
        mock_get_kommo.return_value = kommo

        messenger = MagicMock()
        messenger.build_message_text.return_value = "[template] Иван Петров"
        messenger.send_message.return_value = {"message_id": "msg-g1-001"}
        mock_get_messenger.return_value = messenger

        resp = client.post("/webhook/kommo", json=_payload(
            status_id=95514983, pipeline_id=10935879,
        ))
        result = _single_result(resp)
        assert result["message_id"] == 11
        assert result["messenger_message_id"] == "msg-g1-001"

        # Verify create_message called with line=gosniki_consultation_done and template_values
        call_kw = mock_create.call_args.kwargs
        assert call_kw["line"] == "gosniki_consultation_done"
        assert call_kw["termin_date"] == ""  # no termin_date → ""
        assert call_kw["template_values"] == json.dumps(["Иван Петров"])

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_recent_message", return_value=None)
    def test_gosniki_without_name_returns_error(
        self, mock_recent, mock_get_kommo, client,
    ):
        """Gosniki webhook: name not found → error (template requires {{1}}=имя)."""
        kommo = MagicMock()
        contact = _make_contact(name=None)
        contact["name"] = None
        kommo.get_lead_contact.return_value = (_make_lead(), contact)
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_name.return_value = None
        kommo.extract_termin_date.return_value = None
        mock_get_kommo.return_value = kommo

        resp = client.post("/webhook/kommo", json=_payload(
            status_id=95514983, pipeline_id=10935879,
        ))
        result = _single_result(resp)
        assert result["status"] == "error"
        assert "Name not found" in result["message"]

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=12)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_gosniki_proceeds_without_termin_date(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """Gosniki: termin_date optional — empty string stored, message still sent."""
        kommo = MagicMock()
        kommo.get_lead_contact.return_value = (_make_lead(), _make_contact(name="Клара"))
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_name.return_value = "Клара"
        kommo.extract_termin_date.return_value = None  # No date
        mock_get_kommo.return_value = kommo

        messenger = MagicMock()
        messenger.build_message_text.return_value = "[template] Клара"
        messenger.send_message.return_value = {"message_id": "msg-g1-002"}
        mock_get_messenger.return_value = messenger

        resp = client.post("/webhook/kommo", json=_payload(
            status_id=95514983, pipeline_id=10935879,
        ))
        result = _single_result(resp)
        assert result["message_id"] == 12
        # Verify termin_date stored as ""
        assert mock_create.call_args.kwargs["termin_date"] == ""


# -----------------------------------------------------------------------
# Berater accepted happy path (Б1)
# -----------------------------------------------------------------------

class TestBeraterAcceptedWebhook:
    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=21)
    @patch("server.app.get_recent_message", return_value=None)
    @patch("server.app.is_in_send_window", return_value=True)
    def test_berater_accepted_sends_message(
        self, mock_window, mock_recent, mock_create,
        mock_get_messenger, mock_get_kommo, client,
    ):
        """Berater accepted webhook: line=berater_accepted, template_values=[name]."""
        kommo = MagicMock()
        contact = _make_contact(name="Мария Шмидт")
        kommo.get_lead_contact.return_value = (
            _make_lead(pipeline_id=12154099, status_id=93860331), contact
        )
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_name.return_value = "Мария Шмидт"
        kommo.extract_termin_date.return_value = "01.04.2026"
        mock_get_kommo.return_value = kommo

        messenger = MagicMock()
        messenger.build_message_text.return_value = "[template] Мария Шмидт"
        messenger.send_message.return_value = {"message_id": "msg-b1-001"}
        mock_get_messenger.return_value = messenger

        resp = client.post("/webhook/kommo", json=_payload(
            status_id=93860331, pipeline_id=12154099,
        ))
        result = _single_result(resp)
        assert result["message_id"] == 21

        call_kw = mock_create.call_args.kwargs
        assert call_kw["line"] == "berater_accepted"
        assert call_kw["template_values"] == json.dumps(["Мария Шмидт"])

    @patch("server.app.get_kommo_client")
    @patch("server.app.get_recent_message", return_value=None)
    def test_berater_accepted_without_name_returns_error(
        self, mock_recent, mock_get_kommo, client,
    ):
        kommo = MagicMock()
        kommo.get_lead_contact.return_value = (
            _make_lead(pipeline_id=12154099, status_id=93860331), _make_contact()
        )
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_name.return_value = None
        kommo.extract_termin_date.return_value = None
        mock_get_kommo.return_value = kommo

        resp = client.post("/webhook/kommo", json=_payload(
            status_id=93860331, pipeline_id=12154099,
        ))
        result = _single_result(resp)
        assert result["status"] == "error"
        assert "Name not found" in result["message"]
