"""Integration tests for T13 temporal triggers.

Uses freezegun + real SQLite (temp DB from conftest.py) + mocked Kommo/Wazzup.
"""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest
from freezegun import freeze_time
from zoneinfo import ZoneInfo

from server.db import init_db, get_messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_for_date(d: date) -> int:
    """Return Unix timestamp for midnight of date in Berlin timezone."""
    dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    return int(dt.timestamp())


def _make_lead(lead_id: int, status_id: int, dc_date: date | None, aa_date: date | None) -> dict:
    custom_fields = []
    if dc_date is not None:
        custom_fields.append({
            "field_id": 887026,
            "values": [{"value": _ts_for_date(dc_date)}],
        })
    if aa_date is not None:
        custom_fields.append({
            "field_id": 887028,
            "values": [{"value": _ts_for_date(aa_date)}],
        })
    return {
        "id": lead_id,
        "status_id": status_id,
        "pipeline_id": 12154099,
        "custom_fields_values": custom_fields,
        "_embedded": {"contacts": [{"id": 9000 + lead_id}]},
    }


def _make_contact(name: str = "Иван Иванов", phone: str = "+4917612345678") -> dict:
    return {
        "id": 999,
        "name": name,
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": phone}]},
        ],
    }


def _run_cron(kommo_mock, messenger_mock, alerter_mock=None):
    """Run process_temporal_triggers with mocked external deps."""
    with (
        patch("server.cron.get_kommo_client", return_value=kommo_mock),
        patch("server.cron.get_messenger", return_value=messenger_mock),
        patch("server.cron.get_alerter", return_value=alerter_mock or MagicMock()),
        patch("server.cron.PHONE_WHITELIST", None),
    ):
        from server.cron import process_temporal_triggers
        process_temporal_triggers()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def setup_db():
    """Ensure DB is initialized before each test."""
    init_db()


@pytest.fixture
def kommo():
    m = MagicMock()
    m.get_contact.return_value = _make_contact()
    m.extract_name.return_value = "Иван Иванов"
    m.extract_phone.return_value = "+4917612345678"
    # Default: both date extractors return None
    m.extract_termin_date_dc.return_value = None
    m.extract_termin_date_aa.return_value = None
    return m


