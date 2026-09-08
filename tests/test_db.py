"""Migrations apply in order, are idempotent, and enforce foreign keys."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ceiling.db import repo
from ceiling.db.connection import apply_migrations, connect

EXPECTED_TABLES = {
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


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "test.db")
    apply_migrations(connection)
    return connection


def test_migration_001_creates_all_tables(tmp_path: Path) -> None:
    connection = connect(tmp_path / "fresh.db")
    applied = apply_migrations(connection)
    assert applied == ["001_init.sql"]
    assert EXPECTED_TABLES <= set(repo.table_names(connection))


def test_reapply_is_noop(conn: sqlite3.Connection) -> None:
    assert apply_migrations(conn) == []
    assert repo.applied_migrations(conn) == ["001_init.sql"]


def test_foreign_keys_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO fetches"
            " (scan_id, store_id, url, page_role, fetched_at,"
            "  robots_allowed, created_at)"
            " VALUES (99, 99, 'https://example.com/', 'home',"
            "  '2026-09-08T00:00:00Z', 1, '2026-09-08T00:00:00Z')"
        )


def test_scan_unique_per_date_and_panel(conn: sqlite3.Connection) -> None:
    row = (
        "2026-09-08",
        "v1",
        "2026-09-08T00:00:00Z",
        "deadbeef",
        "cafebabe",
        "2026-09-08T00:00:00Z",
    )
    sql = (
        "INSERT INTO scans"
        " (scan_date, panel_version, started_at, git_sha, config_sha, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)"
    )
    conn.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, row)


def test_count_rows_rejects_unknown_table(conn: sqlite3.Connection) -> None:
    assert repo.count_rows(conn, "stores") == 0
    with pytest.raises(ValueError):
        repo.count_rows(conn, "sqlite_master; DROP TABLE stores")
