"""Tests for S02 config changes: PIPELINE_CONFIG, TEMPLATE_MAP, STOP_STATUSES (T12)."""

import pytest

from server.config import (
    PIPELINE_CONFIG,
    STOP_STATUSES,
    TEMPLATE_MAP,
    _non_empty,
    determine_line,
)
from server.template_helpers import (
    B2_CHECKLIST_TEXT,
    CUSTOMER_FACING_BERATER,
    build_gosniki_consultation_done_texts,
)


class TestPipelineConfig:
    def test_berater_has_berater_accepted(self):
        assert PIPELINE_CONFIG[12154099][93860331] == "berater_accepted"

    def test_gosniki_has_gosniki_consultation_done(self):
        assert PIPELINE_CONFIG[10935879][95514983] == "gosniki_consultation_done"

    def test_old_gosniki_pipeline_removed(self):
        assert 10631243 not in PIPELINE_CONFIG

    def test_determine_line_berater_accepted(self):
        assert determine_line(12154099, 93860331) == "berater_accepted"

    def test_determine_line_gosniki_consultation_done(self):
        assert determine_line(10935879, 95514983) == "gosniki_consultation_done"

    def test_determine_line_unknown_status(self):
        assert determine_line(12154099, 999999) is None

    def test_determine_line_unknown_pipeline(self):
        assert determine_line(99999, 93860331) is None


class TestStopStatuses:
    def test_berater_has_stop_statuses(self):
        assert 12154099 in STOP_STATUSES

    def test_berater_stop_statuses_values(self):
        assert STOP_STATUSES[12154099] == {93860875, 93860883}


class TestTemplateMap:
    def test_all_s02_lines_present(self):
        expected = {
            "gosniki_consultation_done", "berater_accepted",
            "berater_day_minus_7", "berater_day_minus_3",
            "berater_day_minus_1", "berater_day_0",
        }
        assert set(TEMPLATE_MAP.keys()) == expected

    def test_legacy_first_second_removed(self):
        assert "first" not in TEMPLATE_MAP
        assert "second" not in TEMPLATE_MAP

    def test_gosniki_consultation_done_guid(self):
        assert TEMPLATE_MAP["gosniki_consultation_done"]["template_guid"] == \
            "95ddec60-bb6b-44a8-b5fb-a98abd76f974"

    def test_berater_accepted_guid(self):
        assert TEMPLATE_MAP["berater_accepted"]["template_guid"] == \
            "47d2946c-f66a-4697-b702-eb5d138bb1f1"

    def test_berater_day_minus_7_guid(self):
        assert TEMPLATE_MAP["berater_day_minus_7"]["template_guid"] == \
            "b028964c-9c27-4bc9-9b97-02a5e283df16"
        assert callable(TEMPLATE_MAP["berater_day_minus_7"]["vars"])

    def test_berater_day_minus_3_guid(self):
        assert TEMPLATE_MAP["berater_day_minus_3"]["template_guid"] == \
            "e1cb07aa-5236-4f8a-84dc-fef26b3cccf6"

    def test_berater_day_minus_1_guid(self):
        assert TEMPLATE_MAP["berater_day_minus_1"]["template_guid"] == \
            "a9b04e05-6b6c-4a5f-9463-d8a0d96316f4"

    def test_berater_day_0_guid(self):
        assert TEMPLATE_MAP["berater_day_0"]["template_guid"] == \
            "176a8b5b-8704-4d04-aee5-0fbd08641806"

    def test_berater_day_0_vars_falls_back_to_client_name(self):
        fn = TEMPLATE_MAP["berater_day_0"]["vars"]
        assert fn(name=None) == ["Клиент"]

    def test_gosniki_vars_returns_utility_payload(self):
        fn = TEMPLATE_MAP["gosniki_consultation_done"]["vars"]
        texts = build_gosniki_consultation_done_texts("Анна")
        assert fn(**texts) == [
            "SternMeister",
            (
                "Анна, вы получили комплект документов, необходимых для записи на термин. "
                "Мы уже забронировали для вас место для консультации с нашим карьерным экспертом. "
                "Пожалуйста, постарайтесь сегодня записаться на термин."
            ),
        ]

    def test_berater_accepted_vars_returns_utility_payload(self):
        fn = TEMPLATE_MAP["berater_accepted"]["vars"]
        assert fn(name="Мария Шмидт") == ["Мария Шмидт"]

    def test_berater_day_minus_7_vars_returns_4_values(self):
        fn = TEMPLATE_MAP["berater_day_minus_7"]["vars"]
        assert fn(
            name="Анна",
            date="25.03.2026",
            institution=CUSTOMER_FACING_BERATER,
            checklist_text=B2_CHECKLIST_TEXT,
        ) == ["Анна", "25.03.2026", CUSTOMER_FACING_BERATER, B2_CHECKLIST_TEXT]

    def test_berater_day_minus_3_vars_returns_3_values(self):
        fn = TEMPLATE_MAP["berater_day_minus_3"]["vars"]
        result = fn(
            name="Анна",
            institution=CUSTOMER_FACING_BERATER,
            schedule_text="Среда, 25.03.2026",
        )
        assert result == ["Анна", CUSTOMER_FACING_BERATER, "Среда, 25.03.2026"]



class TestNonEmpty:
    def test_none_returns_fallback(self):
        assert _non_empty(None, "fallback") == "fallback"

    def test_empty_string_returns_fallback(self):
        assert _non_empty("", "fallback") == "fallback"

    def test_whitespace_returns_fallback(self):
        assert _non_empty("   ", "fallback") == "fallback"

    def test_value_is_trimmed(self):
        assert _non_empty("  value  ", "fallback") == "value"
