"""End-to-end integration tests for T11.

These tests verify the full webhook → Kommo API → messenger → DB flow
using mocked external services (Kommo, Wazzup24, Telegram).

They exercise the real FastAPI app, SQLite database, and all internal logic
WITHOUT calling external APIs.

Run: docker run --rm --user root -v $(pwd)/tests:/app/tests \
     whatsapp-notifications sh -c \
     "pip install -q pytest freezegun httpx && python -m pytest tests/test_integration_e2e.py -v"
"""

import os
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
os.environ.setdefault("WAZZUP_TEMPLATE_ID", "test-template")
os.environ.setdefault("KOMMO_WEBHOOK_SECRET", "")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_ALERT_CHAT_ID", "")

from fastapi.testclient import TestClient
from freezegun import freeze_time

# Must import after env setup
from server.app import app
from server.db import _get_conn, get_message_by_id, get_messages, init_db

BERLIN_TZ = ZoneInfo("Europe/Berlin")

# Real Kommo pipeline/status IDs
BERATAR_PIPELINE = 12154099
FIRST_LINE_STATUS = 9386032      # "Принято от первой линии"
SECOND_LINE_STATUS = 10093587    # "Термин ДЦ"
GOSNIKI_PIPELINE = 10631243
GOSNIKI_FIRST_STATUS = 8152349


def _make_contact(contact_id=100, phone="+491761234567"):
    """Build a Kommo contact dict with phone."""
    return {
        "id": contact_id,
        "custom_fields_values": [
            {
                "field_code": "PHONE",
                "values": [{"value": phone}],
            },
        ],
    }


