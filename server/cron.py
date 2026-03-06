"""Cron job: retry unacknowledged messages and send deferred (pending) ones.

Run:  python -m server.cron
Schedule: every hour via systemd timer or crontab.
"""

import dataclasses
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from server.alerts import get_alerter
from server.config import (
    MAX_RETRY_ATTEMPTS, PHONE_WHITELIST,
    RETRY_INTERVAL_HOURS, STOP_STATUSES, TEMPLATE_MAP, determine_line,
)
from server.db import (
    create_message,
    get_messages_for_retry,
    get_pending_messages,
    get_temporal_dedup,
    get_webhook_line_exists,
    init_db,
    update_message,
)
from server.kommo import KommoAPIError, get_kommo_client
from server.messenger import MessageData, MessengerError, get_messenger
from server.utils import (
    format_date_ru,
    get_next_send_window_start,
    is_in_send_window,
    weekday_name,
)

logger = logging.getLogger(__name__)

# T15 rule: for webhook lines (Г1/Б1), send no more than one message per (lead_id, line)
# for the deal lifecycle. Enforced by code-level checks + DB partial unique index.
_WEBHOOK_BACKFILL_TARGETS: tuple[tuple[int, int], ...] = (
    (10935879, 95514983),  # Бух Гос / Консультация проведена → gosniki_consultation_done
    (12154099, 93860331),  # Бух Бератер / Принято от 1й линии → berater_accepted
)


def _add_kommo_note(lead_id: int, line: str, note_type: str) -> None:
    """Add a note to the Kommo lead (non-critical, failures are logged)."""
    try:
        kommo = get_kommo_client()
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        kommo.add_note(
            lead_id,
            f"WhatsApp сообщение отправлено ({line}, {note_type}) — {now_str}",
        )
    except KommoAPIError as exc:
        logger.warning("Failed to add note to lead %d: %s", lead_id, exc)


def _build_message_data(msg) -> MessageData:
    """Restore MessageData from a DB row, including S02 template_values if present."""
    extra = {}
    try:
        tv = msg["template_values"]
    except IndexError:
        tv = None
    if tv:
        loaded = json.loads(tv)
        if isinstance(loaded, dict):
            # New format: keyed dict — robust, order-independent (temporal triggers)
            extra = loaded
        else:
            # Legacy list format (S01 / early S02 webhook records)
            keys = ("name", "institution", "weekday", "date")
            extra = dict(zip(keys, loaded))
    return MessageData(line=msg["line"], termin_date=msg["termin_date"], **extra)


def process_retries() -> tuple[int, int]:
    """Retry sent/failed messages whose next_retry_at has passed.

    Criteria (from ``db.get_messages_for_retry``):
      - status IN ('sent', 'failed')
      - attempts < MAX_RETRY_ATTEMPTS + 1  (< 3 by default)
      - next_retry_at <= now

    Returns (success_count, fail_count).
    """
    messages = get_messages_for_retry()
    logger.info("Retries: %d message(s) eligible", len(messages))

    if not messages:
        return 0, 0

    if not is_in_send_window():
        logger.info("Outside send window, skipping retries")
        return 0, 0

    messenger = get_messenger()
    max_attempts = MAX_RETRY_ATTEMPTS + 1
    success = 0
    failed = 0

    for msg in messages:
        msg_id = msg["id"]
        if PHONE_WHITELIST and msg["phone"] not in PHONE_WHITELIST:
            logger.info("Retry skip msg %d: phone not in whitelist", msg_id)
            continue
        new_attempts = msg["attempts"] + 1
        logger.info(
            "Retrying msg %d (attempt %d/%d)", msg_id, new_attempts, max_attempts,
        )

        try:
            message_data = _build_message_data(msg)
            result = messenger.send_message(msg["phone"], message_data)
        except (MessengerError, ValueError) as exc:
            logger.error("Retry failed for msg %d: %s", msg_id, exc)
            get_alerter().alert_messenger_error(msg["phone"], str(exc))
            next_retry = (
                datetime.now(tz=timezone.utc) + timedelta(hours=RETRY_INTERVAL_HOURS)
            ).isoformat(timespec="seconds")
            update_message(
                msg_id, status="failed", attempts=new_attempts,
                next_retry_at=next_retry,
            )
            failed += 1
            continue

        # Placeholder template (e.g. berater_day_minus_7): no message sent, no DB update.
        if result.get("status") == "skipped":
            logger.info("Retry skipped msg %d (line=%s, placeholder template)", msg_id, msg["line"])
            continue

        now = datetime.now(tz=timezone.utc)
        sent_at = now.isoformat(timespec="seconds")
        # Temporal reminders must fire exactly once: next_retry_at=None prevents
        # a second resend after a fail → retry-success path (H1-NEW fix).
        next_retry_at = (
            None if msg["line"] in _TEMPORAL_LINES
            else (now + timedelta(hours=RETRY_INTERVAL_HOURS)).isoformat(timespec="seconds")
        )

        update_message(
            msg_id,
            status="sent",
            attempts=new_attempts,
            sent_at=sent_at,
            next_retry_at=next_retry_at,
            messenger_id=result["message_id"],
        )
        _add_kommo_note(
            msg["kommo_lead_id"], msg["line"],
            f"повтор {new_attempts}/{max_attempts}",
        )
        success += 1
        logger.info("Retry OK for msg %d (messenger_id=%s)", msg_id, result["message_id"])

    return success, failed


