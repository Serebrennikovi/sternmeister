import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.config import DATABASE_PATH, MAX_RETRY_ATTEMPTS

_ALLOWED_COLUMNS = frozenset({
    "kommo_contact_id", "kommo_lead_id", "phone", "line", "termin_date",
    "message_text", "status", "attempts", "sent_at", "next_retry_at",
    "messenger_id", "messenger_backend", "template_values",
})

# Full S02 schema — used for fresh installs.
# Existing S01 databases are upgraded via migrate_db().
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kommo_lead_id INTEGER NOT NULL,
    kommo_contact_id INTEGER NOT NULL,
    phone TEXT NOT NULL,
    line TEXT NOT NULL CHECK(line IN (
        'first', 'second',
        'gosniki_consultation_done', 'berater_accepted',
        'berater_day_minus_7', 'berater_day_minus_3',
        'berater_day_minus_1', 'berater_day_0'
    )),
    termin_date TEXT NOT NULL,
    template_values TEXT,
    message_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'delivered', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    next_retry_at TEXT,
    messenger_id TEXT,
    messenger_backend TEXT NOT NULL DEFAULT 'wazzup'
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_status_next_retry ON messages (status, next_retry_at);",
    "CREATE INDEX IF NOT EXISTS idx_kommo_contact ON messages (kommo_contact_id);",
    "CREATE INDEX IF NOT EXISTS idx_dedup ON messages (kommo_lead_id, line, created_at);",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup_temporal
    ON messages(kommo_lead_id, line, termin_date)
    WHERE line IN (
        'berater_day_minus_7', 'berater_day_minus_3',
        'berater_day_minus_1', 'berater_day_0'
    );""",
]


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _validate_columns(columns: set[str]) -> None:
    """Raise ValueError if any column name is not in the whitelist."""
    invalid = columns - _ALLOWED_COLUMNS
    if invalid:
        raise ValueError(f"Invalid column names: {invalid}")


def now_iso() -> str:
    """Return current UTC time as ISO 8601 string.

    All timestamps in the DB are stored in UTC for consistent
    lexicographic comparison in SQLite queries.
    """
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def migrate_db() -> None:
    """Migrate S01 schema to S02: expand CHECK constraint, add template_values.

    Idempotent: checks if template_values column already exists.
    Uses isolation_level=None (autocommit mode) so that explicit
    BEGIN IMMEDIATE / COMMIT control DDL operations atomically —
    Python's default isolation_level auto-commits before each DDL statement,
    breaking a normal BEGIN/COMMIT wrapper.
    """
    conn = sqlite3.connect(DATABASE_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        # Idempotency check: if template_values already exists, migration done.
        cursor = conn.execute("PRAGMA table_info(messages)")
        cols = [row[1] for row in cursor.fetchall()]
        if "template_values" in cols:
            return

        conn.execute("BEGIN IMMEDIATE")
        # Clean up any leftover messages_new from a previously interrupted migration.
        conn.execute("DROP TABLE IF EXISTS messages_new")
        conn.execute("""
        CREATE TABLE messages_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kommo_lead_id INTEGER NOT NULL,
            kommo_contact_id INTEGER NOT NULL,
            phone TEXT NOT NULL,
            line TEXT NOT NULL CHECK(line IN (
                'first', 'second',
                'gosniki_consultation_done', 'berater_accepted',
                'berater_day_minus_7', 'berater_day_minus_3',
                'berater_day_minus_1', 'berater_day_0'
            )),
            termin_date TEXT NOT NULL,
            template_values TEXT,
            message_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'sent', 'delivered', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            next_retry_at TEXT,
            messenger_id TEXT,
            messenger_backend TEXT NOT NULL DEFAULT 'wazzup'
        )
        """)
        conn.execute("""
            INSERT INTO messages_new
                (id, kommo_lead_id, kommo_contact_id, phone, line, termin_date,
                 template_values, message_text, status, attempts, created_at,
                 sent_at, next_retry_at, messenger_id, messenger_backend)
            SELECT
                id, kommo_lead_id, kommo_contact_id, phone, line, termin_date,
                NULL, message_text, status, attempts, created_at,
                sent_at, next_retry_at, messenger_id, messenger_backend
            FROM messages
        """)
        conn.execute("DROP TABLE messages")
        conn.execute("ALTER TABLE messages_new RENAME TO messages")
        # Recreate indexes
        for idx_sql in _CREATE_INDEXES:
            conn.execute(idx_sql)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create messages table and indexes if they don't exist, then migrate."""
    db_dir = Path(DATABASE_PATH).parent
    if db_dir != Path("."):
        db_dir.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            conn.execute(idx_sql)
        conn.commit()
    finally:
        conn.close()
    migrate_db()


