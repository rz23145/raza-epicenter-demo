"""Thin repository layer over sqlite3.

Phase 1 ships schema introspection helpers used by the CLI. Data access
methods for stores, scans, fetches and the rest are added phase by phase,
next to the code that needs them.
"""

from __future__ import annotations

import sqlite3

_IDENTIFIER_TABLES: frozenset[str] = frozenset(
    {
        "stores",
        "scans",
        "fetches",
        "assets",
        "app_matches",
        "fingerprint_matches",
        "catalog_snapshots",
        "review_snapshots",
        "features",
        "labels",
        "wayback_snapshots",
        "wayback_app_matches",
        "wayback_fingerprint_matches",
        "hiring_posts",
        "index_values",
    }
)


def applied_migrations(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT migration FROM schema_migrations ORDER BY migration"
    ).fetchall()
    return [str(row["migration"]) for row in rows]


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [str(row["name"]) for row in rows]


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """Row count for a known table. Rejects unknown table names."""
    if table not in _IDENTIFIER_TABLES:
        raise ValueError(f"unknown table: {table!r}")
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])
