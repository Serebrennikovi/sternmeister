"""End-to-end integration tests for T11/T12.

These tests verify the full webhook → Kommo API → messenger → DB flow
using mocked external services (Kommo, Wazzup24, Telegram).

They exercise the real FastAPI app, SQLite database, and all internal logic
WITHOUT calling external APIs.

Run: docker run --rm --user root -v $(pwd)/tests:/app/tests \
     whatsapp-notifications sh -c \
     "pip install -q pytest freezegun httpx && python -m pytest tests/test_integration_e2e.py -v"
"""

import os
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

# Set env vars BEFORE importing app modules
os.environ.setdefault("KOMMO_DOMAIN", "test.kommo.com")
os.environ.setdefault("KOMMO_TOKEN", "test-token")
os.environ.setdefault("WAZZUP_API_KEY", "test-wazzup-key")
os.environ.setdefault("WAZZUP_CHANNEL_ID", "test-channel")
os.environ.setdefault("KOMMO_WEBHOOK_SECRET", "")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_ALERT_CHAT_ID", "")

from fastapi.testclient import TestClient
from freezegun import freeze_time

# Must import after env setup
from server.app import app
from server.db import _get_conn, get_message_by_id, get_messages, init_db

BERLIN_TZ = ZoneInfo("Europe/Berlin")

# S02 Kommo pipeline/status IDs
BERATAR_PIPELINE = 12154099
BERATER_ACCEPTED_STATUS = 93860331   # "Принято от первой линии" → berater_accepted (S02)
GOSNIKI_PIPELINE = 10935879
GOSNIKI_STATUS = 95514983            # "Консультация проведена" → gosniki_consultation_done


def _make_contact(contact_id=100, phone="+491761234567", name="Test User"):
    """Build a Kommo contact dict with phone."""
    return {
        "id": contact_id,
        "name": name,
        "custom_fields_values": [
            {
                "field_code": "PHONE",
                "values": [{"value": phone}],
            },
        ],
    }


def _make_lead(lead_id=1, pipeline_id=BERATAR_PIPELINE, status_id=BERATER_ACCEPTED_STATUS,
               contact_id=100, termin_timestamp=None):
    """Build a Kommo lead dict with termin date field."""
    if termin_timestamp is None:
        # Tomorrow 14:00 Berlin
        tomorrow = datetime.now(tz=BERLIN_TZ).replace(
            hour=14, minute=0, second=0, microsecond=0,
        ) + timedelta(days=1)
        termin_timestamp = int(tomorrow.timestamp())

    return {
        "id": lead_id,
        "pipeline_id": pipeline_id,
        "status_id": status_id,
        "custom_fields_values": [
            {
                "field_id": 885996,  # date_termin
                "values": [{"value": termin_timestamp}],
            },
        ],
        "_embedded": {
            "contacts": [{"id": contact_id, "is_main": True}],
        },
    }


