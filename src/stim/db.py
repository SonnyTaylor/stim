"""SQLite database connection and schema."""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path.home() / ".stim" / "stim.db"


def get_connection() -> sqlite3.Connection:
    """Get a connection to the SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Initialize the database schema with migrations."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS doses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount_mg REAL NOT NULL,
                taken_at TEXT NOT NULL,
                note TEXT,
                is_off_day INTEGER NOT NULL DEFAULT 0,
                fed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS idx_doses_taken_at ON doses(taken_at);
        """)

        # Migration: add fed column if missing
        columns = {row[1] for row in conn.execute("PRAGMA table_info(doses)").fetchall()}
        if "fed" not in columns:
            conn.execute("ALTER TABLE doses ADD COLUMN fed INTEGER NOT NULL DEFAULT 0")


def now_utc() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