def process_pending() -> tuple[int, int]:
    """Send messages that were deferred because they arrived outside the send window.

    Criteria (from ``db.get_pending_messages``):
      - status = 'pending'
      - next_retry_at <= now

    On success the message transitions to 'sent' with attempts=1.
    On failure attempts is incremented.  When attempts reaches
    MAX_RETRY_ATTEMPTS + 1 the message transitions to 'failed' and
    stops being retried (same cap as process_retries).

    Returns (success_count, fail_count).
    """
    messages = get_pending_messages()
    logger.info("Pending: %d message(s) eligible", len(messages))

    if not messages:
        return 0, 0

    if not is_in_send_window():
        logger.info("Outside send window, skipping pending")
        return 0, 0

    messenger = get_messenger()
    max_attempts = MAX_RETRY_ATTEMPTS + 1
    success = 0
    failed = 0

    for msg in messages:
        msg_id = msg["id"]
        if PHONE_WHITELIST and msg["phone"] not in PHONE_WHITELIST:
            logger.info("Pending skip msg %d: phone not in whitelist", msg_id)
            continue
        logger.info("Sending pending msg %d", msg_id)

        try:
            message_data = _build_message_data(msg)
            result = messenger.send_message(msg["phone"], message_data)
        except (MessengerError, ValueError) as exc:
            logger.error("Pending send failed for msg %d: %s", msg_id, exc)
            get_alerter().alert_messenger_error(msg["phone"], str(exc))
            new_attempts = msg["attempts"] + 1
            if new_attempts >= max_attempts:
                update_message(msg_id, status="failed", attempts=new_attempts)
                logger.warning(
                    "Pending msg %d reached max attempts (%d), marking failed",
                    msg_id, max_attempts,
                )
            else:
                # Push next_retry_at forward so we don't hammer Wazzup every cron run
                next_try = (
                    datetime.now(tz=timezone.utc) + timedelta(hours=RETRY_INTERVAL_HOURS)
                ).isoformat(timespec="seconds")
                update_message(
                    msg_id, attempts=new_attempts, next_retry_at=next_try,
                )
            failed += 1
            continue

        # Placeholder template (e.g. berater_day_minus_7): no message sent, no DB update.
        if result.get("status") == "skipped":
            logger.info("Pending skipped msg %d (line=%s, placeholder template)", msg_id, msg["line"])
            continue

        now = datetime.now(tz=timezone.utc)
        sent_at = now.isoformat(timespec="seconds")
        next_retry_at = (
            now + timedelta(hours=RETRY_INTERVAL_HOURS)
        ).isoformat(timespec="seconds")

        update_message(
            msg_id,
            status="sent",
            attempts=1,
            sent_at=sent_at,
            next_retry_at=next_retry_at,
            messenger_id=result["message_id"],
        )
        _add_kommo_note(msg["kommo_lead_id"], msg["line"], "отложенное")
        success += 1
        logger.info("Pending OK for msg %d (messenger_id=%s)", msg_id, result["message_id"])

    return success, failed