def _make_webhook_payload(lead_id=1, status_id=BERATER_ACCEPTED_STATUS,
                          pipeline_id=BERATAR_PIPELINE):
    """Build a minimal webhook JSON payload."""
    return {
        "leads": {
            "status": [{
                "id": lead_id,
                "status_id": status_id,
                "pipeline_id": pipeline_id,
            }],
        },
    }


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use a fresh temp SQLite DB for each test."""
    db_path = str(tmp_path / "test.db")
    with patch("server.config.DATABASE_PATH", db_path), \
         patch("server.db.DATABASE_PATH", db_path):
        init_db()
        yield db_path


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset lazy singletons between tests."""
    from server.kommo import _reset_client
    from server.messenger.wazzup import _reset_messenger
    from server.alerts import _reset_alerter
    _reset_client()
    _reset_messenger()
    _reset_alerter()
    yield
    _reset_client()
    _reset_messenger()
    _reset_alerter()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestScenario1BeraterAccepted:
    """Scenario 1: Berater accepted — консультация принята от 1й линии.

    Lead moves to status 93860331 → "berater_accepted" → WhatsApp sent with name,
    Kommo note added, DB record with status=sent, line=berater_accepted, attempts=1.
    """

    @freeze_time("2026-02-25 10:00:00", tz_offset=0)  # inside window
    def test_full_flow_berater_accepted(self, client, _temp_db):
        lead = _make_lead(lead_id=42, contact_id=200, pipeline_id=BERATAR_PIPELINE,
                          status_id=BERATER_ACCEPTED_STATUS)
        contact = _make_contact(contact_id=200, phone="+996501354144", name="Анна Петрова")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+996501354144"
            kommo.extract_name.return_value = "Анна Петрова"
            kommo.extract_termin_date.return_value = None  # optional for berater_accepted
            kommo.extract_termin_date_dc.return_value = None
            kommo.extract_termin_date_aa.return_value = None
            kommo.extract_time_termin.return_value = None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = "[template] Анна Петрова"
            messenger.send_message.return_value = {
                "message_id": "wazzup-msg-001",
                "status": "sent",
                "message_text": "[template] Анна Петрова",
            }
            mock_msgr_cls.return_value = messenger

            payload = _make_webhook_payload(lead_id=42, status_id=BERATER_ACCEPTED_STATUS,
                                            pipeline_id=BERATAR_PIPELINE)
            resp = client.post("/webhook/kommo", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        result = data["results"][0]
        assert "message_id" in result
        msg_id = result["message_id"]

        # Verify DB record
        msg = get_message_by_id(msg_id)
        assert msg is not None
        assert msg["status"] == "sent"
        assert msg["line"] == "berater_accepted"
        assert msg["attempts"] == 1
        assert msg["phone"] == "+996501354144"
        assert msg["kommo_lead_id"] == 42
        assert msg["sent_at"] is not None
        assert msg["next_retry_at"] is not None
        assert msg["messenger_id"] == "wazzup-msg-001"
        tv = json.loads(msg["template_values"])
        assert tv == {"name": "Анна Петрова"}

        # Verify Kommo note was called
        kommo.add_note.assert_called_once()
        note_text = kommo.add_note.call_args[0][1]
        assert "WhatsApp сообщение отправлено" in note_text
        assert "berater_accepted" in note_text


class TestScenario2GosnikisConsultation:
    """Scenario 2: Gosniki consultation done — консультация проведена Госники."""

    @freeze_time("2026-02-25 12:00:00", tz_offset=0)
    def test_full_flow_gosniki_consultation_done(self, client, _temp_db):
        lead = _make_lead(lead_id=55, contact_id=300, pipeline_id=GOSNIKI_PIPELINE,
                          status_id=GOSNIKI_STATUS)
        contact = _make_contact(contact_id=300, phone="+79167310500", name="Мария Сидорова")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+79167310500"
            kommo.extract_name.return_value = "Мария Сидорова"
            kommo.extract_termin_date.return_value = None  # optional
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = "[template] Мария Сидорова"
            messenger.send_message.return_value = {
                "message_id": "wazzup-msg-002",
                "status": "sent",
                "message_text": "[template] Мария Сидорова",
            }
            mock_msgr_cls.return_value = messenger

            payload = _make_webhook_payload(lead_id=55, status_id=GOSNIKI_STATUS,
                                            pipeline_id=GOSNIKI_PIPELINE)
            resp = client.post("/webhook/kommo", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        msg_id = result["message_id"]

        msg = get_message_by_id(msg_id)
        assert msg["status"] == "sent"
        assert msg["line"] == "gosniki_consultation_done"
        assert msg["phone"] == "+79167310500"
        tv = json.loads(msg["template_values"])
        assert tv["name"] == "Мария Сидорова"
        assert "Мария Сидорова" in tv["news_text"]


class TestScenario3PendingOutsideWindow:
    """Scenario 3: Outside send window → pending."""

    @freeze_time("2026-02-25 22:00:00", tz_offset=0)  # 23:00 Berlin (CET+1)
    def test_outside_window_creates_pending(self, client, _temp_db):
        lead = _make_lead(lead_id=70)
        contact = _make_contact(phone="+491761234567", name="Иван Иванов")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+491761234567"
            kommo.extract_name.return_value = "Иван Иванов"
            kommo.extract_termin_date.return_value = None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = "[template] Иван Иванов"
            mock_msgr_cls.return_value = messenger

            payload = _make_webhook_payload(lead_id=70)
            resp = client.post("/webhook/kommo", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        assert result["message"] == "Scheduled for next send window"
        msg_id = result["message_id"]

        msg = get_message_by_id(msg_id)
        assert msg["status"] == "pending"
        assert msg["attempts"] == 0
        assert msg["sent_at"] is None
        assert msg["next_retry_at"] is not None
        tv = json.loads(msg["template_values"])
        assert tv == {"name": "Иван Иванов"}

        # Verify messenger.send_message was NOT called
        messenger.send_message.assert_not_called()


class TestScenario4CronRetry:
    """Scenario 4: Retry via cron — sent messages re-sent after 24h."""

    @freeze_time("2026-02-26 10:00:00", tz_offset=0)  # inside window
    def test_retry_cycle(self, _temp_db):
        from server.cron import process_retries
        from server.db import create_message

        # Create a sent message with next_retry_at in the past
        past = (datetime(2026, 2, 25, 10, 0, 0, tzinfo=timezone.utc)).isoformat(timespec="seconds")
        retry_at = (datetime(2026, 2, 26, 9, 0, 0, tzinfo=timezone.utc)).isoformat(timespec="seconds")
        msg_id = create_message(
            kommo_lead_id=42,
            kommo_contact_id=200,
            phone="+996501354144",
            line="gosniki_consultation_done",
            termin_date="26.02.2026",
            message_text="Здравствуйте...",
            status="sent",
            attempts=1,
            sent_at=past,
            next_retry_at=retry_at,
        )

        with patch("server.cron.get_messenger") as mock_msgr_cls, \
             patch("server.cron.get_kommo_client") as mock_kommo_cls, \
             patch("server.cron.get_alerter"):

            messenger = MagicMock()
            messenger.send_message.return_value = {
                "message_id": "wazzup-retry-001",
                "status": "sent",
            }
            mock_msgr_cls.return_value = messenger

            kommo = MagicMock()
            mock_kommo_cls.return_value = kommo

            ok, fail = process_retries()

        assert ok == 1
        assert fail == 0

        msg = get_message_by_id(msg_id)
        assert msg["attempts"] == 2
        assert msg["status"] == "sent"
        assert msg["messenger_id"] == "wazzup-retry-001"

        # Kommo note should mention retry
        kommo.add_note.assert_called_once()
        note_text = kommo.add_note.call_args[0][1]
        assert "повтор" in note_text

    @freeze_time("2026-02-27 10:00:00", tz_offset=0)
    def test_max_attempts_not_retried(self, _temp_db):
        """Messages at max attempts (3) are not picked for retry."""
        from server.cron import process_retries
        from server.db import create_message

        past = (datetime(2026, 2, 26, 10, 0, 0, tzinfo=timezone.utc)).isoformat(timespec="seconds")
        retry_at = (datetime(2026, 2, 27, 9, 0, 0, tzinfo=timezone.utc)).isoformat(timespec="seconds")
        create_message(
            kommo_lead_id=42,
            kommo_contact_id=200,
            phone="+996501354144",
            line="gosniki_consultation_done",
            termin_date="26.02.2026",
            message_text="Здравствуйте...",
            status="sent",
            attempts=3,  # MAX — should not be retried
            sent_at=past,
            next_retry_at=retry_at,
        )

        with patch("server.cron.get_messenger") as mock_msgr_cls, \
             patch("server.cron.get_kommo_client"), \
             patch("server.cron.get_alerter"):

            messenger = MagicMock()
            mock_msgr_cls.return_value = messenger

            ok, fail = process_retries()

        assert ok == 0
        assert fail == 0
        messenger.send_message.assert_not_called()


class TestScenario5MessengerError:
    """Scenario 5: Invalid phone → messenger error → failed status + alert."""

    @freeze_time("2026-02-25 10:00:00", tz_offset=0)
    def test_messenger_error_saves_failed(self, client, _temp_db):
        from server.messenger.wazzup import MessengerError

        lead = _make_lead(lead_id=80)
        contact = _make_contact(phone="+123", name="Test User")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter") as mock_alerter_cls:

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+123"
            kommo.extract_name.return_value = "Test User"
            kommo.extract_termin_date.return_value = None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = "test"
            messenger.send_message.side_effect = MessengerError("Invalid request: bad phone")
            mock_msgr_cls.return_value = messenger

            alerter = MagicMock()
            mock_alerter_cls.return_value = alerter

            payload = _make_webhook_payload(lead_id=80)
            resp = client.post("/webhook/kommo", json=payload)

        assert resp.status_code == 200
        result = resp.json()["results"][0]
        assert "Messenger error" in result["message"]

        # Check DB
        msgs = get_messages(kommo_lead_id=80)
        assert len(msgs) == 1
        assert msgs[0]["status"] == "failed"
        assert msgs[0]["next_retry_at"] is not None

        # Alert was sent
        alerter.alert_messenger_error.assert_called_once()


class TestScenario6KommoAPIError:
    """Scenario 6: Kommo API error → alert + error response."""

    @freeze_time("2026-02-25 10:00:00", tz_offset=0)
    def test_kommo_api_error_triggers_alert(self, client):
        from server.kommo import KommoAPIError

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_alerter") as mock_alerter_cls:

            kommo = MagicMock()
            kommo.get_lead_contact.side_effect = KommoAPIError("Not found: GET /leads/99999999", 404)
            mock_kommo_cls.return_value = kommo

            alerter = MagicMock()
            mock_alerter_cls.return_value = alerter

            payload = _make_webhook_payload(lead_id=99999999)
            resp = client.post("/webhook/kommo", json=payload)

        assert resp.status_code == 200
        result = resp.json()["results"][0]
        assert "Kommo API error" in result["message"]

        alerter.alert_kommo_error.assert_called_once_with(99999999, "Not found: GET /leads/99999999")


class TestScenario8DoD:
    """Scenario 8: Verify all S01/S02 DoD criteria are met."""

    def test_webhook_accepts_json_and_form(self, client):
        """DoD: Webhook от Kommo принимается корректно."""
        # JSON
        resp = client.post("/webhook/kommo", json={"leads": {"status": []}})
        assert resp.status_code == 200

        # Form-encoded (Kommo real format)
        resp = client.post(
            "/webhook/kommo",
            content=b"leads[status][0][id]=1&leads[status][0][status_id]=9999&leads[status][0][pipeline_id]=9999",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200

    @freeze_time("2026-02-25 10:00:00", tz_offset=0)
    def test_send_window_enforcement(self):
        """DoD: Отправка только в окне 8:00-22:00."""
        from server.utils import is_in_send_window
        # 10:00 UTC = 11:00 CET → inside window
        assert is_in_send_window() is True

    @freeze_time("2026-02-25 22:00:00", tz_offset=0)
    def test_outside_window(self):
        """DoD: Вне окна → False."""
        from server.utils import is_in_send_window
        # 22:00 UTC = 23:00 CET → outside window
        assert is_in_send_window() is False

    @freeze_time("2026-02-25 22:00:00", tz_offset=0)
    def test_next_window_calculation(self):
        """DoD: next_retry_at = tomorrow 8:00 Berlin."""
        from server.utils import get_next_send_window_start
        next_start = get_next_send_window_start()
        # 8:00 Berlin CET = 07:00 UTC
        assert "2026-02-26T07:00:00" in next_start

    def test_dedup_window(self, client, _temp_db):
        """DoD: Deduplication within 10 minutes."""
        from server.db import create_message
        # Create a recent berater_accepted message for lead 42
        create_message(
            kommo_lead_id=42,
            kommo_contact_id=100,
            phone="+491761234567",
            line="berater_accepted",
            termin_date="",
            message_text="test",
            status="sent",
        )

        with patch("server.app.get_kommo_client"), \
             patch("server.app.get_messenger"), \
             patch("server.app.get_alerter"):
            payload = _make_webhook_payload(lead_id=42, status_id=BERATER_ACCEPTED_STATUS)
            resp = client.post("/webhook/kommo", json=payload)

        result = resp.json()["results"][0]
        assert "Duplicate" in result["message"]

    def test_waba_template_text(self):
        """DoD: Wazzup24 uses WABA template variables correctly."""
        from server.messenger.wazzup import WazzupMessenger, MessageData
        with patch("server.config.WAZZUP_API_KEY", "test"), \
             patch("server.config.WAZZUP_CHANNEL_ID", "ch"), \
             patch("server.config.WAZZUP_API_URL", "http://test"):
            m = WazzupMessenger()

            # S02 berater_accepted — uses utility composite variables
            text_berater = m.build_message_text(
                MessageData(
                    line="berater_accepted",
                    termin_date="",
                    name="Анна Петрова",
                )
            )
            assert "Анна Петрова" in text_berater

    def test_health_endpoint(self, client):
        """DoD: Health check works and includes failed_temporal."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "send_window" in data
        assert "in_window" in data
        assert "server_time_utc" in data
        assert "server_time_berlin" in data
        assert "failed_temporal" in data

    def test_env_example_has_all_vars(self):
        """DoD: .env.example contains all required env vars."""
        import pathlib
        # Try repo root (host) or /app (Docker)
        candidates = [
            pathlib.Path(__file__).parent.parent / ".env.example",
            pathlib.Path("/app/.env.example"),
        ]
        env_example = None
        for p in candidates:
            if p.exists():
                env_example = p
                break
        if env_example is None:
            pytest.skip(".env.example not available in Docker (not copied into image)")

        content = env_example.read_text()
        required_vars = [
            "KOMMO_DOMAIN", "KOMMO_TOKEN",
            "WAZZUP_API_KEY", "WAZZUP_API_URL", "WAZZUP_CHANNEL_ID",
            "KOMMO_WEBHOOK_SECRET",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALERT_CHAT_ID",
            "SEND_WINDOW_START", "SEND_WINDOW_END",
            "MAX_RETRY_ATTEMPTS", "RETRY_INTERVAL_HOURS", "DEDUP_WINDOW_MINUTES",
            "DATABASE_PATH",
        ]
        for var in required_vars:
            assert var in content, f"{var} missing from .env.example"

    def test_webhook_secret_validation(self, client):
        """DoD: Webhook secret validation works."""
        with patch("server.app.KOMMO_WEBHOOK_SECRET", "my-secret"):
            # No secret → 403
            resp = client.post("/webhook/kommo", json={"leads": {"status": []}})
            assert resp.status_code == 403

            # Wrong secret → 403
            resp = client.post("/webhook/kommo?secret=wrong", json={"leads": {"status": []}})
            assert resp.status_code == 403

            # Correct secret → 200
            resp = client.post("/webhook/kommo?secret=my-secret", json={"leads": {"status": []}})
            assert resp.status_code == 200


class TestScenarioCronPending:
    """Cron processes pending messages in send window."""

    @freeze_time("2026-02-26 10:00:00", tz_offset=0)
    def test_pending_sent_by_cron(self, _temp_db):
        from server.cron import process_pending
        from server.db import create_message

        retry_at = (datetime(2026, 2, 26, 8, 0, 0, tzinfo=timezone.utc)).isoformat(timespec="seconds")
        msg_id = create_message(
            kommo_lead_id=70,
            kommo_contact_id=300,
            phone="+491761234567",
            line="gosniki_consultation_done",
            termin_date="26.02.2026",
            message_text="Здравствуйте...",
            status="pending",
            attempts=0,
            next_retry_at=retry_at,
        )

        with patch("server.cron.get_messenger") as mock_msgr_cls, \
             patch("server.cron.get_kommo_client") as mock_kommo_cls, \
             patch("server.cron.get_alerter"):

            messenger = MagicMock()
            messenger.send_message.return_value = {
                "message_id": "wazzup-pending-001",
                "status": "sent",
            }
            mock_msgr_cls.return_value = messenger

            kommo = MagicMock()
            mock_kommo_cls.return_value = kommo

            ok, fail = process_pending()

        assert ok == 1
        assert fail == 0

        msg = get_message_by_id(msg_id)
        assert msg["status"] == "sent"
        assert msg["attempts"] == 1
        assert msg["sent_at"] is not None
        assert msg["messenger_id"] == "wazzup-pending-001"

        # Kommo note for pending
        kommo.add_note.assert_called_once()
        note_text = kommo.add_note.call_args[0][1]
        assert "отложенное" in note_text


class TestFullLifecycle:
    """Full lifecycle: webhook → sent → retry 1 → retry 2 → max."""

    @freeze_time("2026-02-25 10:00:00", tz_offset=0)
    def test_webhook_to_max_retries(self, client, _temp_db):
        """Test the complete message lifecycle from initial send to max retries."""
        from server.cron import process_retries
        from server.db import update_message

        lead = _make_lead(lead_id=99, contact_id=500)
        contact = _make_contact(contact_id=500, phone="+491761234567", name="Тест Тестов")

        # Step 1: Initial send via webhook
        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+491761234567"
            kommo.extract_name.return_value = "Тест Тестов"
            kommo.extract_termin_date.return_value = None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = "[template] Тест Тестов"
            messenger.send_message.return_value = {
                "message_id": "msg-init", "status": "sent", "message_text": "test",
            }
            mock_msgr_cls.return_value = messenger

            resp = client.post("/webhook/kommo", json=_make_webhook_payload(lead_id=99))

        msg_id = resp.json()["results"][0]["message_id"]
        msg = get_message_by_id(msg_id)
        assert msg["attempts"] == 1
        assert msg["status"] == "sent"
        assert msg["line"] == "berater_accepted"

        # Step 2: Simulate 24h passing → cron retry 1
        past_retry = (datetime(2026, 2, 25, 9, 0, 0, tzinfo=timezone.utc)).isoformat(timespec="seconds")
        update_message(msg_id, next_retry_at=past_retry)

        with patch("server.cron.get_messenger") as mock_msgr_cls, \
             patch("server.cron.get_kommo_client") as mock_kommo_cls, \
             patch("server.cron.get_alerter"):

            messenger = MagicMock()
            messenger.send_message.return_value = {"message_id": "msg-retry1", "status": "sent"}
            mock_msgr_cls.return_value = messenger

            kommo = MagicMock()
            mock_kommo_cls.return_value = kommo

            ok, fail = process_retries()

        assert ok == 1
        msg = get_message_by_id(msg_id)
        assert msg["attempts"] == 2
        assert msg["status"] == "sent"

        # Step 3: Simulate another 24h → cron retry 2
        past_retry2 = (datetime(2026, 2, 25, 8, 0, 0, tzinfo=timezone.utc)).isoformat(timespec="seconds")
        update_message(msg_id, next_retry_at=past_retry2)

        with patch("server.cron.get_messenger") as mock_msgr_cls, \
             patch("server.cron.get_kommo_client") as mock_kommo_cls, \
             patch("server.cron.get_alerter"):

            messenger = MagicMock()
            messenger.send_message.return_value = {"message_id": "msg-retry2", "status": "sent"}
            mock_msgr_cls.return_value = messenger

            kommo = MagicMock()
            mock_kommo_cls.return_value = kommo

            ok, fail = process_retries()

        assert ok == 1
        msg = get_message_by_id(msg_id)
        assert msg["attempts"] == 3
        assert msg["status"] == "sent"

        # Step 4: No more retries — attempts == 3 == MAX_RETRY_ATTEMPTS + 1
        past_retry3 = (datetime(2026, 2, 25, 7, 0, 0, tzinfo=timezone.utc)).isoformat(timespec="seconds")
        update_message(msg_id, next_retry_at=past_retry3)

        with patch("server.cron.get_messenger") as mock_msgr_cls, \
             patch("server.cron.get_kommo_client"), \
             patch("server.cron.get_alerter"):

            messenger = MagicMock()
            mock_msgr_cls.return_value = messenger

            ok, fail = process_retries()

        assert ok == 0
        assert fail == 0
        messenger.send_message.assert_not_called()
        msg = get_message_by_id(msg_id)
        assert msg["attempts"] == 3  # unchanged


class TestGosniki:
    """Verify Госники pipeline (S02) works correctly."""

    @freeze_time("2026-02-25 10:00:00", tz_offset=0)
    def test_gosniki_consultation_done(self, client, _temp_db):
        lead = _make_lead(lead_id=101, contact_id=600, pipeline_id=GOSNIKI_PIPELINE,
                          status_id=GOSNIKI_STATUS)
        contact = _make_contact(contact_id=600, phone="+491761234567", name="Карим Ахметов")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+491761234567"
            kommo.extract_name.return_value = "Карим Ахметов"
            kommo.extract_termin_date.return_value = None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = "[template] Карим Ахметов"
            messenger.send_message.return_value = {
                "message_id": "gos-001", "status": "sent", "message_text": "test",
            }
            mock_msgr_cls.return_value = messenger

            payload = _make_webhook_payload(lead_id=101, status_id=GOSNIKI_STATUS,
                                            pipeline_id=GOSNIKI_PIPELINE)
            resp = client.post("/webhook/kommo", json=payload)

        assert resp.status_code == 200
        result = resp.json()["results"][0]
        msg_id = result["message_id"]
        msg = get_message_by_id(msg_id)
        assert msg["line"] == "gosniki_consultation_done"
        assert msg["status"] == "sent"
        tv = json.loads(msg["template_values"])
        assert tv["name"] == "Карим Ахметов"
