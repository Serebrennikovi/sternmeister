"""Integration tests for T15 webhook backfill fail-safe."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from server.db import _get_conn, get_messages, init_db


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM messages")
        conn.commit()
    finally:
        conn.close()


def _make_contact(contact_id: int, name: str = "Иван Иванов", phone: str = "+4917612345678") -> dict:
    return {
        "id": contact_id,
        "name": name,
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": phone}]},
        ],
    }


def _make_lead(lead_id: int, pipeline_id: int, status_id: int, contact_id: int) -> dict:
    return {
        "id": lead_id,
        "pipeline_id": pipeline_id,
        "status_id": status_id,
        "custom_fields_values": [],
        "_embedded": {"contacts": [{"id": contact_id, "is_main": True}]},
    }


@freeze_time("2026-03-06 10:00:00", tz_offset=0)  # 11:00 Berlin, inside send window
def test_backfill_gosniki_sent_and_idempotent():
    """Backfill sends missing Г1 line and second run does not create duplicates."""
    from server.cron import process_webhook_backfill

    lead = _make_lead(701, 10935879, 95514983, contact_id=801)

    kommo = MagicMock()

    def get_active(pipeline_id):
        if pipeline_id == 10935879:
            return [lead]
        return []

    kommo.get_active_leads.side_effect = get_active
    kommo.get_contact.return_value = _make_contact(801, name="Мария Шмидт")
    kommo.extract_name.return_value = "Мария Шмидт"
    kommo.extract_phone.return_value = "+4917612345678"

    messenger = MagicMock()
    messenger.build_message_text.return_value = "[template] Мария Шмидт"
    messenger.send_message.return_value = {"message_id": "wz-backfill-1", "status": "sent"}

    with (
        patch("server.cron.get_kommo_client", return_value=kommo),
        patch("server.cron.get_messenger", return_value=messenger),
        patch("server.cron.get_alerter", return_value=MagicMock()),
        patch("server.cron.PHONE_WHITELIST", None),
    ):
        created1, failed1 = process_webhook_backfill()
        created2, failed2 = process_webhook_backfill()

    assert (created1, failed1) == (1, 0)
    assert (created2, failed2) == (0, 0)

    msgs = get_messages(kommo_lead_id=701)
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["line"] == "gosniki_consultation_done"
    assert msg["status"] == "sent"
    assert msg["messenger_id"] == "wz-backfill-1"
    values = json.loads(msg["template_values"])
    assert values["name"] == "Мария Шмидт"
    assert "Мария Шмидт" in values["news_text"]


@freeze_time("2026-03-06 22:00:00", tz_offset=0)  # 23:00 Berlin, outside send window
def test_backfill_gosniki_outside_window_creates_pending():
    """Outside send window, backfill creates pending with next 08:00 Berlin."""
    from server.cron import process_webhook_backfill

    lead = _make_lead(702, 10935879, 95514983, contact_id=802)

    kommo = MagicMock()
    kommo.get_active_leads.side_effect = lambda pipeline_id: [lead] if pipeline_id == 10935879 else []
    kommo.get_contact.return_value = _make_contact(802, name="Анна Мюллер")
    kommo.extract_name.return_value = "Анна Мюллер"
    kommo.extract_phone.return_value = "+4917612345678"

    messenger = MagicMock()
    messenger.build_message_text.return_value = "[template] Анна Мюллер"

    with (
        patch("server.cron.get_kommo_client", return_value=kommo),
        patch("server.cron.get_messenger", return_value=messenger),
        patch("server.cron.get_alerter", return_value=MagicMock()),
        patch("server.cron.PHONE_WHITELIST", None),
    ):
        created, failed = process_webhook_backfill()

    assert (created, failed) == (1, 0)
    messenger.send_message.assert_not_called()

    msgs = get_messages(kommo_lead_id=702)
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["status"] == "pending"
    assert msg["next_retry_at"] is not None
    assert msg["next_retry_at"].startswith("2026-03-07T07:00:00")  # 08:00 Berlin (CET)
    assert msg["line"] == "gosniki_consultation_done"
    values = json.loads(msg["template_values"])
    assert "news_text" in values


@freeze_time("2026-03-06 10:00:00", tz_offset=0)  # 11:00 Berlin, inside send window
def test_backfill_berater_accepted_sent_and_idempotent():
    """Backfill sends missing Б1 and uses the same DC/AA priority path as webhook."""
    from server.cron import process_webhook_backfill

    lead = _make_lead(801, 12154099, 93860331, contact_id=901)

    kommo = MagicMock()

    def get_active(pipeline_id):
        if pipeline_id == 12154099:
            return [lead]
        return []

    kommo.get_active_leads.side_effect = get_active
    kommo.get_contact.return_value = _make_contact(901, name="Карл Шульц")
    kommo.extract_name.return_value = "Карл Шульц"
    kommo.extract_phone.return_value = "+4917699999999"
    kommo.extract_termin_date_dc.return_value = date(2026, 3, 20)
    kommo.extract_termin_date_aa.return_value = date(2026, 3, 12)
    kommo.extract_time_termin.return_value = " 14:45 "

    messenger = MagicMock()
    messenger.build_message_text.return_value = "[template] Карл Шульц"
    messenger.send_message.return_value = {"message_id": "wz-backfill-b1", "status": "sent"}

    with (
        patch("server.cron.get_kommo_client", return_value=kommo),
        patch("server.cron.get_messenger", return_value=messenger),
        patch("server.cron.get_alerter", return_value=MagicMock()),
        patch("server.cron.PHONE_WHITELIST", None),
    ):
        created1, failed1 = process_webhook_backfill()
        created2, failed2 = process_webhook_backfill()

    assert (created1, failed1) == (1, 0)
    assert (created2, failed2) == (0, 0)

    msgs = get_messages(kommo_lead_id=801)
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["line"] == "berater_accepted"
    assert msg["status"] == "sent"
    assert msg["messenger_id"] == "wz-backfill-b1"
    values = json.loads(msg["template_values"])
    assert values == {"name": "Карл Шульц"}


@freeze_time("2026-03-10 10:00:00", tz_offset=0)
def test_backfill_berater_accepted_with_active_temporal_state_is_marked_skipped():
    """Backfill must store a stale-skip marker instead of sending outdated Б1."""
    from server.cron import process_webhook_backfill

    lead = _make_lead(802, 12154099, 93860331, contact_id=902)

    kommo = MagicMock()
    kommo.get_active_leads.side_effect = lambda pipeline_id: [lead] if pipeline_id == 12154099 else []
    kommo.get_contact.return_value = _make_contact(902, name="Лена Фишер")
    kommo.extract_name.return_value = "Лена Фишер"
    kommo.extract_phone.return_value = "+4917699999999"
    kommo.extract_termin_date_dc.return_value = None
    kommo.extract_termin_date_aa.return_value = date(2026, 3, 13)  # +3 days
    kommo.extract_time_termin.return_value = None

    messenger = MagicMock()
    messenger.build_message_text.return_value = "[template] Лена Фишер"

    with (
        patch("server.cron.get_kommo_client", return_value=kommo),
        patch("server.cron.get_messenger", return_value=messenger),
        patch("server.cron.get_alerter", return_value=MagicMock()),
        patch("server.cron.PHONE_WHITELIST", None),
    ):
        created, failed = process_webhook_backfill()

    assert (created, failed) == (0, 0)
    messenger.send_message.assert_not_called()

    msgs = get_messages(kommo_lead_id=802)
    assert len(msgs) == 1
    assert msgs[0]["status"] == "failed"