def process_webhook_backfill() -> tuple[int, int]:
    """Fail-safe backfill for webhook lines (Г1/Б1) missed by status_changed webhook."""
    kommo = get_kommo_client()
    messenger = get_messenger()
    created = 0
    failed = 0

    for pipeline_id, target_status_id in _WEBHOOK_BACKFILL_TARGETS:
        line = determine_line(pipeline_id, target_status_id)
        if line is None:
            logger.error(
                "Backfill config error: no line mapping for pipeline=%d status=%d",
                pipeline_id, target_status_id,
            )
            continue

        try:
            leads = kommo.get_active_leads(pipeline_id)
        except KommoAPIError as exc:
            logger.error(
                "Backfill: get_active_leads failed (pipeline=%d): %s",
                pipeline_id, exc,
            )
            get_alerter().alert_cron_error(
                f"Backfill get_active_leads failed for pipeline {pipeline_id}: {exc}",
            )
            continue

        logger.info(
            "Backfill: pipeline=%d line=%s active=%d",
            pipeline_id, line, len(leads),
        )

        for lead in leads:
            if lead.get("status_id") != target_status_id:
                continue

            lead_id_raw = lead.get("id")
            if lead_id_raw is None:
                logger.warning("Backfill: lead without id in pipeline=%d", pipeline_id)
                continue
            lead_id = int(lead_id_raw)

            if get_webhook_line_exists(lead_id, line):
                logger.debug("Backfill dedup: lead=%d line=%s already exists", lead_id, line)
                continue

            # Embedded contacts contain only IDs; fetch full contact for name/phone.
            try:
                contacts = (lead.get("_embedded") or {}).get("contacts") or []
                if not contacts:
                    raise KommoAPIError(f"Lead {lead_id} has no embedded contacts")
                main_contact = next((c for c in contacts if c.get("is_main")), contacts[0])
                contact_id = int(main_contact["id"])
                contact = kommo.get_contact(contact_id)
                name = kommo.extract_name(contact)
                if name is None:
                    raise KommoAPIError(
                        f"Name not found for contact {contact_id} (lead {lead_id})",
                    )
                phone = kommo.extract_phone(contact)
                if not phone:
                    raise KommoAPIError(
                        f"Phone not found for contact {contact_id} (lead {lead_id})",
                    )
            except (KeyError, TypeError, ValueError, KommoAPIError) as exc:
                logger.error("Backfill contact fetch failed for lead %d: %s", lead_id, exc)
                get_alerter().alert_kommo_error(lead_id, str(exc))
                continue

            if PHONE_WHITELIST and phone not in PHONE_WHITELIST:
                logger.info("Backfill skip lead %d: phone not in whitelist", lead_id)
                continue

            # termin_date="": Г1/Б1 templates use only {{1}}=name, no date variable.
            message_data = MessageData(line=line, termin_date="", name=name)
            template_values_json = json.dumps([name])
            message_text = messenger.build_message_text(message_data)

            if not is_in_send_window():
                next_retry_at = get_next_send_window_start()
                try:
                    create_message(
                        kommo_lead_id=lead_id,
                        kommo_contact_id=contact_id,
                        phone=phone,
                        line=line,
                        termin_date="",
                        message_text=message_text,
                        status="pending",
                        attempts=0,
                        next_retry_at=next_retry_at,
                        template_values=template_values_json,
                    )
                except sqlite3.IntegrityError:
                    logger.warning(
                        "Backfill dedup race (pending): lead=%d line=%s",
                        lead_id, line,
                    )
                    continue
                created += 1
                logger.info(
                    "Backfill scheduled pending lead=%d line=%s next_retry_at=%s",
                    lead_id, line, next_retry_at,
                )
                continue

            # Record-before-send: reserve DB slot as "pending" BEFORE sending.
            # If IntegrityError here, no WhatsApp was sent => safe dedup.
            try:
                msg_id = create_message(
                    kommo_lead_id=lead_id,
                    kommo_contact_id=contact_id,
                    phone=phone,
                    line=line,
                    termin_date="",
                    message_text=message_text,
                    status="pending",
                    attempts=0,
                    next_retry_at=None,
                    template_values=template_values_json,
                )
            except sqlite3.IntegrityError:
                logger.warning(
                    "Backfill dedup race (reserve): lead=%d line=%s",
                    lead_id, line,
                )
                continue

            try:
                result = messenger.send_message(phone, message_data)
            except MessengerError as exc:
                logger.error("Backfill messenger error for lead %d: %s", lead_id, exc)
                get_alerter().alert_messenger_error(phone, str(exc))
                next_retry_at = (
                    datetime.now(tz=timezone.utc) + timedelta(hours=RETRY_INTERVAL_HOURS)
                ).isoformat(timespec="seconds")
                update_message(
                    msg_id,
                    status="failed",
                    attempts=1,
                    next_retry_at=next_retry_at,
                )
                failed += 1
                continue

            # Currently unreachable: both Г1/Б1 have real WABA GUIDs.
            # Added for consistency with process_retries() and process_pending().
            if result.get("status") == "skipped":
                logger.info(
                    "Backfill skipped lead=%d line=%s (placeholder template)",
                    lead_id, line,
                )
                continue

            now = datetime.now(tz=timezone.utc)
            sent_at = now.isoformat(timespec="seconds")
            next_retry_at = (
                now + timedelta(hours=RETRY_INTERVAL_HOURS)
            ).isoformat(timespec="seconds")
            update_message(
                msg_id,
                status="sent",
                attempts=1,
                sent_at=sent_at,
                next_retry_at=next_retry_at,
                messenger_id=result["message_id"],
            )
            _add_kommo_note(lead_id, line, "backfill")
            created += 1
            logger.info(
                "Backfill sent lead=%d line=%s messenger_id=%s",
                lead_id, line, result["message_id"],
            )

    return created, failed


