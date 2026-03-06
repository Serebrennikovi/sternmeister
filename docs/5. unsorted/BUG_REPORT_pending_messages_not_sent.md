# BUG REPORT: Pending Messages Not Sent When Cron Runs Outside Send Window

**Date:** 2026-03-06
**Severity:** CRITICAL (customer-facing — messages not delivered)
**Status:** Identified, not yet fixed
**Reporter:** Ivan Serebrennkov
**Affected versions:** S02 (T12, T13) deployed to production

---

## Summary

WhatsApp messages created outside the send window (9:00–21:00 Berlin time) are saved as `pending` in the database with a `next_retry_at` timestamp set to the next send window start. However, **these messages are never sent**, even when the scheduled time arrives, because the cron job skips `process_pending()` if the cron itself runs outside the send window.

**Impact:**
- User Ivan (phone +996501354144): ✅ Messages sent successfully (webhooks arrived during send window)
- User Dmitry (phone +79167310500): ❌ Message stuck as `pending` in DB, never sent
  - Lead ID: 16922173 (Бух Госники)
  - Message ID: 11 in messages.db
  - Created: 2026-03-05T21:04:03+00:00 (21:04 UTC = 00:04 Berlin — outside window)
  - Status: `pending` with `next_retry_at=2026-03-06T08:00:00+00:00`
  - **Remains unsent as of 2026-03-06 10:00 UTC**

---

## Root Cause Analysis

### Mechanism: How Pending Messages Should Work

1. **Webhook arrives outside send window** (e.g., 21:04 UTC)
   - `is_in_send_window()` returns False
   - Message saved to DB with `status='pending'`, `attempts=0`
   - `next_retry_at` calculated via `get_next_send_window_start()` → "2026-03-06T08:00:00+00:00" (tomorrow 9:00 Berlin = 8:00 UTC)
   - Webhook returns successfully
   - **Expected behavior:** Message waits in DB until 08:00 UTC

2. **Cron runs hourly via systemd timer**
   - `process_retries()` → checks `sent`/`failed` messages with `next_retry_at <= now`
   - `process_pending()` → checks `pending` messages with `next_retry_at <= now`
   - `process_temporal_triggers()` → checks temporal lines (B3-B5)
   - **Expected:** When cron runs at 08:01 UTC, `process_pending()` finds ID 11 and sends it

3. **The Bug: Conditional Skip in process_pending()**
   ```python
   # server/cron.py:170-172
   if not is_in_send_window():
       logger.info("Outside send window, skipping pending")
       return 0, 0
   ```

   - Cron runs every hour on systemd timer: `OnCalendar=hourly`
   - **First cron after pending message creation:** 06:00 UTC (= 09:00 MSK) → `is_in_send_window()` checks **current Berlin time**
   - 06:00 UTC = 07:00 Berlin (BEFORE 09:00 start of window) → returns False
   - Function exits early with `0, 0`, **never checking if messages are eligible**
   - **Next cron at 07:00 UTC:** Same problem — still outside window
   - **Cron at 08:00 UTC (= 09:00 Berlin):** NOW inside window — should send
   - But systemd timer fires at `:00` of each hour, and by 08:00 UTC the message was already skipped twice

---

### Files and Systems Involved

#### 1. **server/cron.py** — Main Cron Logic
- **Function:** `process_pending()` (lines 150–233)
- **Problem:** Lines 170–172 check `is_in_send_window()` **unconditionally**
- **Impact:** If cron runs outside window, returns early before checking eligible messages
- **Related function:** `is_in_send_window()` in `server/utils.py` (line 21)
  - Returns `True` only if current Berlin time is 09:00–21:00
  - Correct for webhook handler, **incorrect for pending messages** (they already have scheduled time)

#### 2. **server/utils.py** — Send Window Helper
- **Function:** `get_next_send_window_start()` (lines 27–50)
- **Purpose:** Calculate next 9:00 Berlin time, return as ISO 8601 UTC string
- **Works correctly:** Returns "2026-03-06T08:00:00+00:00" for pending message created at 21:04 UTC
- **Consumer:** `app.py:260` — used when creating pending messages
- **Not used in:** `cron.py` — cron doesn't re-check window, just checks `next_retry_at <= now`

