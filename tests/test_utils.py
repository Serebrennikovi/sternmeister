"""Tests for server.utils — send window logic (T07).

Boundary cases for is_in_send_window() and get_next_send_window_start().
Default window: 8:00-22:00 Europe/Berlin.

CET  (winter) = UTC+1  — e.g. February
CEST (summer) = UTC+2  — e.g. July
DST spring-forward: last Sunday of March (2026-03-29), 2:00 CET → 3:00 CEST
DST fall-back:       last Sunday of October (2026-10-25), 3:00 CEST → 2:00 CET
"""

from freezegun import freeze_time

from server.utils import get_next_send_window_start, is_in_send_window


# ---------------------------------------------------------------------------
# is_in_send_window()
# ---------------------------------------------------------------------------

class TestIsInSendWindowCET:
    """Winter (CET, UTC+1) — February 2026."""

    @freeze_time("2026-02-24T06:59:00Z")  # 07:59 Berlin
    def test_before_window(self):
        assert not is_in_send_window()

    @freeze_time("2026-02-24T07:00:00Z")  # 08:00 Berlin
    def test_window_start(self):
        assert is_in_send_window()

    @freeze_time("2026-02-24T14:30:00Z")  # 15:30 Berlin
    def test_inside_window(self):
        assert is_in_send_window()

    @freeze_time("2026-02-24T20:59:00Z")  # 21:59 Berlin
    def test_last_minute(self):
        assert is_in_send_window()

    @freeze_time("2026-02-24T21:00:00Z")  # 22:00 Berlin
    def test_window_end(self):
        assert not is_in_send_window()

    @freeze_time("2026-02-24T21:01:00Z")  # 22:01 Berlin
    def test_after_window(self):
        assert not is_in_send_window()

    @freeze_time("2026-02-24T22:30:00Z")  # 23:30 Berlin
    def test_late_night(self):
        assert not is_in_send_window()

    @freeze_time("2026-02-24T23:00:00Z")  # 00:00 next day Berlin
    def test_midnight(self):
        assert not is_in_send_window()


class TestIsInSendWindowCEST:
    """Summer (CEST, UTC+2) — July 2026."""

    @freeze_time("2026-07-15T05:59:00Z")  # 07:59 Berlin
    def test_before_window(self):
        assert not is_in_send_window()

    @freeze_time("2026-07-15T06:00:00Z")  # 08:00 Berlin
    def test_window_start(self):
        assert is_in_send_window()

    @freeze_time("2026-07-15T19:59:00Z")  # 21:59 Berlin
    def test_last_minute(self):
        assert is_in_send_window()

    @freeze_time("2026-07-15T20:00:00Z")  # 22:00 Berlin
    def test_window_end(self):
        assert not is_in_send_window()


# ---------------------------------------------------------------------------
# get_next_send_window_start()
# ---------------------------------------------------------------------------

class TestGetNextSendWindowCET:
    """Winter (CET, UTC+1): 8:00 Berlin = 07:00 UTC."""

    @freeze_time("2026-02-24T23:00:00Z")  # 00:00 Berlin (next day) — early morning
    def test_midnight_returns_today(self):
        # 00:00 Berlin Feb 25 → should return today (Feb 25) at 08:00
        assert get_next_send_window_start() == "2026-02-25T07:00:00+00:00"

    @freeze_time("2026-02-24T02:00:00Z")  # 03:00 Berlin — early morning
    def test_early_morning_returns_today(self):
        assert get_next_send_window_start() == "2026-02-24T07:00:00+00:00"

    @freeze_time("2026-02-24T06:59:00Z")  # 07:59 Berlin — before window
    def test_before_window_returns_today(self):
        assert get_next_send_window_start() == "2026-02-24T07:00:00+00:00"

    @freeze_time("2026-02-24T07:00:00Z")  # 08:00 Berlin — at window start
    def test_at_window_start_returns_tomorrow(self):
        assert get_next_send_window_start() == "2026-02-25T07:00:00+00:00"

    @freeze_time("2026-02-24T14:30:00Z")  # 15:30 Berlin — inside window
    def test_inside_window_returns_tomorrow(self):
        assert get_next_send_window_start() == "2026-02-25T07:00:00+00:00"

    @freeze_time("2026-02-24T21:00:00Z")  # 22:00 Berlin — window closed
    def test_at_window_end_returns_tomorrow(self):
        assert get_next_send_window_start() == "2026-02-25T07:00:00+00:00"

    @freeze_time("2026-02-24T22:30:00Z")  # 23:30 Berlin
    def test_late_night_returns_tomorrow(self):
        assert get_next_send_window_start() == "2026-02-25T07:00:00+00:00"


class TestGetNextSendWindowCEST:
    """Summer (CEST, UTC+2): 8:00 Berlin = 06:00 UTC."""

    @freeze_time("2026-07-15T05:59:00Z")  # 07:59 Berlin
    def test_before_window_returns_today(self):
        assert get_next_send_window_start() == "2026-07-15T06:00:00+00:00"

    @freeze_time("2026-07-15T06:00:00Z")  # 08:00 Berlin
    def test_at_window_start_returns_tomorrow(self):
        assert get_next_send_window_start() == "2026-07-16T06:00:00+00:00"

    @freeze_time("2026-07-15T20:00:00Z")  # 22:00 Berlin
    def test_at_window_end_returns_tomorrow(self):
        assert get_next_send_window_start() == "2026-07-16T06:00:00+00:00"


class TestGetNextSendWindowDST:
    """DST transition edge cases."""

    @freeze_time("2026-03-28T22:30:00Z")  # 23:30 Berlin CET (night before spring-forward)
    def test_spring_forward_night(self):
        # Next 8:00 is March 29 — already in CEST (UTC+2) → 06:00 UTC
        assert get_next_send_window_start() == "2026-03-29T06:00:00+00:00"

    @freeze_time("2026-10-24T21:30:00Z")  # 23:30 Berlin CEST (night before fall-back)
    def test_fall_back_night(self):
        # Next 8:00 is Oct 25 — already in CET (UTC+1) → 07:00 UTC
        assert get_next_send_window_start() == "2026-10-25T07:00:00+00:00"
