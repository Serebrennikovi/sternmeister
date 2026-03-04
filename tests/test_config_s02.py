"""Tests for S02 config changes: PIPELINE_CONFIG, TEMPLATE_MAP, STOP_STATUSES (T12)."""

import pytest

from server.config import (
    PIPELINE_CONFIG,
    STOP_STATUSES,
    TEMPLATE_MAP,
    determine_line,
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
            "first", "second",
            "gosniki_consultation_done", "berater_accepted",
            "berater_day_minus_7", "berater_day_minus_3",
            "berater_day_minus_1", "berater_day_0",
        }
        assert set(TEMPLATE_MAP.keys()) == expected

    def test_gosniki_consultation_done_guid(self):
        assert TEMPLATE_MAP["gosniki_consultation_done"]["template_guid"] == \
            "d253993f-e2fc-441f-a877-0c2252cb300b"

    def test_berater_accepted_guid(self):
        assert TEMPLATE_MAP["berater_accepted"]["template_guid"] == \
            "18b763f8-1841-43fb-af65-669ab4c8dcea"

    def test_berater_day_minus_7_is_placeholder(self):
        assert TEMPLATE_MAP["berater_day_minus_7"]["template_guid"] is None
        assert TEMPLATE_MAP["berater_day_minus_7"]["vars"] is None

    def test_berater_day_minus_3_guid(self):
        assert TEMPLATE_MAP["berater_day_minus_3"]["template_guid"] == \
            "140a1ed5-7047-4de1-aa0d-d3fe5e0d912a"

    def test_berater_day_minus_1_guid(self):
        assert TEMPLATE_MAP["berater_day_minus_1"]["template_guid"] == \
            "7732e8ac-1bcc-42d6-a723-bbb80b635c79"

    def test_berater_day_0_guid(self):
        assert TEMPLATE_MAP["berater_day_0"]["template_guid"] == \
            "176a8b5b-8704-4d04-aee5-0fbd08641806"

    def test_gosniki_vars_returns_name(self):
        fn = TEMPLATE_MAP["gosniki_consultation_done"]["vars"]
        assert fn(name="Анна", termin_date="") == ["Анна"]

    def test_berater_accepted_vars_returns_name(self):
        fn = TEMPLATE_MAP["berater_accepted"]["vars"]
        assert fn(name="Иван", termin_date="", institution=None) == ["Иван"]

    def test_berater_day_minus_3_vars_returns_4_values(self):
        fn = TEMPLATE_MAP["berater_day_minus_3"]["vars"]
        result = fn(name="Анна", institution="Jobcenter", weekday="Среда", date="25.03.2026")
        assert result == ["Анна", "Jobcenter", "Среда", "25.03.2026"]

    def test_first_line_uses_wazzup_template_id(self):
        from server.config import WAZZUP_TEMPLATE_ID
        assert TEMPLATE_MAP["first"]["template_guid"] == WAZZUP_TEMPLATE_ID

    def test_second_line_uses_wazzup_template_id(self):
        from server.config import WAZZUP_TEMPLATE_ID
        assert TEMPLATE_MAP["second"]["template_guid"] == WAZZUP_TEMPLATE_ID

    def test_first_line_vars(self):
        fn = TEMPLATE_MAP["first"]["vars"]
        assert fn(name=None, termin_date="25.02.2026") == [
            "SternMeister", "записи на термин", "25.02.2026"
        ]

    def test_second_line_vars(self):
        fn = TEMPLATE_MAP["second"]["vars"]
        assert fn(name=None, termin_date="01.03.2026") == [
            "SternMeister", "термине", "01.03.2026"
        ]
