"""SQLite connection management and schema migrations.

Schema is managed by numbered SQL files in src/ceiling/db/migrations/, applied
in lexical order at startup. Applied migrations are recorded in the
schema_migrations table so reapplication is a no-op.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utc_now_iso() -> str:
    """Current UTC time as ISO 8601 with second precision."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database with foreign keys on and named row access."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def apply_migrations(
    conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR
) -> list[str]:
    """Apply pending migrations in order. Returns the names newly applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    already = {
        row["migration"]
        for row in conn.execute("SELECT migration FROM schema_migrations")
    }
    applied: list[str] = []
    for sql_path in sorted(migrations_dir.glob("*.sql")):
        if sql_path.name in already:
            continue
        conn.executescript(sql_path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations (migration, applied_at) VALUES (?, ?)",
            (sql_path.name, utc_now_iso()),
        )
        applied.append(sql_path.name)
    conn.commit()
    return applied