_BERLIN_TZ = ZoneInfo("Europe/Berlin")

# Maps days_until → temporal line name.
_DAYS_TO_LINE: dict[int, str] = {
    7: "berater_day_minus_7",
    3: "berater_day_minus_3",
    1: "berater_day_minus_1",
    0: "berater_day_0",
}

# Lines that must fire exactly once — no re-send after a fail→retry-success cycle.
_TEMPORAL_LINES: frozenset[str] = frozenset(_DAYS_TO_LINE.values())

_BERATER_PIPELINE_ID = 12154099


def process_temporal_triggers() -> None:
    """Send temporal WhatsApp reminders for Бух Бератер leads.

    Runs every hour after process_retries/process_pending.
    Checks ДЦ (field 887026) and АА (field 887028) dates for each
    active lead and sends reminders at -7, -3, -1 and 0 days.

    - СТОП-статусы block both ДЦ and АА for the lead.
    - berater_day_minus_7 is a placeholder (no WABA GUID): logged, skipped.
    - Dedup: one message per (lead_id, line, termin_date).
    - On MessengerError: status='failed' in DB; process_retries() will retry.
    """
    if not is_in_send_window():
        logger.info("Outside send window, skipping temporal triggers")
        return

    kommo = get_kommo_client()
    try:
        leads = kommo.get_active_leads(_BERATER_PIPELINE_ID)
    except KommoAPIError as exc:
        logger.critical("Temporal triggers: get_active_leads failed: %s", exc)
        get_alerter().alert_cron_error(f"CRITICAL: get_active_leads failed: {exc}")
        return

    logger.info("Temporal triggers: processing %d leads", len(leads))
    today = datetime.now(tz=_BERLIN_TZ).date()
    messenger = get_messenger()
    stop_statuses = STOP_STATUSES.get(_BERATER_PIPELINE_ID, set())
    # Pairs of (extractor callable, institution name) — processed independently per lead.
    termin_fields = [
        (kommo.extract_termin_date_dc, "Jobcenter"),
        (kommo.extract_termin_date_aa, "Agentur für Arbeit"),
    ]

    for lead in leads:
        lead_id = lead.get("id")

        # СТОП-проверка: этапы "отменён/перенесён" блокируют оба термина
        if lead.get("status_id") in stop_statuses:
            logger.debug("Lead %d on STOP status %d, skipping", lead_id, lead.get("status_id"))
            continue

        for extract_date, institution in termin_fields:
            termin_date_obj = extract_date(lead)

            if termin_date_obj is None:
                continue

            days_until = (termin_date_obj - today).days
            line = _DAYS_TO_LINE.get(days_until)
            if line is None:
                continue

            # berater_day_minus_7: placeholder GUID, log and skip (no DB record)
            if TEMPLATE_MAP[line]["template_guid"] is None:
                termin_date_str = format_date_ru(termin_date_obj)
                logger.info(
                    "Temporal berater_day_minus_7: placeholder GUID, skipping "
                    "(lead_id=%d, termin_date=%s)", lead_id, termin_date_str,
                )
                continue

            termin_date_str = format_date_ru(termin_date_obj)

            # Дедупликация: одно сообщение на (lead_id, line, termin_date)
            if get_temporal_dedup(lead_id, line, termin_date_str):
                logger.debug(
                    "Dedup: lead=%d line=%s termin_date=%s already sent",
                    lead_id, line, termin_date_str,
                )
                continue

            # Получить контакт (телефон + имя)
            try:
                contacts = (lead.get("_embedded") or {}).get("contacts") or []
                if not contacts:
                    raise KommoAPIError(f"Lead {lead_id} has no embedded contacts")
                main_contact = next((c for c in contacts if c.get("is_main")), contacts[0])
                contact_id = main_contact["id"]
                contact = kommo.get_contact(contact_id)
                name = kommo.extract_name(contact)
                if name is None:
                    raise KommoAPIError(
                        f"Name not found for contact {contact_id} (lead {lead_id})"
                    )
                phone = kommo.extract_phone(contact)
                if not phone:
                    raise KommoAPIError(
                        f"Phone not found for contact {contact_id} (lead {lead_id})"
                    )
            except KommoAPIError as exc:
                logger.error("Failed to get contact for lead %d: %s", lead_id, exc)
                get_alerter().alert_kommo_error(lead_id, str(exc))
                continue

            # Phone whitelist (test mode)
            if PHONE_WHITELIST and phone not in PHONE_WHITELIST:
                logger.info("Phone not in whitelist, skipping lead %d", lead_id)
                continue

            # Собрать MessageData
            message_data = MessageData(
                line=line,
                termin_date=termin_date_str,
                name=name,
                institution=institution,
                weekday=weekday_name(termin_date_obj),
                date=termin_date_str,
            )

            # Вычислить template_values для отправки в Wazzup
            template_values_list = TEMPLATE_MAP[line]["vars"](**dataclasses.asdict(message_data))
            # Store MessageData extra fields as a keyed dict for robust retry restore (M2 fix).
            # template_values_list is passed to build_message_text below;
            # send_message() recomputes vars internally for the Wazzup API payload.
            template_values_json = json.dumps({
                "name": name,
                "institution": institution,
                "weekday": weekday_name(termin_date_obj),
                "date": termin_date_str,
            })
            message_text = messenger.build_message_text(
                message_data, template_values=template_values_list,
            )

            # Отправить
            try:
                result = messenger.send_message(phone, message_data)
            except MessengerError as exc:
                logger.error(
                    "MessengerError for lead %d (line=%s): %s", lead_id, line, exc,
                )
                get_alerter().alert_messenger_error(phone, str(exc))
                now = datetime.now(tz=timezone.utc)
                try:
                    create_message(
                        kommo_lead_id=lead_id,
                        kommo_contact_id=contact["id"],
                        phone=phone,
                        line=line,
                        termin_date=termin_date_str,
                        message_text=message_text,
                        status="failed",
                        attempts=1,
                        next_retry_at=(
                            now + timedelta(hours=RETRY_INTERVAL_HOURS)
                        ).isoformat(timespec="seconds"),
                        template_values=template_values_json,
                    )
                except sqlite3.IntegrityError:
                    logger.warning(
                        "Dedup race (failed): record already exists "
                        "(lead=%d, line=%s, termin_date=%s)", lead_id, line, termin_date_str,
                    )
                continue

            # Успех — сохранить в БД и добавить примечание в Kommo
            now = datetime.now(tz=timezone.utc)
            sent_at = now.isoformat(timespec="seconds")
            # next_retry_at=None: temporal reminders must fire exactly once —
            # process_retries() skips rows where next_retry_at IS NULL (H1 fix).
            try:
                msg_id = create_message(
                    kommo_lead_id=lead_id,
                    kommo_contact_id=contact["id"],
                    phone=phone,
                    line=line,
                    termin_date=termin_date_str,
                    message_text=message_text,
                    status="sent",
                    attempts=1,
                    sent_at=sent_at,
                    next_retry_at=None,
                    messenger_id=result["message_id"],
                    template_values=template_values_json,
                )
            except sqlite3.IntegrityError:
                logger.warning(
                    "Dedup race (sent): record already exists "
                    "(lead=%d, line=%s, termin_date=%s)", lead_id, line, termin_date_str,
                )
                continue
            _add_kommo_note(lead_id, line, "temporal")
            logger.info(
                "Temporal msg %d sent to lead %d (line=%s, termin_date=%s)",
                msg_id, lead_id, line, termin_date_str,
            )


def main() -> int:
    """Entry point. Returns 0 on success, 1 on unhandled error."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Cron started")

    init_db()

    try:
        retry_ok, retry_fail = process_retries()
        pending_ok, pending_fail = process_pending()
        backfill_created, backfill_failed = process_webhook_backfill()
        process_temporal_triggers()
    except Exception as exc:
        logger.exception("Cron fatal error")
        get_alerter().alert_cron_error(str(exc))
        return 1

    logger.info(
        "Cron finished: retries %d ok / %d fail, pending %d ok / %d fail, "
        "backfill %d created / %d failed",
        retry_ok, retry_fail, pending_ok, pending_fail, backfill_created, backfill_failed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
