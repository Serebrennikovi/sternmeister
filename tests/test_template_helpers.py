"""Unit tests for shared template helpers used by app.py and cron.py."""

from datetime import date

from server.template_helpers import (
    AA_DAY_MINUS_7_ALLOWED_STATUSES,
    B2_CHECKLIST_TEXT,
    B1_FALLBACK_INSTITUTION,
    B1_NO_DATE_DATETIME_TEXT,
    CUSTOMER_FACING_BERATER,
    TIME_FALLBACK,
    build_berater_accepted_texts,
    build_berater_day_minus_3_schedule_text,
    build_berater_day_minus_1_texts,
    has_newer_berater_temporal_state,
    iter_temporal_candidates,
    normalize_time_raw,
    pick_berater_accepted_institution_and_date,
)


class TestPickBeraterAcceptedInstitutionAndDate:
    _TODAY = date(2026, 3, 10)

    def test_dc_only(self):
        institution, date_text = pick_berater_accepted_institution_and_date(
            date(2026, 3, 20),
            None,
            today=self._TODAY,
        )
        assert institution == CUSTOMER_FACING_BERATER
        assert date_text == "20.03.2026"

    def test_aa_only(self):
        institution, date_text = pick_berater_accepted_institution_and_date(
            None,
            date(2026, 3, 12),
            today=self._TODAY,
        )
        assert institution == CUSTOMER_FACING_BERATER
        assert date_text == "12.03.2026"

    def test_both_dc_closer(self):
        institution, date_text = pick_berater_accepted_institution_and_date(
            date(2026, 3, 11),
            date(2026, 3, 16),
            today=self._TODAY,
        )
        assert institution == CUSTOMER_FACING_BERATER
        assert date_text == "11.03.2026"

    def test_both_aa_closer(self):
        institution, date_text = pick_berater_accepted_institution_and_date(
            date(2026, 3, 21),
            date(2026, 3, 12),
            today=self._TODAY,
        )
        assert institution == CUSTOMER_FACING_BERATER
        assert date_text == "12.03.2026"

    def test_both_equal_distance_dc_wins(self):
        institution, date_text = pick_berater_accepted_institution_and_date(
            date(2026, 3, 12),
            date(2026, 3, 8),
            today=self._TODAY,
        )
        assert institution == CUSTOMER_FACING_BERATER
        assert date_text == "12.03.2026"

    def test_both_missing_uses_fallback(self):
        institution, date_text = pick_berater_accepted_institution_and_date(
            None,
            None,
            today=self._TODAY,
        )
        assert institution == B1_FALLBACK_INSTITUTION
        assert date_text is None


class TestNormalizeTimeRaw:
    def test_non_string_returns_none(self):
        assert normalize_time_raw(None) is None
        assert normalize_time_raw(123) is None

    def test_whitespace_returns_none(self):
        assert normalize_time_raw("   ") is None

    def test_string_is_trimmed(self):
        assert normalize_time_raw(" 10:30 ") == "10:30"


class TestTemporalCandidateHelpers:
    _TODAY = date(2026, 3, 4)

    def test_iter_temporal_candidates_allows_aa_day_minus_7_on_allowed_status(self):
        result = iter_temporal_candidates(
            None,
            date(2026, 3, 11),
            next(iter(AA_DAY_MINUS_7_ALLOWED_STATUSES)),
            today=self._TODAY,
        )
        assert result == [("berater_day_minus_7", date(2026, 3, 11))]

    def test_iter_temporal_candidates_blocks_aa_day_minus_7_on_other_status(self):
        result = iter_temporal_candidates(
            None,
            date(2026, 3, 11),
            93860331,
            today=self._TODAY,
        )
        assert result == []

    def test_has_newer_berater_temporal_state_detects_minus_3(self):
        assert has_newer_berater_temporal_state(
            date(2026, 3, 7),
            None,
            93860331,
            today=self._TODAY,
        )

    def test_has_newer_berater_temporal_state_ignores_minus_7(self):
        assert not has_newer_berater_temporal_state(
            date(2026, 3, 11),
            None,
            93860331,
            today=self._TODAY,
        )


class TestBuildTexts:
    def test_build_berater_accepted_uses_name_only(self):
        result = build_berater_accepted_texts("  Анна  ")
        assert result == {"name": "Анна"}

    def test_build_berater_accepted_falls_back_to_client(self):
        result = build_berater_accepted_texts("   ")
        assert result == {"name": "Клиент"}

    def test_build_berater_day_minus_3_schedule_from_date(self):
        result = build_berater_day_minus_3_schedule_text(
            date_obj=date(2026, 3, 19),
        )
        assert result == "Четверг, 19.03.2026"

    def test_build_berater_day_minus_3_schedule_from_weekday_and_date(self):
        result = build_berater_day_minus_3_schedule_text(
            weekday="Среда",
            date_text="25.03.2026",
        )
        assert result == "Среда, 25.03.2026"

    def test_build_berater_day_minus_1_with_time(self):
        result = build_berater_day_minus_1_texts(
            date_for_template="12.03.2026",
            time_raw="14:45",
        )
        assert result["time_text"] == "14:45"
        assert result["datetime_text"] == "12.03.2026 в 14:45"

    def test_build_berater_day_minus_1_without_time(self):
        result = build_berater_day_minus_1_texts(
            date_for_template="12.03.2026",
            time_raw=None,
        )
        assert result["time_text"] == TIME_FALLBACK
        assert result["datetime_text"] == "12.03.2026"