@pytest.fixture
def messenger():
    m = MagicMock()
    m.send_message.return_value = {"message_id": "wz-test-001", "status": "sent"}
    m.build_message_text.return_value = "[template] Иван Иванов"
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@freeze_time("2026-03-04 10:00:00")  # 11:00 Berlin, inside send window
class TestTemporalIntegration:
    """today = 2026-03-04 (Berlin)."""

    def test_dc_3_days_sends_and_saves_to_db(self, kommo, messenger):
        """Lead with ДЦ 3 days away → message sent → DB record with status='sent'."""
        lead = _make_lead(101, 93860331, dc_date=date(2026, 3, 7), aa_date=None)
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = date(2026, 3, 7)

        _run_cron(kommo, messenger)

        messenger.send_message.assert_called_once()
        msgs = get_messages(kommo_lead_id=101)
        assert len(msgs) == 1
        assert msgs[0]["line"] == "berater_day_minus_3"
        assert msgs[0]["status"] == "sent"
        assert msgs[0]["termin_date"] == "07.03.2026"
        assert msgs[0]["template_values"] is not None

    def test_aa_1_day_sends_berater_day_minus_1(self, kommo, messenger):
        """Lead with АА 1 day away → berater_day_minus_1 sent."""
        lead = _make_lead(102, 93860331, dc_date=None, aa_date=date(2026, 3, 5))
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = None
        kommo.extract_termin_date_aa.return_value = date(2026, 3, 5)

        _run_cron(kommo, messenger)

        msgs = get_messages(kommo_lead_id=102)
        assert len(msgs) == 1
        assert msgs[0]["line"] == "berater_day_minus_1"
        assert msgs[0]["status"] == "sent"

    def test_dedup_two_cron_runs_one_message(self, kommo, messenger):
        """Two cron runs for same lead+line+date → only one DB record."""
        lead = _make_lead(103, 93860331, dc_date=date(2026, 3, 7), aa_date=None)
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = date(2026, 3, 7)

        # First run
        _run_cron(kommo, messenger)
        msgs_after_1 = get_messages(kommo_lead_id=103)
        assert len(msgs_after_1) == 1

        # Second run — dedup prevents second send
        _run_cron(kommo, messenger)
        msgs_after_2 = get_messages(kommo_lead_id=103)
        assert len(msgs_after_2) == 1  # Still only 1
        assert messenger.send_message.call_count == 1  # Called only once

    def test_stop_status_dc_cancelled_no_message(self, kommo, messenger):
        """Lead on STOP status 93860875 → no message sent or saved."""
        lead = _make_lead(104, 93860875, dc_date=date(2026, 3, 7), aa_date=None)
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = date(2026, 3, 7)

        _run_cron(kommo, messenger)

        messenger.send_message.assert_not_called()
        assert len(get_messages(kommo_lead_id=104)) == 0

    def test_stop_status_aa_cancelled_no_message(self, kommo, messenger):
        """Lead on STOP status 93860883 → no message sent."""
        lead = _make_lead(105, 93860883, dc_date=None, aa_date=date(2026, 3, 7))
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_aa.return_value = date(2026, 3, 7)

        _run_cron(kommo, messenger)

        messenger.send_message.assert_not_called()
        assert len(get_messages(kommo_lead_id=105)) == 0

    def test_b2_days_7_no_send_no_db_record(self, kommo, messenger):
        """Б2 placeholder: lead with ДЦ 7 days away → INFO log, no send, no DB record."""
        lead = _make_lead(106, 93860331, dc_date=date(2026, 3, 11), aa_date=None)
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = date(2026, 3, 11)
        kommo.extract_termin_date_aa.return_value = None

        _run_cron(kommo, messenger)

        messenger.send_message.assert_not_called()
        assert len(get_messages(kommo_lead_id=106)) == 0

    def test_pagination_two_pages_all_leads_processed(self, kommo, messenger):
        """get_active_leads returns leads from two pages (via mock) → all processed."""
        # Simulate 300 leads: first page 250 + second page 50
        leads_page1 = [_make_lead(200 + i, 93860331, dc_date=None, aa_date=None)
                       for i in range(250)]
        leads_page2 = [_make_lead(450 + i, 93860331,
                                  dc_date=date(2026, 3, 7), aa_date=None)
                       for i in range(50)]
        all_leads = leads_page1 + leads_page2

        kommo.get_active_leads.return_value = all_leads

        def dc_side_effect(lead):
            lead_id = lead.get("id")
            if lead_id >= 450:
                return date(2026, 3, 7)  # +3 days
            return None

        def aa_side_effect(lead):
            return None

        kommo.extract_termin_date_dc.side_effect = dc_side_effect
        kommo.extract_termin_date_aa.side_effect = aa_side_effect

        _run_cron(kommo, messenger)

        # Only 50 leads from page2 have a valid date trigger
        assert messenger.send_message.call_count == 50

    def test_get_active_leads_error_telegram_alert(self, kommo, messenger):
        """get_active_leads() raises KommoAPIError → no processing, Telegram alert sent."""
        from server.kommo import KommoAPIError
        kommo.get_active_leads.side_effect = KommoAPIError("API down", 500)
        alerter = MagicMock()

        _run_cron(kommo, messenger, alerter_mock=alerter)

        messenger.send_message.assert_not_called()
        alerter.alert_cron_error.assert_called_once()
        error_msg = alerter.alert_cron_error.call_args[0][0]
        assert "CRITICAL" in error_msg

    def test_messenger_error_saves_failed_status(self, kommo, messenger):
        """MessengerError → status='failed' saved in DB with template_values."""
        from server.messenger import MessengerError
        lead = _make_lead(107, 93860331, dc_date=date(2026, 3, 7), aa_date=None)
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = date(2026, 3, 7)
        messenger.send_message.side_effect = MessengerError("Wazzup down")

        alerter = MagicMock()
        _run_cron(kommo, messenger, alerter_mock=alerter)

        msgs = get_messages(kommo_lead_id=107)
        assert len(msgs) == 1
        assert msgs[0]["status"] == "failed"
        assert msgs[0]["attempts"] == 1
        assert msgs[0]["template_values"] is not None
        alerter.alert_messenger_error.assert_called_once()

    def test_messenger_error_other_leads_continue(self, kommo, messenger):
        """MessengerError on lead A → lead B still processed."""
        from server.messenger import MessengerError
        lead_a = _make_lead(108, 93860331, dc_date=date(2026, 3, 7), aa_date=None)
        lead_b = _make_lead(109, 93860331, dc_date=date(2026, 3, 7), aa_date=None)
        kommo.get_active_leads.return_value = [lead_a, lead_b]

        def dc_side_effect(lead):
            return date(2026, 3, 7)

        kommo.extract_termin_date_dc.side_effect = dc_side_effect
        kommo.extract_termin_date_aa.return_value = None
        messenger.send_message.side_effect = [
            MessengerError("fail"),
            {"message_id": "wz-ok", "status": "sent"},
        ]

        _run_cron(kommo, messenger)

        # Lead A: failed, Lead B: sent
        assert get_messages(kommo_lead_id=108)[0]["status"] == "failed"
        assert get_messages(kommo_lead_id=109)[0]["status"] == "sent"

    def test_different_termin_dates_create_separate_records(self, kommo, messenger):
        """Same lead with different termin_date → two separate messages."""
        lead = _make_lead(110, 93860331, dc_date=date(2026, 3, 7), aa_date=date(2026, 3, 5))
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = date(2026, 3, 7)  # +3 → minus_3
        kommo.extract_termin_date_aa.return_value = date(2026, 3, 5)  # +1 → minus_1

        _run_cron(kommo, messenger)

        msgs = get_messages(kommo_lead_id=110)
        assert len(msgs) == 2
        lines = {m["line"] for m in msgs}
        assert "berater_day_minus_3" in lines
        assert "berater_day_minus_1" in lines

    def test_template_values_restored_for_retry(self, kommo, messenger):
        """template_values JSON saved; _build_message_data restores them correctly."""
        from server.messenger import MessengerError
        lead = _make_lead(111, 93860331, dc_date=date(2026, 3, 7), aa_date=None)
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = date(2026, 3, 7)
        kommo.extract_name.return_value = "Анна"
        messenger.send_message.side_effect = MessengerError("first fail")

        _run_cron(kommo, messenger)

        msgs = get_messages(kommo_lead_id=111)
        assert len(msgs) == 1
        assert msgs[0]["status"] == "failed"
        tv = json.loads(msgs[0]["template_values"])
        # M2 fix: template_values stored as dict, not positional list
        assert isinstance(tv, dict)
        assert tv["name"] == "Анна"
        assert tv["institution"] == "Jobcenter"  # DC field → Jobcenter

        # Verify _build_message_data can restore it
        from server.cron import _build_message_data
        md = _build_message_data(msgs[0])
        assert md.name == "Анна"
        assert md.institution == "Jobcenter"
        assert md.weekday is not None

    def test_fail_then_retry_success_temporal_not_retried_again(self, kommo, messenger):
        """H1-NEW fix: fail → retry-success path sets next_retry_at=None.

        Scenario:
        1. Temporal trigger fires → MessengerError → status='failed', next_retry_at=+24h
        2. Cron +25h → process_retries() → retry succeeds → status='sent', next_retry_at=None
        3. Cron +50h → process_retries() → NOT retried (next_retry_at IS NULL in query)
        """
        from server.messenger import MessengerError
        from server.cron import process_retries

        lead = _make_lead(202, 93860331, dc_date=date(2026, 3, 7), aa_date=None)
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = date(2026, 3, 7)

        # Step 1: Initial temporal send fails
        messenger.send_message.side_effect = MessengerError("Wazzup down")
        _run_cron(kommo, messenger)

        msgs = get_messages(kommo_lead_id=202)
        assert len(msgs) == 1
        assert msgs[0]["status"] == "failed"
        assert msgs[0]["next_retry_at"] is not None  # retry is scheduled

        # Step 2: 25h later — process_retries() picks up the failed record, retry succeeds
        messenger.send_message.side_effect = None
        messenger.send_message.return_value = {"message_id": "wz-retry-001", "status": "sent"}
        with freeze_time("2026-03-05 11:00:00"):
            with (
                patch("server.cron.get_messenger", return_value=messenger),
                patch("server.cron.get_kommo_client", return_value=kommo),
                patch("server.cron.get_alerter", return_value=MagicMock()),
            ):
                process_retries()

        msgs = get_messages(kommo_lead_id=202)
        assert msgs[0]["status"] == "sent"
        assert msgs[0]["next_retry_at"] is None  # H1-NEW: no further retry for temporal
        assert msgs[0]["attempts"] == 2

        # Step 3: 50h later — process_retries() must NOT resend
        send_count_before = messenger.send_message.call_count
        with freeze_time("2026-03-06 11:00:00"):
            with (
                patch("server.cron.get_messenger", return_value=messenger),
                patch("server.cron.get_kommo_client", return_value=kommo),
                patch("server.cron.get_alerter", return_value=MagicMock()),
            ):
                process_retries()

        assert messenger.send_message.call_count == send_count_before  # no additional sends
        msgs_final = get_messages(kommo_lead_id=202)
        assert msgs_final[0]["attempts"] == 2  # unchanged

    def test_sent_temporal_not_retried_by_process_retries(self, kommo, messenger):
        """H1 fix: successfully sent temporal message has next_retry_at=None —
        process_retries() must NOT pick it up and resend it."""
        from server.cron import process_retries
        lead = _make_lead(201, 93860331, dc_date=date(2026, 3, 7), aa_date=None)
        kommo.get_active_leads.return_value = [lead]
        kommo.extract_termin_date_dc.return_value = date(2026, 3, 7)

        # Initial temporal send
        _run_cron(kommo, messenger)
        assert messenger.send_message.call_count == 1

        msgs = get_messages(kommo_lead_id=201)
        assert len(msgs) == 1
        assert msgs[0]["status"] == "sent"
        assert msgs[0]["next_retry_at"] is None  # H1: no retry scheduled

        # Advance 25 hours — process_retries() should NOT resend lead 201.
        # (Other failed messages from DB may be retried; we verify lead 201 specifically.)
        with freeze_time("2026-03-05 11:00:00"):
            with (
                patch("server.cron.get_messenger", return_value=messenger),
                patch("server.cron.get_alerter", return_value=MagicMock()),
            ):
                process_retries()

        # Lead 201's DB record must be unchanged: still sent, attempts=1
        msgs_after = get_messages(kommo_lead_id=201)
        assert len(msgs_after) == 1
        assert msgs_after[0]["status"] == "sent"
        assert msgs_after[0]["attempts"] == 1  # Not incremented by process_retries
