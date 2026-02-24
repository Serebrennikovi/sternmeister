"""Tests for server.cron — retry and pending message processing (T08).

Uses a real SQLite DB (temp file from conftest) with table cleanup between
tests for isolation.  Messenger is mocked to avoid HTTP calls.

CET (winter) = UTC+1 — February 2026.
Send window: 9:00-21:00 Berlin = 08:00-20:00 UTC in winter.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from server.db import (
    create_message,
    get_message_by_id,
    init_db,
    update_message,
    _get_conn,
)
from server.messenger import MessengerError


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
def _mock_kommo_client():
    """Mock Kommo client so _add_kommo_note doesn't make HTTP calls."""
    with patch("server.cron.get_kommo_client") as mock_gc:
        mock_gc.return_value = MagicMock()
        yield mock_gc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _past(hours: int = 25) -> str:
    """ISO timestamp *hours* ago (eligible for retry/send)."""
    return (
        datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds")


def _future(hours: int = 24) -> str:
    """ISO timestamp *hours* from now (not yet eligible)."""
    return (
        datetime.now(tz=timezone.utc) + timedelta(hours=hours)
    ).isoformat(timespec="seconds")


def _create_sent(*, attempts=1, next_retry_at=None, phone="+491234567890",
                 line="first", termin_date="25.02.2026") -> int:
    return create_message(
        kommo_lead_id=100, kommo_contact_id=200, phone=phone,
        line=line, termin_date=termin_date, message_text="Test",
        status="sent", attempts=attempts, sent_at=_past(25),
        next_retry_at=next_retry_at or _past(1),
    )


def _create_failed(*, attempts=1, next_retry_at=None) -> int:
    return create_message(
        kommo_lead_id=100, kommo_contact_id=200, phone="+491234567890",
        line="first", termin_date="25.02.2026", message_text="Test",
        status="failed", attempts=attempts,
        next_retry_at=next_retry_at or _past(1),
    )


def _create_pending(*, next_retry_at=None, phone="+491234567890",
                    line="first", termin_date="25.02.2026") -> int:
    return create_message(
        kommo_lead_id=100, kommo_contact_id=200, phone=phone,
        line=line, termin_date=termin_date, message_text="Test pending",
        status="pending", attempts=0,
        next_retry_at=next_retry_at or _past(1),
    )


# Frozen times: inside / outside send window (Berlin CET)
_IN = "2026-02-24T14:00:00Z"    # 15:00 Berlin
_OUT = "2026-02-24T22:00:00Z"   # 23:00 Berlin


# ---------------------------------------------------------------------------
# process_retries
# ---------------------------------------------------------------------------

class TestProcessRetries:

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_success(self, mock_gm):
        """Retry OK: attempts++, status=sent, sent_at/next_retry_at updated."""
        m = MagicMock()
        m.send_message.return_value = {"message_id": "wz-r1"}
        mock_gm.return_value = m

        msg_id = _create_sent(attempts=1)

        from server.cron import process_retries
        ok, fail = process_retries()

        assert (ok, fail) == (1, 0)
        row = get_message_by_id(msg_id)
        assert row["status"] == "sent"
        assert row["attempts"] == 2
        assert row["messenger_id"] == "wz-r1"
        assert row["sent_at"] is not None
        assert row["next_retry_at"] > row["sent_at"]

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_failure_increments_attempts(self, mock_gm):
        """Failed retry: status=failed AND attempts incremented (bug fix)."""
        m = MagicMock()
        m.send_message.side_effect = MessengerError("timeout")
        mock_gm.return_value = m

        msg_id = _create_sent(attempts=1)

        from server.cron import process_retries
        ok, fail = process_retries()

        assert (ok, fail) == (0, 1)
        row = get_message_by_id(msg_id)
        assert row["status"] == "failed"
        assert row["attempts"] == 2  # incremented to prevent infinite loop

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_max_attempts_not_retried(self, mock_gm):
        """attempts=3 (max) → not picked up by get_messages_for_retry."""
        m = MagicMock()
        mock_gm.return_value = m

        _create_sent(attempts=3)

        from server.cron import process_retries
        ok, fail = process_retries()

        assert (ok, fail) == (0, 0)
        m.send_message.assert_not_called()

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_future_next_retry_at_not_retried(self, mock_gm):
        """next_retry_at in the future → not eligible."""
        m = MagicMock()
        mock_gm.return_value = m

        _create_sent(next_retry_at=_future(24))

        from server.cron import process_retries
        ok, fail = process_retries()

        assert (ok, fail) == (0, 0)
        m.send_message.assert_not_called()

    @freeze_time(_OUT)
    def test_outside_window_skips(self):
        """Outside 9-21 Berlin → skip."""
        _create_sent()

        from server.cron import process_retries
        ok, fail = process_retries()

        assert (ok, fail) == (0, 0)

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_failed_message_retried(self, mock_gm):
        """status=failed with attempts < max is eligible."""
        m = MagicMock()
        m.send_message.return_value = {"message_id": "wz-rf1"}
        mock_gm.return_value = m

        msg_id = _create_failed(attempts=1)

        from server.cron import process_retries
        ok, fail = process_retries()

        assert (ok, fail) == (1, 0)
        row = get_message_by_id(msg_id)
        assert row["status"] == "sent"
        assert row["attempts"] == 2

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_multiple_messages(self, mock_gm):
        """Multiple eligible messages all processed."""
        cnt = 0

        def send(phone, data):
            nonlocal cnt
            cnt += 1
            return {"message_id": f"wz-m{cnt}"}

        m = MagicMock()
        m.send_message.side_effect = send
        mock_gm.return_value = m

        id1 = _create_sent(phone="+491111111111")
        id2 = _create_sent(phone="+492222222222")

        from server.cron import process_retries
        ok, fail = process_retries()

        assert ok == 2
        assert fail == 0
        assert get_message_by_id(id1)["attempts"] == 2
        assert get_message_by_id(id2)["attempts"] == 2

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_mixed_success_and_failure(self, mock_gm):
        """One succeeds, one fails."""
        n = 0

        def send(phone, data):
            nonlocal n
            n += 1
            if n == 1:
                return {"message_id": "wz-ok"}
            raise MessengerError("fail")

        m = MagicMock()
        m.send_message.side_effect = send
        mock_gm.return_value = m

        id1 = _create_sent(phone="+491111111111")
        id2 = _create_sent(phone="+492222222222")

        from server.cron import process_retries
        ok, fail = process_retries()

        assert (ok, fail) == (1, 1)
        assert get_message_by_id(id1)["status"] == "sent"
        assert get_message_by_id(id2)["status"] == "failed"

    @freeze_time(_IN)
    def test_no_messages(self):
        """Empty DB → (0, 0)."""
        from server.cron import process_retries
        ok, fail = process_retries()
        assert (ok, fail) == (0, 0)

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_second_line_retry(self, mock_gm):
        """line='second' messages are retried with correct MessageData."""
        m = MagicMock()
        m.send_message.return_value = {"message_id": "wz-s2"}
        mock_gm.return_value = m

        msg_id = _create_sent(line="second", termin_date="01.03.2026")

        from server.cron import process_retries
        process_retries()

        args = m.send_message.call_args
        assert args[0][1].line == "second"
        assert args[0][1].termin_date == "01.03.2026"
        assert get_message_by_id(msg_id)["attempts"] == 2

    @freeze_time(_IN)
    @patch("server.cron.get_kommo_client")
    @patch("server.cron.get_messenger")
    def test_kommo_note_added_on_success(self, mock_gm, mock_gc):
        """Successful retry adds a Kommo note to the lead."""
        m = MagicMock()
        m.send_message.return_value = {"message_id": "wz-note"}
        mock_gm.return_value = m
        kommo = MagicMock()
        mock_gc.return_value = kommo

        _create_sent(attempts=1)

        from server.cron import process_retries
        process_retries()

        kommo.add_note.assert_called_once()
        call_args = kommo.add_note.call_args
        assert call_args[0][0] == 100  # kommo_lead_id
        assert "повтор 2/3" in call_args[0][1]

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_kommo_note_failure_does_not_break_retry(self, mock_gm, _mock_kommo_client):
        """KommoAPIError in add_note is swallowed — retry still counts as success."""
        from server.kommo import KommoAPIError

        m = MagicMock()
        m.send_message.return_value = {"message_id": "wz-nf"}
        mock_gm.return_value = m
        kommo = MagicMock()
        kommo.add_note.side_effect = KommoAPIError("timeout")
        _mock_kommo_client.return_value = kommo

        msg_id = _create_sent(attempts=1)

        from server.cron import process_retries
        ok, fail = process_retries()

        assert (ok, fail) == (1, 0)
        assert get_message_by_id(msg_id)["attempts"] == 2


# ---------------------------------------------------------------------------
# process_pending
# ---------------------------------------------------------------------------

class TestProcessPending:

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_success(self, mock_gm):
        """Pending sent OK: status=sent, attempts=1, times set."""
        m = MagicMock()
        m.send_message.return_value = {"message_id": "wz-p1"}
        mock_gm.return_value = m

        msg_id = _create_pending()

        from server.cron import process_pending
        ok, fail = process_pending()

        assert (ok, fail) == (1, 0)
        row = get_message_by_id(msg_id)
        assert row["status"] == "sent"
        assert row["attempts"] == 1
        assert row["messenger_id"] == "wz-p1"
        assert row["sent_at"] is not None
        assert row["next_retry_at"] > row["sent_at"]

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_failure_increments_attempts_and_delays(self, mock_gm):
        """Failed send: stays pending, attempts incremented, next_retry_at pushed forward."""
        m = MagicMock()
        m.send_message.side_effect = MessengerError("net error")
        mock_gm.return_value = m

        msg_id = _create_pending()

        from server.cron import process_pending
        ok, fail = process_pending()

        assert (ok, fail) == (0, 1)
        row = get_message_by_id(msg_id)
        assert row["status"] == "pending"
        assert row["attempts"] == 1
        # next_retry_at should be pushed into the future (not in the past)
        assert row["next_retry_at"] > "2026-02-24T14:00:00"

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_failure_max_attempts_marks_failed(self, mock_gm):
        """Pending message at max-1 attempts -> failure -> status=failed."""
        m = MagicMock()
        m.send_message.side_effect = MessengerError("net error")
        mock_gm.return_value = m

        # MAX_RETRY_ATTEMPTS=2, max_attempts=3, so attempts=2 -> next fail -> 3 >= 3 -> failed
        msg_id = create_message(
            kommo_lead_id=100, kommo_contact_id=200, phone="+491234567890",
            line="first", termin_date="25.02.2026", message_text="Test pending",
            status="pending", attempts=2,
            next_retry_at=_past(1),
        )

        from server.cron import process_pending
        ok, fail = process_pending()

        assert (ok, fail) == (0, 1)
        row = get_message_by_id(msg_id)
        assert row["status"] == "failed"
        assert row["attempts"] == 3

    @freeze_time(_OUT)
    def test_outside_window_skips(self):
        """Outside 9-21 → skip."""
        _create_pending()

        from server.cron import process_pending
        ok, fail = process_pending()

        assert (ok, fail) == (0, 0)

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_future_next_retry_at_not_sent(self, mock_gm):
        """next_retry_at in future → not picked up."""
        m = MagicMock()
        mock_gm.return_value = m

        _create_pending(next_retry_at=_future(6))

        from server.cron import process_pending
        ok, fail = process_pending()

        assert (ok, fail) == (0, 0)
        m.send_message.assert_not_called()

    @freeze_time(_IN)
    def test_no_pending(self):
        """No pending messages → (0, 0)."""
        from server.cron import process_pending
        ok, fail = process_pending()
        assert (ok, fail) == (0, 0)

    @freeze_time(_IN)
    @patch("server.cron.get_kommo_client")
    @patch("server.cron.get_messenger")
    def test_kommo_note_added_on_pending_success(self, mock_gm, mock_gc):
        """Successful pending send adds a Kommo note."""
        m = MagicMock()
        m.send_message.return_value = {"message_id": "wz-pn"}
        mock_gm.return_value = m
        kommo = MagicMock()
        mock_gc.return_value = kommo

        _create_pending()

        from server.cron import process_pending
        process_pending()

        kommo.add_note.assert_called_once()
        call_args = kommo.add_note.call_args
        assert call_args[0][0] == 100  # kommo_lead_id
        assert "отложенное" in call_args[0][1]

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_sent_not_picked_as_pending(self, mock_gm):
        """A 'sent' message is NOT processed by process_pending."""
        m = MagicMock()
        mock_gm.return_value = m

        _create_sent()

        from server.cron import process_pending
        ok, fail = process_pending()

        assert (ok, fail) == (0, 0)
        m.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:

    @freeze_time(_IN)
    @patch("server.cron.process_pending", return_value=(0, 0))
    @patch("server.cron.process_retries", return_value=(0, 0))
    def test_returns_0_on_success(self, mock_r, mock_p):
        from server.cron import main
        assert main() == 0
        mock_r.assert_called_once()
        mock_p.assert_called_once()

    @freeze_time(_IN)
    @patch("server.cron.process_retries", side_effect=RuntimeError("db gone"))
    def test_returns_1_on_fatal_error(self, mock_r):
        from server.cron import main
        assert main() == 1


# ---------------------------------------------------------------------------
# Retry lifecycle (integration-style)
# ---------------------------------------------------------------------------

class TestRetryLifecycle:
    """Full lifecycle: initial → retry 1 → retry 2 → max reached."""

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_full_lifecycle(self, mock_gm):
        n = 0

        def send(phone, data):
            nonlocal n
            n += 1
            return {"message_id": f"wz-lc-{n}"}

        m = MagicMock()
        m.send_message.side_effect = send
        mock_gm.return_value = m

        from server.cron import process_retries

        # Initial send (by webhook): attempts=1
        msg_id = _create_sent(attempts=1)

        # Retry 1: 1 → 2
        ok, _ = process_retries()
        assert ok == 1
        row = get_message_by_id(msg_id)
        assert row["attempts"] == 2

        # Move next_retry_at to past for next pickup
        update_message(msg_id, next_retry_at=_past(1))

        # Retry 2: 2 → 3
        ok, _ = process_retries()
        assert ok == 1
        row = get_message_by_id(msg_id)
        assert row["attempts"] == 3

        # Move to past again
        update_message(msg_id, next_retry_at=_past(1))

        # Attempt 3: attempts=3 >= MAX → NOT retried
        ok, _ = process_retries()
        assert ok == 0
        assert get_message_by_id(msg_id)["attempts"] == 3

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_failed_retry_still_counts(self, mock_gm):
        """Failed retry increments attempts, eventually reaching max."""
        m = MagicMock()
        m.send_message.side_effect = MessengerError("error")
        mock_gm.return_value = m

        from server.cron import process_retries

        msg_id = _create_sent(attempts=2)

        # Retry fails: 2 → 3, status=failed
        _, fail = process_retries()
        assert fail == 1
        row = get_message_by_id(msg_id)
        assert row["attempts"] == 3
        assert row["status"] == "failed"

        # Move to past
        update_message(msg_id, next_retry_at=_past(1))

        # attempts=3 >= MAX → NOT retried
        ok, fail = process_retries()
        assert (ok, fail) == (0, 0)

    @freeze_time(_IN)
    @patch("server.cron.get_messenger")
    def test_pending_to_sent_to_retry(self, mock_gm):
        """Pending → sent by process_pending → retried by process_retries."""
        n = 0

        def send(phone, data):
            nonlocal n
            n += 1
            return {"message_id": f"wz-ps-{n}"}

        m = MagicMock()
        m.send_message.side_effect = send
        mock_gm.return_value = m

        from server.cron import process_pending, process_retries

        msg_id = _create_pending()

        # Send pending
        ok, _ = process_pending()
        assert ok == 1
        row = get_message_by_id(msg_id)
        assert row["status"] == "sent"
        assert row["attempts"] == 1

        # Move next_retry_at to past
        update_message(msg_id, next_retry_at=_past(1))

        # Now eligible for retry
        ok, _ = process_retries()
        assert ok == 1
        row = get_message_by_id(msg_id)
        assert row["attempts"] == 2