def _make_lead(lead_id=1, pipeline_id=BERATAR_PIPELINE, status_id=FIRST_LINE_STATUS,
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


def _make_webhook_payload(lead_id=1, status_id=FIRST_LINE_STATUS,
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


class TestScenario1FirstLine:
    """Scenario 1: First line — запись на термин.

    Lead moves to "Принято от первой линии" → WhatsApp sent,
    Kommo note added, DB record with status=sent, line=first, attempts=1.
    """

    @freeze_time("2026-02-25 10:00:00", tz_offset=0)  # inside window
    def test_full_flow_first_line(self, client, _temp_db):
        lead = _make_lead(lead_id=42, contact_id=200, pipeline_id=BERATAR_PIPELINE,
                          status_id=FIRST_LINE_STATUS)
        contact = _make_contact(contact_id=200, phone="+996501354144")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+996501354144"
            kommo.extract_termin_date.side_effect = lambda ld, fid: "26.02.2026" if fid == 885996 else None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = (
                "Здравствуйте. Это SternMeister. Напоминаем о записи на термин в 26.02.2026. "
                "Скажите, все в силе?"
            )
            messenger.send_message.return_value = {
                "message_id": "wazzup-msg-001",
                "status": "sent",
                "message_text": "...",
            }
            mock_msgr_cls.return_value = messenger

            payload = _make_webhook_payload(lead_id=42, status_id=FIRST_LINE_STATUS,
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
        assert msg["line"] == "first"
        assert msg["attempts"] == 1
        assert msg["phone"] == "+996501354144"
        assert msg["kommo_lead_id"] == 42
        assert msg["sent_at"] is not None
        assert msg["next_retry_at"] is not None
        assert msg["messenger_id"] == "wazzup-msg-001"

        # Verify Kommo note was called
        kommo.add_note.assert_called_once()
        note_text = kommo.add_note.call_args[0][1]
        assert "WhatsApp сообщение отправлено" in note_text
        assert "first" in note_text


class TestScenario2SecondLine:
    """Scenario 2: Second line — напоминание о термине ДЦ."""

    @freeze_time("2026-02-25 12:00:00", tz_offset=0)
    def test_full_flow_second_line(self, client, _temp_db):
        lead = _make_lead(lead_id=55, contact_id=300, pipeline_id=BERATAR_PIPELINE,
                          status_id=SECOND_LINE_STATUS)
        contact = _make_contact(contact_id=300, phone="+79167310500")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+79167310500"
            kommo.extract_termin_date.side_effect = lambda ld, fid: "27.02.2026" if fid == 885996 else None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = (
                "Здравствуйте. Это SternMeister. Напоминаем о термине в 27.02.2026. "
                "Скажите, все в силе?"
            )
            messenger.send_message.return_value = {
                "message_id": "wazzup-msg-002",
                "status": "sent",
                "message_text": "...",
            }
            mock_msgr_cls.return_value = messenger

            payload = _make_webhook_payload(lead_id=55, status_id=SECOND_LINE_STATUS,
                                            pipeline_id=BERATAR_PIPELINE)
            resp = client.post("/webhook/kommo", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        result = data["results"][0]
        msg_id = result["message_id"]

        msg = get_message_by_id(msg_id)
        assert msg["status"] == "sent"
        assert msg["line"] == "second"
        assert msg["phone"] == "+79167310500"


class TestScenario3PendingOutsideWindow:
    """Scenario 3: Outside send window → pending."""

    @freeze_time("2026-02-25 22:00:00", tz_offset=0)  # 23:00 Berlin (CET+1)
    def test_outside_window_creates_pending(self, client, _temp_db):
        lead = _make_lead(lead_id=70)
        contact = _make_contact(phone="+491761234567")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+491761234567"
            kommo.extract_termin_date.side_effect = lambda ld, fid: "26.02.2026" if fid == 885996 else None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = "test message"
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
            line="first",
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
            line="first",
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
        contact = _make_contact(phone="+123")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter") as mock_alerter_cls:

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+123"
            kommo.extract_termin_date.side_effect = lambda ld, fid: "26.02.2026" if fid == 885996 else None
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
    """Scenario 8: Verify all S01 DoD criteria are met."""

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
        """DoD: Отправка только в окне 9:00-21:00."""
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
        """DoD: next_retry_at = tomorrow 9:00 Berlin."""
        from server.utils import get_next_send_window_start
        next_start = get_next_send_window_start()
        # 9:00 Berlin CET = 08:00 UTC
        assert "2026-02-26T08:00:00" in next_start

    def test_dedup_window(self, client, _temp_db):
        """DoD: Deduplication within 10 minutes."""
        from server.db import create_message
        # Create a recent message for lead 42
        create_message(
            kommo_lead_id=42,
            kommo_contact_id=100,
            phone="+491761234567",
            line="first",
            termin_date="26.02.2026",
            message_text="test",
            status="sent",
        )

        with patch("server.app.get_kommo_client"), \
             patch("server.app.get_messenger"), \
             patch("server.app.get_alerter"):
            payload = _make_webhook_payload(lead_id=42, status_id=FIRST_LINE_STATUS)
            resp = client.post("/webhook/kommo", json=payload)

        result = resp.json()["results"][0]
        assert "Duplicate" in result["message"]

    def test_waba_template_text(self):
        """DoD: Wazzup24 uses WABA template 'Напоминание о записи или встрече'."""
        from server.messenger.wazzup import WazzupMessenger, MessageData
        with patch("server.config.WAZZUP_API_KEY", "test"), \
             patch("server.config.WAZZUP_CHANNEL_ID", "ch"), \
             patch("server.config.WAZZUP_TEMPLATE_ID", "tpl"), \
             patch("server.config.WAZZUP_API_URL", "http://test"):
            m = WazzupMessenger()
            text_first = m.build_message_text(MessageData(line="first", termin_date="25.02.2026"))
            assert "SternMeister" in text_first
            assert "записи на термин" in text_first
            assert "25.02.2026" in text_first
            assert "все в силе" in text_first

            text_second = m.build_message_text(MessageData(line="second", termin_date="26.02.2026"))
            assert "термине" in text_second
            assert "26.02.2026" in text_second

    def test_health_endpoint(self, client):
        """DoD: Health check works."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "send_window" in data
        assert "in_window" in data
        assert "server_time_utc" in data
        assert "server_time_berlin" in data

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
            "WAZZUP_API_KEY", "WAZZUP_API_URL", "WAZZUP_CHANNEL_ID", "WAZZUP_TEMPLATE_ID",
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
            line="first",
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
        contact = _make_contact(contact_id=500, phone="+491761234567")

        # Step 1: Initial send via webhook
        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+491761234567"
            kommo.extract_termin_date.side_effect = lambda ld, fid: "26.02.2026" if fid == 885996 else None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = "test"
            messenger.send_message.return_value = {"message_id": "msg-init", "status": "sent", "message_text": "test"}
            mock_msgr_cls.return_value = messenger

            resp = client.post("/webhook/kommo", json=_make_webhook_payload(lead_id=99))

        msg_id = resp.json()["results"][0]["message_id"]
        msg = get_message_by_id(msg_id)
        assert msg["attempts"] == 1
        assert msg["status"] == "sent"

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
    """Verify Госники pipeline works correctly."""

    @freeze_time("2026-02-25 10:00:00", tz_offset=0)
    def test_gosniki_first_line(self, client, _temp_db):
        lead = _make_lead(lead_id=101, contact_id=600, pipeline_id=GOSNIKI_PIPELINE,
                          status_id=GOSNIKI_FIRST_STATUS)
        contact = _make_contact(contact_id=600, phone="+491761234567")

        with patch("server.app.get_kommo_client") as mock_kommo_cls, \
             patch("server.app.get_messenger") as mock_msgr_cls, \
             patch("server.app.get_alerter"):

            kommo = MagicMock()
            kommo.get_lead_contact.return_value = (lead, contact)
            kommo.extract_phone.return_value = "+491761234567"
            kommo.extract_termin_date.side_effect = lambda ld, fid: "26.02.2026" if fid == 885996 else None
            mock_kommo_cls.return_value = kommo

            messenger = MagicMock()
            messenger.build_message_text.return_value = "test"
            messenger.send_message.return_value = {"message_id": "gos-001", "status": "sent", "message_text": "test"}
            mock_msgr_cls.return_value = messenger

            payload = _make_webhook_payload(lead_id=101, status_id=GOSNIKI_FIRST_STATUS,
                                            pipeline_id=GOSNIKI_PIPELINE)
            resp = client.post("/webhook/kommo", json=payload)

        assert resp.status_code == 200
        result = resp.json()["results"][0]
        msg_id = result["message_id"]
        msg = get_message_by_id(msg_id)
        assert msg["line"] == "first"
        assert msg["status"] == "sent"