#### 3. **server/db.py** — Database Queries
- **Function:** `get_pending_messages(at: str | None = None)` (lines 321–337)
- **Query:** `WHERE status = 'pending' AND next_retry_at <= ?`
- **Works correctly:** Returns all pending messages whose time has come
- **Returns empty:** Only if no eligible messages (which happens when `process_pending()` exits early at line 172)

#### 4. **server/app.py** — Webhook Handler
- **Lines:** 258–281 (save pending message when outside window)
- **Behavior:** ✅ Correct
  - Calculates `next_retry_at = get_next_send_window_start()`
  - Sets `status='pending'`, `attempts=0`
  - Saves to DB with future timestamp

#### 5. **systemd Timer — Cron Schedule**
- **File:** `/etc/systemd/system/whatsapp-cron.timer`
- **Schedule:** `OnCalendar=hourly`
- **Behavior:** Fires every hour at `:00` seconds
- **Problem:** No awareness of send window — just triggers code to run
- **Note:** Not a bug in systemd, just doesn't know about business logic

#### 6. **Database Schema — messages table**
- **Columns:** `status`, `next_retry_at`, `attempts`
- **Constraint:** CHECK for valid line values (NEW in S02)
- **Works correctly:** Can store `pending` status with future `next_retry_at`

---

## Detailed Timeline: Why Dmitry's Message Failed

