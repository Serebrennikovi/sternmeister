"""Tests for cron.py S02: template_values restoration in process_retries/process_pending (T12)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from server.db import create_message, init_db, get_message_by_id, _get_conn
from server.messenger import MessengerError, MessageData
from server.template_helpers import B2_CHECKLIST_TEXT, CUSTOMER_FACING_BERATER


@pytest.fixture(autouse=True)
def _clean_db():
    """Init DB and delete all rows before each test for isolation."""
    init_db()
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM messages")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _mock_kommo():
    with patch("server.cron.get_kommo_client") as mock_gc:
        mock_gc.return_value = MagicMock()
        yield


def _past(hours: int = 25) -> str:
    return (
        datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds")


def _create_msg(line: str, termin_date: str, template_values: str | None = None,
                status: str = "sent", attempts: int = 1) -> int:
    return create_message(
        kommo_lead_id=100, kommo_contact_id=200, phone="+491234567890",
        line=line, termin_date=termin_date,
        message_text="Test",
        status=status, attempts=attempts,
        sent_at=_past(25),
        next_retry_at=_past(1),
        template_values=template_values,
    )


def _create_pending(line: str, termin_date: str,
                    template_values: str | None = None) -> int:
    return create_message(
        kommo_lead_id=100, kommo_contact_id=200, phone="+491234567890",
        line=line, termin_date=termin_date,
        message_text="Test",
        status="pending", attempts=0,
        next_retry_at=_past(1),
        template_values=template_values,
    )


# -----------------------------------------------------------------------
# process_retries()
# -----------------------------------------------------------------------

class TestProcessRetriesTemplateValues:

    @freeze_time("2026-03-01 10:00:00")
    def test_keyed_retry_for_berater_day_minus_7_forces_customer_facing_institution(self):
        """Keyed retry for Б2 ignores legacy institution values from pre-T17 rows."""
        tv = json.dumps({
            "name": "Анна",
            "institution": "Jobcenter",
            "date": "25.03.2026",
            "checklist_text": B2_CHECKLIST_TEXT,
        })
        _create_msg("berater_day_minus_7", "25.03.2026", template_values=tv)

        captured = {}

        def fake_send(phone, message_data):
            captured["message_data"] = message_data
            return {"message_id": "retry-msg-000"}

        from server.cron import process_retries
        with patch("server.cron.get_messenger") as mock_gm, \
             patch("server.cron.is_in_send_window", return_value=True):
            messenger = MagicMock()
            messenger.send_message.side_effect = fake_send
            mock_gm.return_value = messenger

            process_retries()

        md = captured["message_data"]
        assert md.name == "Анна"
        assert md.institution == CUSTOMER_FACING_BERATER
        assert md.date == "25.03.2026"
        assert md.checklist_text == B2_CHECKLIST_TEXT

    @freeze_time("2026-03-01 10:00:00")
    def test_restores_3_vars_for_berater_day_minus_3(self):
        """Retry normalizes old temporal institution names to customer-facing text."""
        tv = json.dumps(["Анна", "Jobcenter", "Среда", "25.03.2026"])
        _create_msg("berater_day_minus_3", "25.03.2026", template_values=tv)

        captured = {}

        def fake_send(phone, message_data):
            captured["message_data"] = message_data
            return {"message_id": "retry-msg-001"}

        from server.cron import process_retries
        with patch("server.cron.get_messenger") as mock_gm, \
             patch("server.cron.is_in_send_window", return_value=True):
            messenger = MagicMock()
            messenger.send_message.side_effect = fake_send
            mock_gm.return_value = messenger

            process_retries()

        md = captured["message_data"]
        assert md.name == "Анна"
        assert md.institution == CUSTOMER_FACING_BERATER
        assert md.weekday == "Среда"
        assert md.date == "25.03.2026"
        assert md.schedule_text == "Среда, 25.03.2026"

    @freeze_time("2026-03-01 10:00:00")
    def test_keyed_retry_for_berater_day_minus_3_forces_customer_facing_institution(self):
        """Keyed retry for Б3 ignores legacy institution values from pre-T17 rows."""
        tv = json.dumps({
            "name": "Анна",
            "institution": "Jobcenter",
            "weekday": "Среда",
            "date": "25.03.2026",
        })
        _create_msg("berater_day_minus_3", "25.03.2026", template_values=tv)

        captured = {}

        def fake_send(phone, message_data):
            captured["message_data"] = message_data
            return {"message_id": "retry-msg-001-keyed"}

        from server.cron import process_retries
        with patch("server.cron.get_messenger") as mock_gm, \
             patch("server.cron.is_in_send_window", return_value=True):
            messenger = MagicMock()
            messenger.send_message.side_effect = fake_send
            mock_gm.return_value = messenger

            process_retries()

        md = captured["message_data"]
        assert md.name == "Анна"
        assert md.institution == CUSTOMER_FACING_BERATER
        assert md.weekday == "Среда"
        assert md.date == "25.03.2026"
        assert md.schedule_text == "Среда, 25.03.2026"

    @freeze_time("2026-03-01 10:00:00")
    def test_restores_1_var_for_berater_accepted(self):
        """Legacy retry for berater_accepted restores name-only payload."""
        tv = json.dumps(["Анна"])
        _create_msg("berater_accepted", "", template_values=tv)

        captured = {}

        def fake_send(phone, message_data):
            captured["message_data"] = message_data
            return {"message_id": "retry-msg-002"}

        from server.cron import process_retries
        with patch("server.cron.get_messenger") as mock_gm, \
             patch("server.cron.is_in_send_window", return_value=True):
            messenger = MagicMock()
            messenger.send_message.side_effect = fake_send
            mock_gm.return_value = messenger

            process_retries()

        md = captured["message_data"]
        assert md.name == "Анна"
        assert md.institution is None
        assert md.time is None
        assert md.topic is None
        assert md.datetime_text is None
        assert md.location_text is None

    @freeze_time("2026-03-01 10:00:00")
    def test_restores_keyed_vars_for_berater_accepted(self):
        """Keyed retry for berater_accepted normalizes to name-only payload."""
        tv = json.dumps({
            "name": "Мария",
            "institution": "Jobcenter",
            "date": "01.04.2026",
            "time": "10:30",
            "topic": "термин в Jobcenter",
            "datetime_text": "01.04.2026 в 10:30",
            "location_text": "в Jobcenter",
        })
        _create_msg("berater_accepted", "01.04.2026", template_values=tv)

        captured = {}

        def fake_send(phone, message_data):
            captured["message_data"] = message_data
            return {"message_id": "retry-msg-002-keyed"}

        from server.cron import process_retries
        with patch("server.cron.get_messenger") as mock_gm, \
             patch("server.cron.is_in_send_window", return_value=True):
            messenger = MagicMock()
            messenger.send_message.side_effect = fake_send
            mock_gm.return_value = messenger

            process_retries()

        md = captured["message_data"]
        assert md.termin_date == "01.04.2026"
        assert md.name == "Мария"
        assert md.institution is None
        assert md.date is None
        assert md.time is None
        assert md.topic is None
        assert md.datetime_text is None
        assert md.location_text is None



# -----------------------------------------------------------------------
# process_pending()
# -----------------------------------------------------------------------

class TestProcessPendingTemplateValues:

    @freeze_time("2026-03-01 10:00:00")
    def test_pending_restores_template_values(self):
        """process_pending: pending msg with template_values → MessageData.name set on send."""
        tv = json.dumps(["Мария"])
        _create_pending("gosniki_consultation_done", "", template_values=tv)

        captured = {}

        def fake_send(phone, message_data):
            captured["message_data"] = message_data
            return {"message_id": "pending-msg-001"}

        from server.cron import process_pending
        with patch("server.cron.get_messenger") as mock_gm, \
             patch("server.cron.is_in_send_window", return_value=True):
            messenger = MagicMock()
            messenger.send_message.side_effect = fake_send
            mock_gm.return_value = messenger

            process_pending()

        md = captured["message_data"]
        assert md.name == "Мария"
        assert md.line == "gosniki_consultation_done"
        assert md.termin_date == ""
        assert "Мария" in md.news_text


# -----------------------------------------------------------------------
# M2 fix coverage: skipped result does not cause KeyError (L8)
# -----------------------------------------------------------------------

class TestProcessRetriesSkipped:
    """Verify M2 fix: send_message returning 'skipped' does not crash cron (L8)."""

    @freeze_time("2026-03-01 10:00:00")
    def test_skipped_result_does_not_raise(self):
        """berater_day_minus_7 retry → skipped → no KeyError, counts as neither ok nor fail."""
        _create_msg("berater_day_minus_7", "25.03.2026")

        from server.cron import process_retries
        with patch("server.cron.get_messenger") as mock_gm, \
             patch("server.cron.is_in_send_window", return_value=True), \
             patch("server.cron.update_message") as mock_update:
            messenger = MagicMock()
            messenger.send_message.return_value = {"status": "skipped"}
            mock_gm.return_value = messenger

            ok, fail = process_retries()

        assert ok == 0
        assert fail == 0
        # DB must NOT be updated with messenger_id (no actual send happened)
        mock_update.assert_not_called()

    @freeze_time("2026-03-01 10:00:00")
    def test_pending_skipped_result_does_not_raise(self):
        """berater_day_minus_7 pending → skipped → no KeyError, counts as neither ok nor fail."""
        _create_pending("berater_day_minus_7", "25.03.2026")

        from server.cron import process_pending
        with patch("server.cron.get_messenger") as mock_gm, \
             patch("server.cron.is_in_send_window", return_value=True), \
             patch("server.cron.update_message") as mock_update:
            messenger = MagicMock()
            messenger.send_message.return_value = {"status": "skipped"}
            mock_gm.return_value = messenger

            ok, fail = process_pending()

        assert ok == 0
        assert fail == 0
        mock_update.assert_not_called()
