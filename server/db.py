import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from server.config import DATABASE_PATH, MAX_RETRY_ATTEMPTS

_ALLOWED_COLUMNS = frozenset({
    "kommo_contact_id", "kommo_lead_id", "phone", "line", "message_text",
    "status", "attempts", "sent_at", "next_retry_at", "messenger_id",
    "messenger_backend",
})

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kommo_lead_id INTEGER NOT NULL,
    kommo_contact_id INTEGER NOT NULL,
    phone TEXT NOT NULL,
    line TEXT NOT NULL CHECK(line IN ('first', 'second')),
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


def init_db() -> None:
    """Create messages table and indexes if they don't exist."""
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


def create_message(
    *,
    kommo_lead_id: int,
    kommo_contact_id: int,
    phone: str,
    line: str,
    message_text: str,
    status: str = "pending",
    attempts: int = 1,
    sent_at: str | None = None,
    next_retry_at: str | None = None,
    messenger_id: str | None = None,
    messenger_backend: str = "wazzup",
) -> int:
    """Insert a new message record. Returns the row id."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO messages
                (kommo_lead_id, kommo_contact_id, phone, line,
                 message_text, status, attempts, created_at, sent_at,
                 next_retry_at, messenger_id, messenger_backend)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kommo_lead_id,
                kommo_contact_id,
                phone,
                line,
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


def get_messages_for_retry(
    at: str | None = None,
    max_attempts: int = MAX_RETRY_ATTEMPTS + 1,
) -> list[sqlite3.Row]:
    """Get messages eligible for retry: sent, next_retry_at <= now, attempts < max."""
    if at is None:
        at = now_iso()
    conn = _get_conn()
    try:
        return conn.execute(
            """
            SELECT * FROM messages
            WHERE status = 'sent'
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