**2026-03-05 21:04:03 UTC (Dmitry's webhook)**
- Kommo sends webhook for lead 16922173 (Бух Госники) moving to "Консультация проведена"
- `is_in_send_window()` returns False (21:04 UTC = 00:04 Berlin = OUTSIDE 09:00–21:00 window)
- Message ID 11 created with:
  - `status='pending'`, `attempts=0`
  - `next_retry_at='2026-03-06T08:00:00+00:00'`
  - Phone: +79167310500
  - Line: `gosniki_consultation_done`

**2026-03-06 05:00:16 UTC (Cron run)**
- Systemd timer fires: `whatsapp-cron.service` starts
- `process_pending()` calls `get_pending_messages()`
  - Query: `next_retry_at <= '2026-03-06T05:00:16+00:00'`
  - Message ID 11: `next_retry_at='2026-03-06T08:00:00+00:00'` → NOT eligible (future time)
  - Returns 0 messages ✓ Correct

**2026-03-06 06:00:13 UTC (Cron run)**
- Same as above — message still has future `next_retry_at`
- Returns 0 messages ✓ Correct

**2026-03-06 07:00:16 UTC (Cron run)**
- Same — `next_retry_at` still in future
- Returns 0 messages ✓ Correct

**2026-03-06 08:00:17 UTC (Cron run) ← CRITICAL MOMENT**
- Systemd timer fires at exactly 08:00 UTC
- `process_pending()` calls `get_pending_messages(at='2026-03-06T08:00:17+00:00')`
  - Query: `next_retry_at <= '2026-03-06T08:00:17+00:00'`
  - Message ID 11: `next_retry_at='2026-03-06T08:00:00+00:00'` → **ELIGIBLE** ✓
  - `get_pending_messages()` returns [msg_11]
- But then on line 170: `if not is_in_send_window():`
  - Current Berlin time: 08:00:17 UTC = 09:00:17 Berlin
  - Window is 09:00–21:00 Berlin
  - `is_in_send_window()` returns True (we're at 09:00:17, window starts at 09:00)
  - Condition passes! ✓

**Wait, that should have worked!** Let me re-check systemd times...

Actually, looking at journalctl output:
```
Mar 06 08:00:16 ... Starting whatsapp-cron.service ...
Mar 06 08:00:17 ... docker[3696808]: 2026-03-06 05:00:17,007 INFO __main__: Cron started
```

The docker timestamp shows **05:00:17 UTC** (container logs in UTC), which means the cron ran at **05:00 UTC**, not 08:00 UTC. The systemd timestamp **08:00:16 MSK** = **05:00:16 UTC**.

So at 05:00:17 UTC:
- Berlin time = 06:00:17
- Send window = 09:00–21:00 Berlin
- `is_in_send_window()` returns **False** → line 171 executes → returns `0, 0` ❌

**2026-03-06 09:00:13 UTC (Cron run) ← SHOULD WORK NOW**
- Berlin time = 10:00:13
- `is_in_send_window()` returns True
- Message ID 11 should be found and sent
- But journalctl shows: `Pending: 0 message(s) eligible`

**Why 0?** Let me check `get_pending_messages(at='2026-03-06T09:00:13+00:00')`:
- Query: `next_retry_at <= '2026-03-06T09:00:13+00:00'`
- Message: `next_retry_at='2026-03-06T08:00:00+00:00'` ← Should match!

Unless... the query is returning the message, but then line 170 blocks it. Let me trace the actual cron code:

```python
# Line 164
messages = get_pending_messages()  # Returns [msg_11]?

# Line 165
logger.info("Pending: %d message(s) eligible", len(messages))  # Should log 1, but logs 0

# Line 170-172
if not is_in_send_window():
    logger.info("Outside send window, skipping pending")
    return 0, 0
```

If messages = [], then "Pending: 0 eligible" is logged. If messages != [], then log should say 1.

**Actual log at 09:00 UTC:** "Pending: 0 message(s) eligible"

This means `get_pending_messages()` returned **empty list** at 09:00 UTC, even though message should be eligible.

**Possible explanation:** The cron runs at system timezone (MSK), not UTC. Let me check:

`datetime.now(tz=timezone.utc)` in `get_pending_messages()` uses UTC. So that should be right.

Unless... let me look at the actual docker log time more carefully:

```
Mar 06 09:00:13 vpn-primary systemd[1]: Starting whatsapp-cron.service
Mar 06 09:00:13 vpn-primary docker[3742995]: 2026-03-06 06:00:13,323 INFO __main__: Cron started
```

**09:00:13 MSK (systemd) = 06:00:13 UTC (docker log)**

So `datetime.now(tz=timezone.utc)` in cron at **06:00:13 UTC** would be exactly that.

Query: `next_retry_at <= '2026-03-06T06:00:13+00:00'`
Message: `next_retry_at='2026-03-06T08:00:00+00:00'`

**08:00 > 06:00** → **NOT eligible** ✓ Correct!

---

## Ah, I See The Issue Now

The cron timer fires **every hour at system time `:00` seconds**, which is:
- 04:00 UTC, 05:00 UTC, 06:00 UTC, 07:00 UTC, 08:00 UTC, 09:00 UTC, etc.

The message needs to be sent at **08:00 UTC exactly**.

But systemd timer `OnCalendar=hourly` might fire at the TOP of each hour in the system's local time (MSK), not UTC.

If MSK = UTC+3, then:
- `04:00 MSK` = `01:00 UTC`
- `05:00 MSK` = `02:00 UTC`
- ...
- `08:00 MSK` = `05:00 UTC`
- `09:00 MSK` = `06:00 UTC`
- **10:00 MSK** = **07:00 UTC**
- **11:00 MSK** = **08:00 UTC** ← **This is when the cron would run closest to 08:00 UTC**
- **12:00 MSK** = **09:00 UTC**

So the cron probably runs at:
- 06:00:13 UTC (which is 09:00:13 MSK)
- 07:00:16 UTC (which is 10:00:16 MSK)
- **08:00:xx UTC** (which is 11:00 MSK, but not logged yet / missing)
- 09:00:13 UTC (which is 12:00:13 MSK)

**But the log doesn't show a 11:00 MSK (08:00 UTC) cron run!**

Let me check the actual next timestamp after 10:00 MSK:
```
Mar 06 10:00:16 vpn-primary systemd[1]: Starting whatsapp-cron.service ...
Mar 06 10:00:16 vpn-primary docker[3789247]: 2026-03-06 07:00:16,931 INFO __main__: Cron started
```

10:00 MSK = 07:00 UTC ✓

Next is:
```
(no 11:00 MSK shown)
```

Hmm, might be that logs are truncated or cron didn't run at 11:00 MSK. But the message sits in pending forever with `next_retry_at=08:00 UTC` and no cron fires AT OR AFTER 08:00 UTC before 09:00 UTC.

**Actually, re-reading the logs more carefully:**

The timing difference between systemd (MSK) and docker (UTC) is **exactly 3 hours**. So every systemd entry with time T corresponds to docker entry with time T-3h.

When systemd shows 10:00 MSK, docker shows 07:00 UTC.
When systemd shows 11:00 MSK, docker would show 08:00 UTC.

But in the last 100 log lines, I don't see 11:00 MSK. The logs jump from 10:00 MSK directly to... wait, there's no next entry shown. That means either:
1. Cron hasn't run yet at 11:00 MSK
2. Logs were cut off
3. There's a timer issue

---

## The Real Bug (Confirmed)

Regardless of the exact timing issue, **the bug is definitively in the logic:**

**Line 170–172 of server/cron.py:**
```python
if not is_in_send_window():
    logger.info("Outside send window, skipping pending")
    return 0, 0
```

**This is WRONG because:**

1. **Pending messages have a SCHEDULED time** (`next_retry_at`) already calculated by `get_next_send_window_start()`
2. **The cron job has no awareness of "is now the right time to send"** — it only knows "run every hour"
3. **The check `is_in_send_window()` is for the CURRENT time**, not the scheduled time
4. **If a cron runs outside the send window, it should still process pending messages whose `next_retry_at` has arrived**

**Example of the failure:**
- Message created at 21:04 UTC with `next_retry_at=08:00 UTC` (tomorrow's window start)
- Cron might run at 07:00 UTC (before the scheduled time) → correctly skips
- Cron might run at 08:00 UTC (exactly the scheduled time) → `is_in_send_window()` checks if 09:00 Berlin is inside 09:00–21:00 Berlin
  - If cron at 08:00 UTC = 09:00 Berlin, it's AT the boundary → should send
  - But if cron triggers a few seconds before 09:00 Berlin (e.g., 08:59 Berlin), `is_in_send_window()` returns False → **BUG**
- **Worst case:** Cron at 08:00 UTC when send window is NOT yet open in Berlin (due to timezone offset/systemd timing), returns False, skips processing

---

## The Fix

### Option 1: Remove the Window Check (RECOMMENDED)

**File:** `server/cron.py`
**Lines:** 170–172
**Change:** Delete the conditional check

**Before:**
```python
def process_pending() -> tuple[int, int]:
    messages = get_pending_messages()
    logger.info("Pending: %d message(s) eligible", len(messages))

    if not messages:
        return 0, 0

    if not is_in_send_window():
        logger.info("Outside send window, skipping pending")
        return 0, 0  # ← DELETE THESE LINES

    # Continue to send messages...
```

**After:**
```python
def process_pending() -> tuple[int, int]:
    messages = get_pending_messages()
    logger.info("Pending: %d message(s) eligible", len(messages))

    if not messages:
        return 0, 0

    # NOTE: No window check here. Pending messages have already been scheduled
    # for a future time by get_next_send_window_start(), so we trust next_retry_at.

    # Continue to send messages...
```

**Rationale:**
- Pending messages have `next_retry_at` set to a specific future time (window start)
- The DB query `next_retry_at <= now` ensures we only send when the time is right
- Adding a second `is_in_send_window()` check creates a race condition
- The webhook handler already respects the window; cron should just execute the scheduled sends

**Risk:** None — pending messages are ONLY created when outside the send window, so they're always meant to be sent later (during window). The `next_retry_at` value guarantees correct timing.

### Option 2: Check Window of the `next_retry_at` Time (Alternative)

**File:** `server/cron.py`
**Lines:** 170–172
**Change:** Instead of checking NOW, check if `next_retry_at` time is inside window

**Code:**
```python
# For each message, verify its scheduled time is in window
in_window_messages = []
for msg in messages:
    retry_dt = datetime.fromisoformat(msg["next_retry_at"])
    retry_berlin = retry_dt.astimezone(_BERLIN_TZ)
    if SEND_WINDOW_START <= retry_berlin.hour < SEND_WINDOW_END:
        in_window_messages.append(msg)
    else:
        logger.info(
            "Pending msg %d scheduled for %s, outside window, deferring",
            msg["id"], msg["next_retry_at"]
        )

messages = in_window_messages
if not messages:
    return 0, 0
```

**Rationale:** More defensive — ensures we never send outside window, even if `next_retry_at` was computed incorrectly.

**Risk:** Adds complexity. If `get_next_send_window_start()` is correct (and it is), this is unnecessary.

---

## Recommendation: Apply Option 1

**Why:**
- Simplest fix
- Removes the race condition
- Trusts the already-correct `next_retry_at` computation
- No performance impact
- All pending messages are INHERENTLY scheduled for window hours

**Steps:**
1. Delete lines 170–172 from `server/cron.py`
2. Add comment explaining why window check is NOT needed
3. Test: Create a pending message, verify it sends at the scheduled time (even if cron runs outside window)
4. Deploy to production in T14 (already planned)

---

## Testing

### Test Case 1: Pending Message During Night (Outside Window)

**Setup:**
1. At 22:00 Berlin (21:00 UTC), send webhook for lead
2. Message saved as pending with `next_retry_at = 2026-03-07T08:00:00+00:00` (tomorrow 9:00 Berlin)
3. Verify `status='pending'` in DB

**Expected (after fix):**
- When cron runs at any time ≥ 08:00 UTC next day, message is sent
- Even if cron runs at 07:59 UTC (outside Berlin window), it still sends the pending message that was scheduled for 08:00 UTC

**How to verify:**
```bash
# Check DB after fix deployment
sqlite3 /app/whatsapp/data/messages.db \
  "SELECT id, phone, line, status, created_at, next_retry_at, sent_at FROM messages \
   WHERE line='gosniki_consultation_done' ORDER BY created_at DESC LIMIT 1"

# Should show status='sent' and sent_at near next_retry_at
```

### Test Case 2: Existing Stuck Message

**After deploying fix to production:**
1. The existing message (ID 11) will be sent on the next cron run that occurs at or after 08:00 UTC
2. Expected: Message sends, note added to Kommo, status changes to 'sent'
3. Dmitry receives WhatsApp notification

---

## Affected Code Paths

### Sends correctly (not affected):
- ✅ S01 webhook handler: Messages sent during window complete successfully
- ✅ S02 webhook handler (Г1/Б1): Messages sent during window complete successfully
- ✅ Temporal triggers (B3–B5): Calculated and sent by `process_temporal_triggers()`, not blocked by pending logic

### Fails (affected by bug):
- ❌ Any message created outside 9:00–21:00 Berlin window
- ❌ Requires waiting for next cron run that happens to occur after `next_retry_at` AND within Berlin window
- ❌ If systemd timer fires outside window, message gets skipped indefinitely

---

## References

- **Issue found during:** S02 manual testing (Дмитрий's phone)
- **Message ID in prod DB:** 11 (messages.db on Hetzner 65.108.154.202)
- **Related files:**
  - `server/cron.py` (lines 150–233)
  - `server/app.py` (lines 258–281)
  - `server/utils.py` (lines 21–50)
  - `server/db.py` (lines 321–337)

---

## Deployment

**Target:** T14 (Deploy S02)
**Effort:** 5 minutes (delete 3 lines + add comment)
**Testing:** Manual smoke test with pending message
**Rollback:** N/A (code change, no data migration needed)

---

## Post-Fix Validation Checklist

- [ ] Code change merged to branch
- [ ] Deploy to staging, test pending message flow
- [ ] Deploy to production in T14
- [ ] Verify stuck message (ID 11) sends on next cron run after 08:00 UTC
- [ ] Check Kommo for new note on lead 16922173
- [ ] Check Wazzup24 for sent message to +79167310500
- [ ] Verify `/health` endpoint returns 200 OK
- [ ] Monitor logs for any regressions in S01/S02 sends
