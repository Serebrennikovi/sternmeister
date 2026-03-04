import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from server.alerts import get_alerter
from server.config import (
    DEDUP_WINDOW_MINUTES, FIELD_IDS, KOMMO_WEBHOOK_SECRET,
    PHONE_WHITELIST, RETRY_INTERVAL_HOURS, SEND_WINDOW_START,
    SEND_WINDOW_END, determine_line,
)
from server.db import create_message, get_failed_temporal_count, get_recent_message, init_db
from server.kommo import KommoAPIError, get_kommo_client
from server.messenger import MessageData, MessengerError, get_messenger
from server.utils import (
    get_next_send_window_start, is_in_send_window, parse_bracket_form,
)

logger = logging.getLogger(__name__)

# Priority order: generic "Дата термина" first, then DC-specific, then AA.
_TERMIN_FIELD_IDS = (
    FIELD_IDS["date_termin"],
    FIELD_IDS["date_termin_dc"],
    FIELD_IDS["date_termin_aa"],
)

# Lines where termin_date is optional (шаблон не использует дату как переменную),
# но name является обязательным ({{1}}=имя в шаблоне).
_TERMIN_OPTIONAL_LINES = {"gosniki_consultation_done", "berater_accepted"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="WhatsApp Auto-notifications", lifespan=lifespan)


_BERLIN_TZ = ZoneInfo("Europe/Berlin")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    now_utc = datetime.now(tz=timezone.utc)
    now_berlin = now_utc.astimezone(_BERLIN_TZ)
    failed_temporal = get_failed_temporal_count()
    return JSONResponse({
        "status": "ok",
        "send_window": f"{SEND_WINDOW_START}-{SEND_WINDOW_END}",
        "in_window": is_in_send_window(),
        "server_time_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "server_time_berlin": now_berlin.strftime("%Y-%m-%d %H:%M:%S"),
        "failed_temporal": failed_temporal,
    })


async def _parse_webhook_payload(request: Request) -> dict[str, Any]:
    """Parse Kommo webhook payload from either form data or JSON.

    Kommo sends ``application/x-www-form-urlencoded`` with PHP bracket
    notation (``leads[status][0][id]=123``).  For manual testing with
    ``curl -H 'Content-Type: application/json'`` we also accept JSON.
    """
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if "application/x-www-form-urlencoded" in content_type:
        logger.debug("Webhook form body (%.500s)", body)
        try:
            return parse_bracket_form(body)
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            logger.warning("Failed to parse form-encoded webhook body: %s", exc)
            return {}
    logger.debug("Webhook body (%s): %.500s", content_type, body)
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        logger.warning("Failed to parse webhook body as JSON: %s", exc)
        return {}


@app.post("/webhook/kommo")
def kommo_webhook(
    payload: dict[str, Any] = Depends(_parse_webhook_payload),
    secret: str = Query(default=""),
):
    """Webhook from Kommo CRM on pipeline status change.

    Sync handler — runs in threadpool, safe for blocking I/O
    (requests, sqlite3, time.sleep in retry logic).

    Security: if KOMMO_WEBHOOK_SECRET is configured, the webhook URL
    must include ``?secret=<value>``.  Kommo standard webhooks don't
    send HMAC headers, so we use a shared secret in the URL instead.
    Configure the URL in Kommo as:
        https://shternmeister.ngrok.pro/webhook/kommo?secret=YOUR_SECRET

    TODO: add rate limiting (e.g. slowapi) to protect against flood.
    Currently protected by secret-in-URL only.
    """
    # 0. Validate webhook secret (compare_digest: constant-time to prevent timing attacks)
    if KOMMO_WEBHOOK_SECRET:
        if not hmac.compare_digest(secret, KOMMO_WEBHOOK_SECRET):
            logger.warning("Webhook rejected: invalid secret")
            return JSONResponse(
                {"status": "error", "message": "Forbidden"},
                status_code=403,
            )

    # 1. Validate payload
    leads = payload.get("leads")
    if not leads or "status" not in leads:
        return JSONResponse({"status": "ok", "message": "Not a status change event"})

    status_list = leads["status"]
    if not status_list:
        return JSONResponse({"status": "ok", "message": "Empty status list"})

    # Process each lead status change (Kommo may batch multiple in one webhook)
    results = []
    for lead_status in status_list:
        result = _process_lead_status(lead_status)
        results.append(result)

    return JSONResponse({"status": "ok", "results": results})


