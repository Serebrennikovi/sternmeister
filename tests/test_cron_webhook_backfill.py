"""Unit tests for T15 webhook backfill fail-safe in server.cron."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from server.db import _get_conn, init_db


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM messages")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def _mock_alerter():
    with patch("server.cron.get_alerter") as mock:
        mock.return_value = MagicMock()
        yield mock


def _make_lead(lead_id: int, pipeline_id: int, status_id: int, contact_id: int = 200):
    return {
        "id": lead_id,
        "pipeline_id": pipeline_id,
        "status_id": status_id,
        "_embedded": {"contacts": [{"id": contact_id, "is_main": True}]},
        "custom_fields_values": [],
    }


@freeze_time("2026-03-06 10:00:00", tz_offset=0)
@patch("server.cron.is_in_send_window", return_value=True)
@patch("server.cron.get_messenger")
@patch("server.cron.get_kommo_client")
def test_backfill_filters_by_target_status_id(mock_gc, mock_gm, _mock_window, _mock_alerter):
    """Only leads in target status are processed for a pipeline."""
    from server.cron import process_webhook_backfill

    kommo = MagicMock()

    def get_active(pipeline_id):
        if pipeline_id == 10935879:
            return [
                _make_lead(1, 10935879, 95514983, contact_id=201),  # target
                _make_lead(2, 10935879, 95514984, contact_id=202),  # non-target
            ]
        return []

    kommo.get_active_leads.side_effect = get_active
    kommo.get_contact.side_effect = lambda cid: {
        "id": cid,
        "name": "Иван",
        "custom_fields_values": [{"field_code": "PHONE", "values": [{"value": "+4917612345678"}]}],
    }
    kommo.extract_name.return_value = "Иван"
    kommo.extract_phone.return_value = "+4917612345678"
    mock_gc.return_value = kommo

    messenger = MagicMock()
    messenger.build_message_text.return_value = "[template] Иван"
    messenger.send_message.return_value = {"message_id": "wz-1"}
    mock_gm.return_value = messenger

    with patch("server.cron.get_webhook_line_exists", return_value=False), \
         patch("server.cron.create_message", return_value=11) as mock_create, \
         patch("server.cron.update_message") as mock_update, \
         patch("server.cron._add_kommo_note"):
        created, failed = process_webhook_backfill()

    assert (created, failed) == (1, 0)
    # create_message called once (reserve as pending)
    assert mock_create.call_count == 1
    call_kw = mock_create.call_args.kwargs
    assert call_kw["kommo_lead_id"] == 1
    assert call_kw["line"] == "gosniki_consultation_done"
    assert call_kw["status"] == "pending"
    # update_message called once (pending → sent)
    assert mock_update.call_count == 1
    upd_kw = mock_update.call_args[1]
    assert upd_kw["status"] == "sent"


@freeze_time("2026-03-06 10:00:00", tz_offset=0)
@patch("server.cron.is_in_send_window", return_value=True)
@patch("server.cron.get_messenger")
@patch("server.cron.get_kommo_client")
def test_backfill_dedup_get_webhook_line_exists_skips(mock_gc, mock_gm, _mock_window, _mock_alerter):
    """If DB already has (lead_id, line), backfill does not send/create duplicate."""
    from server.cron import process_webhook_backfill

    kommo = MagicMock()
    kommo.get_active_leads.side_effect = lambda pid: [
        _make_lead(10, pid, 95514983 if pid == 10935879 else 93860331)
    ]
    mock_gc.return_value = kommo

    messenger = MagicMock()
    mock_gm.return_value = messenger

    with patch("server.cron.get_webhook_line_exists", return_value=True), \
         patch("server.cron.create_message") as mock_create:
        created, failed = process_webhook_backfill()

    assert (created, failed) == (0, 0)
    mock_create.assert_not_called()
    messenger.send_message.assert_not_called()


@freeze_time("2026-03-06 10:00:00", tz_offset=0)
@patch("server.cron.is_in_send_window", return_value=True)
@patch("server.cron.get_messenger")
@patch("server.cron.get_kommo_client")
def test_backfill_integrityerror_is_handled_and_continues(
    mock_gc, mock_gm, _mock_window, _mock_alerter,
):
    """IntegrityError on create_message (reserve step) is treated as dedup race.

    After H2 fix: IntegrityError happens BEFORE send, so the deduped lead
    does NOT receive a WhatsApp message. Processing continues for next lead.
    """
    from server.cron import process_webhook_backfill

    kommo = MagicMock()

    def get_active(pipeline_id):
        if pipeline_id == 10935879:
            return [
                _make_lead(20, 10935879, 95514983, contact_id=301),
                _make_lead(21, 10935879, 95514983, contact_id=302),
            ]
        return []

    kommo.get_active_leads.side_effect = get_active
    kommo.get_contact.side_effect = lambda cid: {
        "id": cid,
        "name": "Анна",
        "custom_fields_values": [{"field_code": "PHONE", "values": [{"value": "+4917612345678"}]}],
    }
    kommo.extract_name.return_value = "Анна"
    kommo.extract_phone.return_value = "+4917612345678"
    mock_gc.return_value = kommo

    messenger = MagicMock()
    messenger.build_message_text.return_value = "[template] Анна"
    messenger.send_message.return_value = {"message_id": "wz-ok"}
    mock_gm.return_value = messenger

    with patch("server.cron.get_webhook_line_exists", return_value=False), \
         patch("server.cron.create_message") as mock_create, \
         patch("server.cron.update_message") as mock_update, \
         patch("server.cron._add_kommo_note"):
        # First lead: IntegrityError on create (reserve step) → no send
        # Second lead: create succeeds → send → update
        mock_create.side_effect = [sqlite3.IntegrityError("dup"), 22]
        created, failed = process_webhook_backfill()

    assert (created, failed) == (1, 0)
    # Only 1 send (lead 21), NOT 2 — lead 20 deduped before send
    assert messenger.send_message.call_count == 1
    # update_message called once for the successful lead (pending → sent)
    assert mock_update.call_count == 1


@freeze_time("2026-03-06 10:00:00", tz_offset=0)
@patch("server.cron.is_in_send_window", return_value=True)
@patch("server.cron.get_messenger")
@patch("server.cron.get_kommo_client")
def test_backfill_messenger_error_creates_failed_record(
    mock_gc, mock_gm, _mock_window, _mock_alerter,
):
    """MessengerError during backfill: record reserved as pending, then updated to failed.

    After H2 fix: create_message(pending) succeeds, send_message raises,
    update_message marks as failed with next_retry_at. Processing continues.
    """
    from server.cron import process_webhook_backfill
    from server.messenger import MessengerError

    kommo = MagicMock()

    def get_active(pipeline_id):
        if pipeline_id == 10935879:
            return [
                _make_lead(30, 10935879, 95514983, contact_id=401),  # will fail
                _make_lead(31, 10935879, 95514983, contact_id=402),  # will succeed
            ]
        return []

    kommo.get_active_leads.side_effect = get_active
    kommo.get_contact.side_effect = lambda cid: {
        "id": cid,
        "name": "Тест",
        "custom_fields_values": [{"field_code": "PHONE", "values": [{"value": "+4917600000000"}]}],
    }
    kommo.extract_name.return_value = "Тест"
    kommo.extract_phone.return_value = "+4917600000000"
    mock_gc.return_value = kommo

    messenger = MagicMock()
    messenger.build_message_text.return_value = "[template] Тест"
    # First call: MessengerError, second call: success
    messenger.send_message.side_effect = [
        MessengerError("Wazzup timeout"),
        {"message_id": "wz-ok-31"},
    ]
    mock_gm.return_value = messenger

    with patch("server.cron.get_webhook_line_exists", return_value=False), \
         patch("server.cron.create_message") as mock_create, \
         patch("server.cron.update_message") as mock_update, \
         patch("server.cron._add_kommo_note"):
        mock_create.side_effect = [50, 51]  # msg_ids for both leads
        created, failed = process_webhook_backfill()

    assert (created, failed) == (1, 1)
    assert messenger.send_message.call_count == 2

    # First update_message: lead 30 → failed
    fail_call = mock_update.call_args_list[0]
    assert fail_call[0][0] == 50  # msg_id
    assert fail_call[1]["status"] == "failed"
    assert fail_call[1]["attempts"] == 1
    assert "next_retry_at" in fail_call[1]

    # Second update_message: lead 31 → sent
    sent_call = mock_update.call_args_list[1]
    assert sent_call[0][0] == 51
    assert sent_call[1]["status"] == "sent"