def create_message(
    *,
    kommo_lead_id: int,
    kommo_contact_id: int,
    phone: str,
    line: str,
    termin_date: str,
    message_text: str,
    status: str = "pending",
    attempts: int = 1,
    sent_at: str | None = None,
    next_retry_at: str | None = None,
    messenger_id: str | None = None,
    messenger_backend: str = "wazzup",
    template_values: str | None = None,
) -> int:
    """Insert a new message record. Returns the row id."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO messages
                (kommo_lead_id, kommo_contact_id, phone, line,
                 termin_date, template_values, message_text, status, attempts,
                 created_at, sent_at, next_retry_at, messenger_id, messenger_backend)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kommo_lead_id,
                kommo_contact_id,
                phone,
                line,
                termin_date,
                template_values,
                message_text,
                status,
                attempts,
                now_iso(),
                sent_at,
                next_retry_at,
                messenger_id,
                messenger_backend,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_message(message_id: int, **fields) -> None:
    """Update specified fields of a message by id."""
    if not fields:
        return
    _validate_columns(set(fields.keys()))
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [message_id]
    conn = _get_conn()
    try:
        conn.execute(
            f"UPDATE messages SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def get_messages(**filters) -> list[sqlite3.Row]:
    """Get messages with optional filters (field=value)."""
    if filters:
        _validate_columns(set(filters.keys()))
    conn = _get_conn()
    try:
        where_parts = []
        values = []
        for k, v in filters.items():
            where_parts.append(f"{k} = ?")
            values.append(v)
        where = " AND ".join(where_parts) if where_parts else "1=1"
        return conn.execute(
            f"SELECT * FROM messages WHERE {where} ORDER BY created_at DESC",
            values,
        ).fetchall()
    finally:
        conn.close()


def get_message_by_id(message_id: int) -> sqlite3.Row | None:
    """Get a single message by its id."""
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT * FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    finally:
        conn.close()


def get_recent_message(
    kommo_lead_id: int,
    line: str,
    within_minutes: int,
) -> sqlite3.Row | None:
    """Check if a message for this lead+line was created recently.

    Used for webhook deduplication: Kommo may resend the same event.
    """
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(minutes=within_minutes)
    ).isoformat(timespec="seconds")
    conn = _get_conn()
    try:
        return conn.execute(
            """
            SELECT * FROM messages
            WHERE kommo_lead_id = ?
              AND line = ?
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (kommo_lead_id, line, cutoff),
        ).fetchone()
    finally:
        conn.close()


def get_messages_for_retry(
    at: str | None = None,
    max_attempts: int = MAX_RETRY_ATTEMPTS + 1,
) -> list[sqlite3.Row]:
    """Get messages eligible for retry: sent or failed, next_retry_at <= now, attempts < max.

    Both 'sent' (no reply yet) and 'failed' (delivery error) messages
    are retried by the T08 cron job.
    """
    if at is None:
        at = now_iso()
    conn = _get_conn()
    try:
        return conn.execute(
            """
            SELECT * FROM messages
            WHERE status IN ('sent', 'failed')
              AND next_retry_at <= ?
              AND attempts < ?
            ORDER BY next_retry_at ASC
            """,
            (at, max_attempts),
        ).fetchall()
    finally:
        conn.close()


def get_pending_messages(at: str | None = None) -> list[sqlite3.Row]:
    """Get pending messages whose next_retry_at has passed."""
    if at is None:
        at = now_iso()
    conn = _get_conn()
    try:
        return conn.execute(
            """
            SELECT * FROM messages
            WHERE status = 'pending'
              AND next_retry_at <= ?
            ORDER BY next_retry_at ASC
            """,
            (at,),
        ).fetchall()
    finally:
        conn.close()


def get_failed_temporal_count() -> int:
    """Count failed messages for temporal lines (for /health endpoint)."""
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE status = 'failed'
              AND line IN (
                  'berater_day_minus_7', 'berater_day_minus_3',
                  'berater_day_minus_1', 'berater_day_0'
              )
            """
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()
