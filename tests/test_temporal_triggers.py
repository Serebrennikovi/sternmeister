"""Unit tests for T13 temporal triggers.

Tests cover:
- days_until → line mapping
- STOP statuses blocking
- deduplication
- customer-facing institution normalization
- AA -7 stage gate
- weekday_name (all 7 days)
- today computed in Berlin timezone
- berater_day_minus_7 active send path
"""

import json
import sqlite3
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from server.template_helpers import B2_CHECKLIST_TEXT, CUSTOMER_FACING_BERATER
from server.utils import format_date_ru, weekday_name


# ---------------------------------------------------------------------------
# weekday_name
# ---------------------------------------------------------------------------

class TestWeekdayName:
    def test_monday(self):
        assert weekday_name(date(2026, 3, 2)) == "Понедельник"  # Mon

    def test_tuesday(self):
        assert weekday_name(date(2026, 3, 3)) == "Вторник"

    def test_wednesday(self):
        assert weekday_name(date(2026, 3, 4)) == "Среда"

    def test_thursday(self):
        assert weekday_name(date(2026, 3, 5)) == "Четверг"

    def test_friday(self):
        assert weekday_name(date(2026, 3, 6)) == "Пятница"

    def test_saturday(self):
        assert weekday_name(date(2026, 3, 7)) == "Суббота"

    def test_sunday(self):
        assert weekday_name(date(2026, 3, 8)) == "Воскресенье"


# ---------------------------------------------------------------------------
# format_date_ru
# ---------------------------------------------------------------------------

class TestFormatDateRu:
    def test_format(self):
        assert format_date_ru(date(2026, 3, 25)) == "25.03.2026"

    def test_leading_zeros(self):
        assert format_date_ru(date(2026, 1, 5)) == "05.01.2026"


# ---------------------------------------------------------------------------
# DAYS_TO_LINE mapping — tested via process_temporal_triggers logic
# ---------------------------------------------------------------------------

class TestDaysToLineMapping:
    """Verify the _DAYS_TO_LINE mapping in cron.py."""

    def test_all_valid_days(self):
        from server.cron import _DAYS_TO_LINE
        assert _DAYS_TO_LINE[7] == "berater_day_minus_7"
        assert _DAYS_TO_LINE[3] == "berater_day_minus_3"
        assert _DAYS_TO_LINE[1] == "berater_day_minus_1"
        assert _DAYS_TO_LINE[0] == "berater_day_0"

    def test_other_days_not_in_map(self):
        from server.cron import _DAYS_TO_LINE
        for d in [2, 4, 5, 6, 8, -1, 30]:
            assert d not in _DAYS_TO_LINE


# ---------------------------------------------------------------------------
# extract_termin_date_dc / extract_termin_date_aa
# ---------------------------------------------------------------------------

def _make_lead_with_field(field_id: int, ts: int, status_id: int = 93860331) -> dict:
    return {
        "id": 111,
        "status_id": status_id,
        "pipeline_id": 12154099,
        "custom_fields_values": [
            {"field_id": field_id, "values": [{"value": ts}]},
        ],
        "_embedded": {"contacts": [{"id": 999}]},
    }


