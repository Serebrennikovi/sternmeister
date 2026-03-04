"""Cron job: retry unacknowledged messages and send deferred (pending) ones.

Run:  python -m server.cron
Schedule: every hour via systemd timer or crontab.
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from server.alerts import get_alerter
from server.config import MAX_RETRY_ATTEMPTS, PHONE_WHITELIST, RETRY_INTERVAL_HOURS
from server.db import (
    get_messages_for_retry,
    get_pending_messages,
    init_db,
    update_message,
)
from server.kommo import KommoAPIError, get_kommo_client
from server.messenger import MessageData, MessengerError, get_messenger
from server.utils import is_in_send_window

logger = logging.getLogger(__name__)


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
        vals = json.loads(tv)
        keys = ("name", "institution", "weekday", "date")
        extra = dict(zip(keys, vals))
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
        next_retry_at = (
            now + timedelta(hours=RETRY_INTERVAL_HOURS)
        ).isoformat(timespec="seconds")

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
    except Exception as exc:
        logger.exception("Cron fatal error")
        get_alerter().alert_cron_error(str(exc))
        return 1

    logger.info(
        "Cron finished: retries %d ok / %d fail, pending %d ok / %d fail",
        retry_ok, retry_fail, pending_ok, pending_fail,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
