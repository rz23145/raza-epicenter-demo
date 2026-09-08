"""Shared fixtures: temp settings, database, fake clock. No test hits the network."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from ceiling.db import repo
from ceiling.db.connection import apply_migrations, connect
from ceiling.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeClock:
    """Monotonic clock that only advances when sleep is called."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "contact_email": "research@example.com",
            "paths": {
                "data_dir": str(tmp_path / "data"),
                "db_path": str(tmp_path / "data" / "ceiling.db"),
                "raw_dir": str(tmp_path / "data" / "raw"),
                "exports_dir": str(tmp_path / "data" / "exports"),
                "logs_dir": str(tmp_path / "data" / "logs"),
                "labels_dir": str(tmp_path / "data" / "labels"),
            },
        }
    )


@pytest.fixture()
def conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.paths.db_path)
    apply_migrations(connection)
    yield connection
    connection.close()


@pytest.fixture()
def store_and_scan(conn: sqlite3.Connection) -> tuple[int, int]:
    now = "2026-09-08T00:00:00Z"
    store_id = repo.upsert_store(conn, "shop.example", now)
    scan_id = repo.get_or_create_scan(conn, "2026-09-08", "test", "sha", "csha", now)
    conn.commit()
    return store_id, scan_id


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