class TestExtractTerminDates:
    # 2026-03-25 midnight Berlin (CET = UTC+1) as Unix timestamp
    _DC_TS = int(datetime(2026, 3, 25, 0, 0, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())

    def test_extract_dc_returns_date(self):
        from server.kommo import KommoClient
        lead = _make_lead_with_field(887026, self._DC_TS)
        result = KommoClient.extract_termin_date_dc(lead)
        assert isinstance(result, date)
        assert result == date(2026, 3, 25)

    def test_extract_aa_returns_date(self):
        from server.kommo import KommoClient
        lead = _make_lead_with_field(887028, self._DC_TS)
        result = KommoClient.extract_termin_date_aa(lead)
        assert result == date(2026, 3, 25)

    def test_extract_dc_returns_none_if_field_absent(self):
        from server.kommo import KommoClient
        lead = {"id": 1, "custom_fields_values": []}
        assert KommoClient.extract_termin_date_dc(lead) is None

    def test_extract_aa_returns_none_if_field_absent(self):
        from server.kommo import KommoClient
        lead = {"id": 1, "custom_fields_values": []}
        assert KommoClient.extract_termin_date_aa(lead) is None

    def test_extract_dc_wrong_field_not_matched(self):
        """Field 887028 (АА) should not match DC extractor."""
        from server.kommo import KommoClient
        lead = _make_lead_with_field(887028, self._DC_TS)
        assert KommoClient.extract_termin_date_dc(lead) is None

    def test_extract_invalid_value_returns_none(self):
        from server.kommo import KommoClient
        lead = {
            "id": 1,
            "custom_fields_values": [
                {"field_id": 887026, "values": [{"value": "not-a-number"}]},
            ],
        }
        assert KommoClient.extract_termin_date_dc(lead) is None


# ---------------------------------------------------------------------------
# Berlin timezone: today computed correctly across midnight UTC
# ---------------------------------------------------------------------------

class TestBerlinToday:
    @freeze_time("2026-03-04 23:05:00", tz_offset=0)
    def test_berlin_is_ahead_of_utc(self):
        """23:05 UTC = 00:05 Berlin next day (CET = UTC+1)."""
        from zoneinfo import ZoneInfo
        berlin_today = datetime.now(tz=ZoneInfo("Europe/Berlin")).date()
        utc_today = datetime.now(tz=timezone.utc).date()
        assert berlin_today == date(2026, 3, 5)
        assert utc_today == date(2026, 3, 4)
        assert berlin_today > utc_today


# ---------------------------------------------------------------------------
# process_temporal_triggers: behavior tests via mocks
# ---------------------------------------------------------------------------

def _make_lead(lead_id, status_id, dc_ts=None, aa_ts=None):
    custom_fields = []
    if dc_ts is not None:
        custom_fields.append({"field_id": 887026, "values": [{"value": dc_ts}]})
    if aa_ts is not None:
        custom_fields.append({"field_id": 887028, "values": [{"value": aa_ts}]})
    return {
        "id": lead_id,
        "status_id": status_id,
        "pipeline_id": 12154099,
        "custom_fields_values": custom_fields,
        "_embedded": {"contacts": [{"id": 9000 + lead_id}]},
    }


def _fake_contact(name: str = "Иван Иванов", phone: str = "+4917612345678") -> dict:
    return {
        "id": 999,
        "name": name,
        "custom_fields_values": [
            {
                "field_code": "PHONE",
                "values": [{"value": phone}],
            }
        ],
    }


# Freeze: 2026-03-04 10:00 UTC = 11:00 Berlin (inside send window)
@pytest.fixture
def mock_cron_deps():
    """Patch all external dependencies for process_temporal_triggers."""
    with (
        patch("server.cron.get_kommo_client") as mock_kommo,
        patch("server.cron.get_messenger") as mock_messenger,
        patch("server.cron.create_message") as mock_create,
        patch("server.cron.get_temporal_dedup", return_value=False) as mock_dedup,
        patch("server.cron._add_kommo_note") as mock_note,
        patch("server.cron.get_alerter") as mock_alerter,
        patch("server.cron.PHONE_WHITELIST", None),
    ):
        kommo = MagicMock()
        mock_kommo.return_value = kommo
        kommo.get_contact.return_value = _fake_contact()
        kommo.extract_name.return_value = "Иван Иванов"
        kommo.extract_phone.return_value = "+4917612345678"
        kommo.extract_termin_date_dc.return_value = None
        kommo.extract_termin_date_aa.return_value = None
        kommo.extract_time_termin.return_value = None

        messenger = MagicMock()
        mock_messenger.return_value = messenger
        messenger.send_message.return_value = {"message_id": "msg-abc", "status": "sent"}
        messenger.build_message_text.return_value = "[template] Иван Иванов"

        alerter = MagicMock()
        mock_alerter.return_value = alerter
        mock_create.return_value = 42

        yield {
            "kommo": kommo,
            "messenger": messenger,
            "create_message": mock_create,
            "dedup": mock_dedup,
            "note": mock_note,
            "alerter": alerter,
        }


@freeze_time("2026-03-04 10:00:00", tz_offset=0)
class TestProcessTemporalTriggers:
    """today = 2026-03-04 (Berlin, inside send window at 11:00)."""

    def _make_lead_with_dc(self, lead_id, days_offset, status_id=93860331):
        """Create a lead whose DC date is today + days_offset."""
        # 2026-03-04 + offset, midnight Berlin CET = UTC+1
        from zoneinfo import ZoneInfo
        from datetime import timedelta
        target = date(2026, 3, 4) + timedelta(days=days_offset)
        dt = datetime(target.year, target.month, target.day, 0, 0, 0,
                      tzinfo=ZoneInfo("Europe/Berlin"))
        ts = int(dt.timestamp())
        return _make_lead(lead_id, status_id, dc_ts=ts)

    def test_days_3_sends_berater_day_minus_3(self, mock_cron_deps):
        lead = self._make_lead_with_dc(1, 3)
        mock_cron_deps["kommo"].get_active_leads.return_value = [lead]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_called_once()
        call_args = mock_cron_deps["messenger"].send_message.call_args
        md = call_args[0][1]
        assert md.line == "berater_day_minus_3"
        assert md.schedule_text == "Суббота, 07.03.2026"

    def test_days_1_sends_berater_day_minus_1(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(2, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 5)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        mock_cron_deps["kommo"].extract_time_termin.return_value = " 14:45 "
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        call_args = mock_cron_deps["messenger"].send_message.call_args
        md = call_args[0][1]
        assert md.line == "berater_day_minus_1"
        assert md.time == "14:45"
        assert md.datetime_text == "05.03.2026 в 14:45"
        call_kwargs = mock_cron_deps["create_message"].call_args.kwargs
        values = json.loads(call_kwargs["template_values"])
        assert values["name"] == "Иван Иванов"
        assert values["date"] == "05.03.2026"
        assert values["time"] == "14:45"
        assert values["datetime_text"] == "05.03.2026 в 14:45"

    def test_days_0_sends_berater_day_0(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(3, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 4)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        call_args = mock_cron_deps["messenger"].send_message.call_args
        assert call_args[0][1].line == "berater_day_0"

    def test_days_7_sends_berater_day_minus_7(self, mock_cron_deps):
        """berater_day_minus_7 is now active utility template and must be sent."""
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(4, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 11)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_called_once()
        call_args = mock_cron_deps["messenger"].send_message.call_args
        md = call_args[0][1]
        assert md.line == "berater_day_minus_7"
        assert md.checklist_text == B2_CHECKLIST_TEXT
        call_kwargs = mock_cron_deps["create_message"].call_args.kwargs
        values = json.loads(call_kwargs["template_values"])
        assert values["name"] == "Иван Иванов"
        assert values["institution"] == CUSTOMER_FACING_BERATER
        assert values["date"] == "11.03.2026"
        assert values["checklist_text"] == B2_CHECKLIST_TEXT
        mock_cron_deps["create_message"].assert_called_once()

    def test_days_7_uses_plaintext_checklist(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(40, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 11)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        call_args = mock_cron_deps["messenger"].send_message.call_args
        md = call_args[0][1]
        assert md.line == "berater_day_minus_7"
        assert "Angebot от SternMeister" in md.checklist_text
        call_kwargs = mock_cron_deps["create_message"].call_args.kwargs
        values = json.loads(call_kwargs["template_values"])
        assert "Angebot от SternMeister" in values["checklist_text"]

    def test_days_2_no_trigger(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(5, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 6)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()

    def test_days_minus_1_no_trigger(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(6, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 3)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()

    def test_stop_status_dc_blocks_lead(self, mock_cron_deps):
        """STOP status 93860875 (ДЦ отменён) → no messages."""
        lead = _make_lead(7, 93860875)  # STOP
        mock_cron_deps["kommo"].get_active_leads.return_value = [lead]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()

    def test_stop_status_aa_blocks_lead(self, mock_cron_deps):
        """STOP status 93860883 (АА отменён) → no messages."""
        lead = _make_lead(8, 93860883)  # STOP
        mock_cron_deps["kommo"].get_active_leads.return_value = [lead]
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = date(2026, 3, 7)
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()

    def test_dedup_skips_already_sent(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(9, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        mock_cron_deps["dedup"].return_value = True  # already sent
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()

    def test_institution_is_customer_facing_for_dc(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(10, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        call_args = mock_cron_deps["messenger"].send_message.call_args
        assert call_args[0][1].institution == CUSTOMER_FACING_BERATER

    def test_institution_is_customer_facing_for_aa(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(11, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = None
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = date(2026, 3, 7)
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        call_args = mock_cron_deps["messenger"].send_message.call_args
        assert call_args[0][1].institution == CUSTOMER_FACING_BERATER

    def test_aa_day_minus_7_requires_allowed_status(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(112, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = None
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = date(2026, 3, 11)
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()

    def test_aa_day_minus_7_sends_on_allowed_stage(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(113, 102183943)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = None
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = date(2026, 3, 11)
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        call_args = mock_cron_deps["messenger"].send_message.call_args
        assert call_args[0][1].line == "berater_day_minus_7"
        assert call_args[0][1].institution == CUSTOMER_FACING_BERATER

    def test_both_dc_and_aa_processed_independently(self, mock_cron_deps):
        """One lead with both ДЦ in 3 days and АА in 1 day → 2 messages."""
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(12, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)  # +3
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = date(2026, 3, 5)  # +1
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        assert mock_cron_deps["messenger"].send_message.call_count == 2

    def test_get_active_leads_error_returns_early(self, mock_cron_deps):
        from server.kommo import KommoAPIError
        mock_cron_deps["kommo"].get_active_leads.side_effect = KommoAPIError("API down")
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()
        mock_cron_deps["alerter"].alert_cron_error.assert_called_once()

    def test_messenger_error_saves_failed_record(self, mock_cron_deps):
        from server.messenger import MessengerError
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(13, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        mock_cron_deps["messenger"].send_message.side_effect = MessengerError("timeout")
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["create_message"].assert_called_once()
        call_kwargs = mock_cron_deps["create_message"].call_args.kwargs
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["attempts"] == 1
        assert call_kwargs["template_values"] is not None

    def test_messenger_error_other_leads_continue(self, mock_cron_deps):
        """MessengerError on first lead → other leads still processed."""
        from server.messenger import MessengerError
        leads = [_make_lead(i, 93860331) for i in [20, 21, 22]]
        mock_cron_deps["kommo"].get_active_leads.return_value = leads

        def dc_side_effect(lead):
            return date(2026, 3, 7)  # all +3 days
        def aa_side_effect(lead):
            return None

        mock_cron_deps["kommo"].extract_termin_date_dc.side_effect = dc_side_effect
        mock_cron_deps["kommo"].extract_termin_date_aa.side_effect = aa_side_effect
        mock_cron_deps["messenger"].send_message.side_effect = [
            MessengerError("fail"),
            {"message_id": "ok2", "status": "sent"},
            {"message_id": "ok3", "status": "sent"},
        ]
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        # 3 send_message calls total (one fails, two succeed)
        assert mock_cron_deps["messenger"].send_message.call_count == 3

    def test_contact_error_skips_lead(self, mock_cron_deps):
        from server.kommo import KommoAPIError
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(14, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        mock_cron_deps["kommo"].get_contact.side_effect = KommoAPIError("Contact not found")
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()
        mock_cron_deps["alerter"].alert_kommo_error.assert_called_once()

    def test_no_name_skips_lead(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(15, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        mock_cron_deps["kommo"].extract_name.return_value = None
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()

    def test_no_phone_skips_lead(self, mock_cron_deps):
        """Phone not found for contact → skip lead, Telegram alert (M2-NEW)."""
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(19, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        mock_cron_deps["kommo"].extract_phone.return_value = ""  # phone not found
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()
        mock_cron_deps["alerter"].alert_kommo_error.assert_called_once()

    def test_outside_send_window_skips_all(self, mock_cron_deps):
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(16, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        from server.cron import process_temporal_triggers
        # Freeze at night (22:00 UTC = 23:00 Berlin = outside 9-21)
        with freeze_time("2026-03-04 21:30:00"):
            process_temporal_triggers()
        mock_cron_deps["messenger"].send_message.assert_not_called()

    def test_weekday_set_correctly_in_message_data(self, mock_cron_deps):
        """2026-03-07 is Saturday → weekday='Суббота'."""
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(17, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        call_args = mock_cron_deps["messenger"].send_message.call_args
        md = call_args[0][1]
        assert md.weekday == "Суббота"
        assert md.date == "07.03.2026"
        assert md.schedule_text == "Суббота, 07.03.2026"

    def test_template_values_saved_to_db(self, mock_cron_deps):
        """template_values JSON saved in create_message call."""
        mock_cron_deps["kommo"].get_active_leads.return_value = [_make_lead(18, 93860331)]
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        mock_cron_deps["kommo"].extract_name.return_value = "Анна"
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        call_kwargs = mock_cron_deps["create_message"].call_args.kwargs
        assert call_kwargs["template_values"] is not None
        vals = json.loads(call_kwargs["template_values"])
        # M2 fix: template_values stored as dict, not positional list
        assert isinstance(vals, dict)
        assert vals["name"] == "Анна"

    def test_integrity_error_on_create_continues_other_leads(self, mock_cron_deps):
        """sqlite3.IntegrityError from create_message is caught; remaining leads processed (H2 fix)."""
        import sqlite3 as sqlite3_mod
        leads = [_make_lead(50, 93860331), _make_lead(51, 93860331)]
        mock_cron_deps["kommo"].get_active_leads.return_value = leads
        mock_cron_deps["kommo"].extract_termin_date_dc.return_value = date(2026, 3, 7)
        mock_cron_deps["kommo"].extract_termin_date_aa.return_value = None
        # First create_message raises IntegrityError (concurrent cron race), second succeeds
        mock_cron_deps["create_message"].side_effect = [
            sqlite3_mod.IntegrityError("UNIQUE constraint failed"),
            42,
        ]
        from server.cron import process_temporal_triggers
        process_temporal_triggers()
        # Both leads: send_message was attempted
        assert mock_cron_deps["messenger"].send_message.call_count == 2
        # Both leads: create_message was attempted (second succeeded)
        assert mock_cron_deps["create_message"].call_count == 2


# ---------------------------------------------------------------------------
# HTTP-level pagination tests for KommoClient.get_active_leads (L1-NEW)
# ---------------------------------------------------------------------------

class TestGetActiveLeadsPagination:
    """Unit tests for the HTTP pagination loop inside KommoClient.get_active_leads().

    Mocks _request directly (not get_active_leads) to verify the while/page loop.
    """

    def _make_client(self):
        from server.kommo import KommoClient
        return KommoClient.__new__(KommoClient)

    def _mock_response(self, leads, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = {"_embedded": {"leads": leads}}
        return resp

    def test_two_pages_returns_all_leads(self):
        """Page 1 (250) + page 2 (50) → 300 leads, 2 HTTP requests with page=1 and page=2."""
        from unittest.mock import patch
        client = self._make_client()
        page1 = [{"id": i} for i in range(250)]
        page2 = [{"id": 250 + i} for i in range(50)]
        resp1 = self._mock_response(page1)
        resp2 = self._mock_response(page2)
        with patch.object(client, "_request", side_effect=[resp1, resp2]) as mock_req:
            with patch.object(client, "_parse_json", side_effect=lambda r: r.json()):
                result = client.get_active_leads(12154099)
        assert len(result) == 300
        assert mock_req.call_count == 2
        assert mock_req.call_args_list[0][1]["params"]["page"] == 1
        assert mock_req.call_args_list[1][1]["params"]["page"] == 2

    def test_204_on_first_page_returns_empty(self):
        """204 No Content on first request → empty list, single HTTP request."""
        from unittest.mock import patch
        client = self._make_client()
        resp = MagicMock()
        resp.status_code = 204
        with patch.object(client, "_request", return_value=resp) as mock_req:
            result = client.get_active_leads(12154099)
        assert result == []
        assert mock_req.call_count == 1

    def test_exactly_250_leads_fetches_second_page(self):
        """Exactly 250 leads on page 1 → must fetch page 2 (not stop early)."""
        from unittest.mock import patch
        client = self._make_client()
        page1 = [{"id": i} for i in range(250)]
        resp1 = self._mock_response(page1)
        resp2 = self._mock_response([])  # empty page 2 → stop
        with patch.object(client, "_request", side_effect=[resp1, resp2]) as mock_req:
            with patch.object(client, "_parse_json", side_effect=lambda r: r.json()):
                result = client.get_active_leads(12154099)
        assert len(result) == 250
        assert mock_req.call_count == 2  # second request made for empty page
