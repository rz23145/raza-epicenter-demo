"""Robots policy: disallow is skipped and recorded, unreachable is allowed
and recorded, Crawl-delay is honored."""

from __future__ import annotations

import sqlite3

import responses

from ceiling.http.client import Client
from ceiling.settings import Settings
from tests.conftest import FakeClock

HOST = "shop.example"


def make_client(
    settings: Settings, conn: sqlite3.Connection, fake_clock: FakeClock
) -> Client:
    return Client(
        settings,
        conn,
        "2026-09-08",
        clock=fake_clock.clock,
        sleeper=fake_clock.sleep,
    )


def test_disallowed_path_skipped_and_recorded(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    with responses.RequestsMock() as rsps:
        rsps.get(
            f"https://{HOST}/robots.txt",
            body="User-agent: *\nDisallow: /private\n",
        )
        client = make_client(settings, conn, fake_clock)
        result = client.get(
            f"https://{HOST}/private/page", "other", store_id, scan_id
        )
        assert result.skipped
        assert result.skip_reason == "robots_disallow"
        assert not result.robots_allowed
        # only robots.txt was requested
        assert len(rsps.calls) == 1

    row = conn.execute(
        "SELECT * FROM fetches WHERE url = ?", (f"https://{HOST}/private/page",)
    ).fetchone()
    assert row["robots_allowed"] == 0
    assert row["skip_reason"] == "robots_disallow"
    assert row["http_status"] is None

    robots_row = conn.execute(
        "SELECT * FROM fetches WHERE page_role = 'robots'"
    ).fetchone()
    assert robots_row is not None
    assert robots_row["http_status"] == 200


def test_unreachable_robots_treated_as_allowed_and_recorded(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", status=500)
        rsps.get(f"https://{HOST}/", body="<html>hello</html>")
        client = make_client(settings, conn, fake_clock)
        allowed, reason = client.robots.allowed(f"https://{HOST}/")
        assert allowed
        assert reason == "robots_unreachable"
        result = client.get(f"https://{HOST}/", "home", store_id, scan_id)
        assert result.ok

    robots_row = conn.execute(
        "SELECT * FROM fetches WHERE page_role = 'robots'"
    ).fetchone()
    assert robots_row["http_status"] == 500


def test_crawl_delay_honored_when_larger_than_configured(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    with responses.RequestsMock() as rsps:
        rsps.get(
            f"https://{HOST}/robots.txt",
            body="User-agent: *\nCrawl-delay: 10\n",
        )
        rsps.get(f"https://{HOST}/a", body="a")
        rsps.get(f"https://{HOST}/b", body="b")
        client = make_client(settings, conn, fake_clock)
        client.get(f"https://{HOST}/a", "other", store_id, scan_id)
        assert client.robots.crawl_delay(HOST) == 10.0
        t_before = fake_clock.t
        client.get(f"https://{HOST}/b", "other", store_id, scan_id)
        # the second page request waited at least the crawl delay
        assert fake_clock.t - t_before >= 10.0
