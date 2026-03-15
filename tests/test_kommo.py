"""Unit tests for KommoClient extract helpers."""

from datetime import datetime
from zoneinfo import ZoneInfo

from server.kommo import KommoClient


def _make_lead(field_id: int, value: object) -> dict:
    return {
        "id": 123,
        "custom_fields_values": [
            {"field_id": field_id, "values": [{"value": value}]},
        ],
    }


class TestExtractTimeTermin:
    FIELD_ID = 886670

    def test_valid_timestamp_returns_hh_mm(self):
        ts = int(datetime(2026, 3, 25, 13, 30, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
        lead = _make_lead(self.FIELD_ID, ts)
        assert KommoClient.extract_time_termin(lead, self.FIELD_ID) == "13:30"

    def test_invalid_string_returns_none(self):
        lead = _make_lead(self.FIELD_ID, "not-a-number")
        assert KommoClient.extract_time_termin(lead, self.FIELD_ID) is None

    def test_missing_field_returns_none(self):
        lead = {"id": 123, "custom_fields_values": []}
        assert KommoClient.extract_time_termin(lead, self.FIELD_ID) is None

    def test_none_value_returns_none(self):
        lead = _make_lead(self.FIELD_ID, None)
        assert KommoClient.extract_time_termin(lead, self.FIELD_ID) is None
