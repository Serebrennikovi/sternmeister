"""Tests for S02 DB migration: migrate_db(), new CHECK constraint, idx_dedup_temporal (T12)."""

import sqlite3
import tempfile
import os
from unittest.mock import patch

import pytest

from server.db import _get_conn, init_db, migrate_db, create_message, get_message_by_id, get_failed_temporal_count


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _make_s01_db(db_path: str) -> None:
    """Create an S01 schema (before migration) at the given path."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kommo_lead_id INTEGER NOT NULL,
            kommo_contact_id INTEGER NOT NULL,
            phone TEXT NOT NULL,
            line TEXT NOT NULL CHECK(line IN ('first', 'second')),
            termin_date TEXT NOT NULL,
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_status_next_retry ON messages (status, next_retry_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dedup ON messages (kommo_lead_id, line, created_at)"
        )
        # Insert S01 data
        conn.execute("""
        INSERT INTO messages
            (kommo_lead_id, kommo_contact_id, phone, line, termin_date, message_text,
             status, attempts, created_at)
        VALUES (1, 10, '+491234567890', 'first', '25.02.2026', 'Test msg', 'sent', 1,
                '2026-02-25T10:00:00+00:00')
        """)
        conn.execute("""
        INSERT INTO messages
            (kommo_lead_id, kommo_contact_id, phone, line, termin_date, message_text,
             status, attempts, created_at)
        VALUES (2, 20, '+491234567891', 'second', '26.02.2026', 'Test msg 2', 'pending', 0,
                '2026-02-25T11:00:00+00:00')
        """)
        conn.commit()
    finally:
        conn.close()


class TestMigrateDb:
    @pytest.fixture(autouse=True)
    def temp_db(self, tmp_path):
        db_path = str(tmp_path / "test_migrate.db")
        with patch("server.db.DATABASE_PATH", db_path), \
             patch("server.config.DATABASE_PATH", db_path):
            yield db_path

    def test_migrate_adds_template_values_column(self, temp_db):
        _make_s01_db(temp_db)
        with patch("server.db.DATABASE_PATH", temp_db):
            migrate_db()

        conn = sqlite3.connect(temp_db)
        try:
            cursor = conn.execute("PRAGMA table_info(messages)")
            cols = [row[1] for row in cursor.fetchall()]
        finally:
            conn.close()

        assert "template_values" in cols

    def test_migrate_s01_data_preserved(self, temp_db):
        _make_s01_db(temp_db)
        with patch("server.db.DATABASE_PATH", temp_db):
            migrate_db()

        conn = sqlite3.connect(temp_db)
        try:
            rows = conn.execute("SELECT kommo_lead_id, line, termin_date FROM messages ORDER BY id").fetchall()
        finally:
            conn.close()

        assert len(rows) == 2
        assert rows[0][0] == 1
        assert rows[0][1] == "first"
        assert rows[0][2] == "25.02.2026"
        assert rows[1][1] == "second"

    def test_migrate_s01_template_values_null_for_old_rows(self, temp_db):
        _make_s01_db(temp_db)
        with patch("server.db.DATABASE_PATH", temp_db):
            migrate_db()

        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute("SELECT template_values FROM messages WHERE kommo_lead_id=1").fetchone()
        finally:
            conn.close()

        assert row[0] is None  # S01 rows get NULL template_values

    def test_migrate_allows_new_line_values(self, temp_db):
        _make_s01_db(temp_db)
        with patch("server.db.DATABASE_PATH", temp_db):
            migrate_db()

        conn = sqlite3.connect(temp_db)
        try:
            # Insert a new S02 line value — must not raise CHECK constraint error
            conn.execute("""
            INSERT INTO messages
                (kommo_lead_id, kommo_contact_id, phone, line, termin_date, message_text,
                 status, attempts, created_at)
            VALUES (3, 30, '+491234567892', 'gosniki_consultation_done', '', 'G1 msg',
                    'sent', 1, '2026-03-01T09:00:00+00:00')
            """)
            conn.execute("""
            INSERT INTO messages
                (kommo_lead_id, kommo_contact_id, phone, line, termin_date, message_text,
                 status, attempts, created_at)
            VALUES (4, 40, '+491234567893', 'berater_accepted', '', 'B1 msg',
                    'sent', 1, '2026-03-01T09:01:00+00:00')
            """)
            conn.execute("""
            INSERT INTO messages
                (kommo_lead_id, kommo_contact_id, phone, line, termin_date, message_text,
                 status, attempts, created_at)
            VALUES (5, 50, '+491234567894', 'berater_day_minus_3', '25.03.2026', 'B3 msg',
                    'sent', 1, '2026-03-01T09:02:00+00:00')
            """)
            conn.commit()
        finally:
            conn.close()

    def test_migrate_creates_idx_dedup_temporal(self, temp_db):
        _make_s01_db(temp_db)
        with patch("server.db.DATABASE_PATH", temp_db):
            migrate_db()

        conn = sqlite3.connect(temp_db)
        try:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_dedup_temporal'"
            ).fetchone()
        finally:
            conn.close()

        assert indexes is not None, "idx_dedup_temporal should exist after migration"

    def test_migrate_is_idempotent(self, temp_db):
        _make_s01_db(temp_db)
        with patch("server.db.DATABASE_PATH", temp_db):
            migrate_db()
            # Second call must not raise
            migrate_db()
            migrate_db()

    def test_fresh_db_already_has_s02_schema(self, temp_db):
        """Fresh install via init_db() creates S02 schema directly — migrate_db() is a no-op."""
        with patch("server.db.DATABASE_PATH", temp_db):
            init_db()

        conn = sqlite3.connect(temp_db)
        try:
            cursor = conn.execute("PRAGMA table_info(messages)")
            cols = [row[1] for row in cursor.fetchall()]
        finally:
            conn.close()

        assert "template_values" in cols

    def test_idx_dedup_temporal_enforces_uniqueness(self, temp_db):
        _make_s01_db(temp_db)
        with patch("server.db.DATABASE_PATH", temp_db):
            migrate_db()

        conn = sqlite3.connect(temp_db)
        try:
            # First insert OK
            conn.execute("""
            INSERT INTO messages
                (kommo_lead_id, kommo_contact_id, phone, line, termin_date, message_text,
                 status, attempts, created_at)
            VALUES (99, 99, '+491234567899', 'berater_day_minus_3', '25.03.2026', 'B3',
                    'sent', 1, '2026-03-01T10:00:00+00:00')
            """)
            conn.commit()
            # Duplicate (same lead, line, termin_date) → UNIQUE constraint violation
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("""
                INSERT INTO messages
                    (kommo_lead_id, kommo_contact_id, phone, line, termin_date, message_text,
                     status, attempts, created_at)
                VALUES (99, 99, '+491234567899', 'berater_day_minus_3', '25.03.2026', 'B3 dup',
                        'sent', 1, '2026-03-01T11:00:00+00:00')
                """)
                conn.commit()
        finally:
            conn.close()


# -----------------------------------------------------------------------
# get_failed_temporal_count()
# -----------------------------------------------------------------------

class TestGetFailedTemporalCount:
    """Unit tests for get_failed_temporal_count() (L7 code-review fix)."""

    @pytest.fixture(autouse=True)
    def fresh_db(self, tmp_path):
        db_path = str(tmp_path / "test_temporal.db")
        with patch("server.db.DATABASE_PATH", db_path), \
             patch("server.config.DATABASE_PATH", db_path):
            init_db()
            yield db_path

    def _insert(self, db_path, line, status):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
            INSERT INTO messages
                (kommo_lead_id, kommo_contact_id, phone, line, termin_date, message_text,
                 status, attempts, created_at)
            VALUES (1, 1, '+491234567890', ?, '25.03.2026', 'msg', ?, 1,
                    '2026-03-01T10:00:00+00:00')
            """, (line, status))
            conn.commit()
        finally:
            conn.close()

    def test_counts_failed_temporal(self, fresh_db):
        self._insert(fresh_db, "berater_day_minus_3", "failed")
        self._insert(fresh_db, "berater_day_minus_1", "failed")
        with patch("server.db.DATABASE_PATH", fresh_db):
            assert get_failed_temporal_count() == 2

    def test_ignores_sent_temporal(self, fresh_db):
        self._insert(fresh_db, "berater_day_minus_3", "sent")
        with patch("server.db.DATABASE_PATH", fresh_db):
            assert get_failed_temporal_count() == 0

    def test_ignores_failed_non_temporal(self, fresh_db):
        self._insert(fresh_db, "berater_accepted", "failed")
        self._insert(fresh_db, "gosniki_consultation_done", "failed")
        with patch("server.db.DATABASE_PATH", fresh_db):
            assert get_failed_temporal_count() == 0

    def test_mixed_records_counts_correctly(self, fresh_db):
        self._insert(fresh_db, "berater_day_minus_3", "failed")   # counted
        self._insert(fresh_db, "berater_day_minus_1", "sent")     # not counted (sent)
        self._insert(fresh_db, "berater_day_0", "failed")         # counted
        self._insert(fresh_db, "berater_accepted", "failed")      # not counted (non-temporal)
        with patch("server.db.DATABASE_PATH", fresh_db):
            assert get_failed_temporal_count() == 2

    def test_empty_db_returns_zero(self, fresh_db):
        with patch("server.db.DATABASE_PATH", fresh_db):
            assert get_failed_temporal_count() == 0