def _process_lead_status(lead_status: dict) -> dict:
    """Process a single lead status change entry from Kommo webhook.

    Returns a result dict (not JSONResponse) for the caller to wrap.
    All exceptions are caught to honour the "always 200" contract.
    """
    try:
        return _process_lead_status_inner(lead_status)
    except Exception as exc:
        logger.exception("Unexpected error processing lead status: %s", exc)
        get_alerter().alert_unexpected_error(str(exc))
        return {"status": "error", "message": "Internal error"}


def _process_lead_status_inner(lead_status: dict) -> dict:
    """Inner implementation — may raise; caller catches everything."""
    try:
        lead_id = int(lead_status["id"])
        status_id = int(lead_status["status_id"])
        pipeline_id = int(lead_status["pipeline_id"])
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Invalid webhook payload: %s", exc)
        return {"status": "error", "message": "Invalid payload structure"}

    # 2. Determine line
    line = determine_line(pipeline_id, status_id)
    if line is None:
        return {"status": "ok", "message": "Status not relevant"}

    logger.info(
        "Webhook: lead=%d pipeline=%d status=%d -> line=%s",
        lead_id, pipeline_id, status_id, line,
    )

    # 2b. Deduplication: skip if same lead+line was processed recently.
    # Note: this is a read-then-write pattern without a lock, so
    # theoretically two concurrent webhooks for the same lead+line
    # could both pass the check.  At ~60 msgs/day this is negligible;
    # worst case is a duplicate message, not a system failure.
    existing = get_recent_message(lead_id, line, within_minutes=DEDUP_WINDOW_MINUTES)
    if existing:
        logger.info(
            "Duplicate webhook for lead=%d line=%s (msg %d at %s), skipping",
            lead_id, line, existing["id"], existing["created_at"],
        )
        return {
            "status": "ok",
            "message": "Duplicate webhook, already processed",
            "existing_message_id": existing["id"],
        }

    # 3. Get lead + contact from Kommo
    kommo = get_kommo_client()
    try:
        lead, contact = kommo.get_lead_contact(lead_id)
    except KommoAPIError as exc:
        logger.error("Kommo API error for lead %d: %s", lead_id, exc)
        get_alerter().alert_kommo_error(lead_id, str(exc))
        return {"status": "error", "message": f"Kommo API error: {exc}"}

    raw_contact_id = contact.get("id")
    if raw_contact_id is None:
        logger.error("Contact missing 'id' for lead %d", lead_id)
        return {"status": "error", "message": "Contact missing id"}
    contact_id = int(raw_contact_id)

    # 4. Extract phone
    phone = kommo.extract_phone(contact)
    if not phone:
        logger.warning("No phone for lead %d contact %d", lead_id, contact_id)
        get_alerter().send_alert(
            f"Телефон не найден для lead {lead_id}", level="WARNING",
        )
        return {"status": "error", "message": "Phone not found in contact"}

    # 4b. Phone whitelist check (testing mode)
    if PHONE_WHITELIST and phone not in PHONE_WHITELIST:
        logger.info("Phone %s not in whitelist, skipping lead %d", phone, lead_id)
        return {"status": "ok", "message": "Phone not in whitelist (test mode)"}

    # 5. Extract termin date (try all date fields).
    # For lines in _TERMIN_OPTIONAL_LINES: date is optional (template doesn't use it),
    # continue with "" if not found.
    # For other lines: date is required; skip with error if not found.
    termin_date = None
    for fid in _TERMIN_FIELD_IDS:
        termin_date = kommo.extract_termin_date(lead, fid)
        if termin_date:
            break
    if not termin_date:
        if line in _TERMIN_OPTIONAL_LINES:
            termin_date = ""  # Template doesn't use date; proceed
        else:
            logger.warning("No termin date for lead %d, cannot send notification", lead_id)
            get_alerter().send_alert(
                f"Дата термина не найдена для lead {lead_id}", level="WARNING",
            )
            return {"status": "error", "message": "Termin date not found in lead"}

    # 5b. For Г1/Б1: extract client name ({{1}} in template — required).
    name = None
    template_values_json = None
    if line in _TERMIN_OPTIONAL_LINES:
        name = kommo.extract_name(contact)
        if name is None:
            logger.warning(
                "Name not found for lead %d, cannot send notification (line=%s)",
                lead_id, line,
            )
            get_alerter().send_alert(
                f"Имя клиента не найдено для lead {lead_id} (line={line})",
                level="WARNING",
            )
            return {"status": "error", "message": "Name not found in contact"}
        template_values_json = json.dumps([name])

    # 6. Build message
    message_data = MessageData(line=line, termin_date=termin_date, name=name)
    messenger = get_messenger()
    message_text = messenger.build_message_text(message_data)

    # 7. Check send window — outside 9-21 Berlin -> save as pending
    if not is_in_send_window():
        next_retry_at = get_next_send_window_start()
        msg_id = create_message(
            kommo_lead_id=lead_id,
            kommo_contact_id=contact_id,
            phone=phone,
            line=line,
            termin_date=termin_date,
            message_text=message_text,
            status="pending",
            attempts=0,
            next_retry_at=next_retry_at,
            template_values=template_values_json,
        )
        logger.info(
            "Outside send window, scheduled msg %d for %s", msg_id, next_retry_at,
        )
        return {
            "status": "ok",
            "message": "Scheduled for next send window",
            "message_id": msg_id,
            "next_retry_at": next_retry_at,
        }

    # 8. Send message
    try:
        result = messenger.send_message(phone, message_data)
    except MessengerError as exc:
        logger.error("Messenger error for lead %d: %s", lead_id, exc)
        get_alerter().alert_messenger_error(phone, str(exc))
        # Save as failed with next_retry_at so T08 cron can retry later.
        now = datetime.now(tz=timezone.utc)
        next_retry_at = (
            now + timedelta(hours=RETRY_INTERVAL_HOURS)
        ).isoformat(timespec="seconds")
        create_message(
            kommo_lead_id=lead_id,
            kommo_contact_id=contact_id,
            phone=phone,
            line=line,
            termin_date=termin_date,
            message_text=message_text,
            status="failed",
            next_retry_at=next_retry_at,
            template_values=template_values_json,
        )
        return {"status": "error", "message": f"Messenger error: {exc}"}

    # 9. Save to DB
    now = datetime.now(tz=timezone.utc)
    sent_at = now.isoformat(timespec="seconds")
    next_retry_at = (
        now + timedelta(hours=RETRY_INTERVAL_HOURS)
    ).isoformat(timespec="seconds")

    msg_id = create_message(
        kommo_lead_id=lead_id,
        kommo_contact_id=contact_id,
        phone=phone,
        line=line,
        termin_date=termin_date,
        message_text=message_text,
        status="sent",
        sent_at=sent_at,
        next_retry_at=next_retry_at,
        messenger_id=result["message_id"],
        template_values=template_values_json,
    )

    # 10. Add note to Kommo (non-critical)
    try:
        note_time = now.strftime("%Y-%m-%d %H:%M UTC")
        kommo.add_note(
            lead_id, f"WhatsApp сообщение отправлено ({line}) — {note_time}",
        )
    except KommoAPIError as exc:
        logger.warning("Failed to add note to lead %d: %s", lead_id, exc)

    logger.info(
        "Sent msg %d to lead %d (messenger_id=%s)",
        msg_id, lead_id, result["message_id"],
    )

    return {
        "status": "ok",
        "message_id": msg_id,
        "messenger_message_id": result["message_id"],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
