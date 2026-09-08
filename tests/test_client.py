"""Client behavior: spacing, backoff, host stop, cache, raw archive."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import responses

from ceiling.db import repo
from ceiling.http.client import Client
from ceiling.settings import Settings
from tests.conftest import FakeClock

HOST = "shop.example"
OTHER = "other.example"

ALLOW_ALL = "User-agent: *\nAllow: /\n"


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


def test_rate_limiter_enforces_minimum_spacing(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        rsps.get(f"https://{HOST}/a", body="a")
        rsps.get(f"https://{HOST}/b", body="b")
        rsps.get(f"https://{OTHER}/robots.txt", body=ALLOW_ALL)
        rsps.get(f"https://{OTHER}/c", body="c")
        client = make_client(settings, conn, fake_clock)

        client.get(f"https://{HOST}/a", "other", store_id, scan_id)
        t_a = fake_clock.t
        client.get(f"https://{HOST}/b", "other", store_id, scan_id)
        t_b = fake_clock.t
        # same-host spacing is at least 1 second
        assert t_b - t_a >= settings.rate_limits.per_host_min_interval_s

        client.get(f"https://{OTHER}/c", "other", store_id, scan_id)
        t_c = fake_clock.t
        # switching hosts costs at least the inter-host delay
        assert t_c - t_b >= settings.rate_limits.inter_host_min_interval_s


def test_429_triggers_backoff_then_success(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    url = f"https://{HOST}/page"
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        rsps.get(url, status=429)
        rsps.get(url, body="ok")
        client = make_client(settings, conn, fake_clock)
        sleeps_before = len(fake_clock.sleeps)
        result = client.get(url, "other", store_id, scan_id)
        assert result.ok
        # two attempts hit the wire
        page_calls = [c for c in rsps.calls if c.request.url == url]
        assert len(page_calls) == 2
        # at least one extra sleep beyond the first rate limit wait (the backoff)
        assert len(fake_clock.sleeps) >= sleeps_before + 2


def test_three_consecutive_429s_stop_the_host(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    url_a = f"https://{HOST}/a"
    url_b = f"https://{HOST}/b"
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        rsps.get(url_a, status=429)
        rsps.get(url_a, status=429)
        rsps.get(url_a, status=429)
        client = make_client(settings, conn, fake_clock)
        result_a = client.get(url_a, "other", store_id, scan_id)
        assert result_a.skipped
        assert result_a.skip_reason == "rate_limit_stop"
        calls_after_a = len(rsps.calls)

        result_b = client.get(url_b, "other", store_id, scan_id)
        assert result_b.skipped
        assert result_b.skip_reason == "rate_limit_stop"
        # no further HTTP call happened for the stopped host
        assert len(rsps.calls) == calls_after_a

    row = conn.execute("SELECT * FROM fetches WHERE url = ?", (url_b,)).fetchone()
    assert row["skip_reason"] == "rate_limit_stop"
    assert row["http_status"] is None


def test_cache_hit_avoids_second_request(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    url = f"https://{HOST}/page"
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        rsps.get(url, body="cached body")
        client = make_client(settings, conn, fake_clock)
        first = client.get(url, "other", store_id, scan_id)
        assert first.ok and not first.from_cache
        second = client.get(url, "other", store_id, scan_id)
        assert second.from_cache
        assert second.body == b"cached body"
        page_calls = [c for c in rsps.calls if c.request.url == url]
        assert len(page_calls) == 1

    # a later scan on the same day reuses the body from disk without refetching
    now = "2026-09-08T01:00:00Z"
    scan2 = repo.get_or_create_scan(conn, "2026-09-08", "test2", "sha", "csha", now)
    conn.commit()
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        client2 = make_client(settings, conn, fake_clock)
        third = client2.get(url, "other", store_id, scan2)
        assert third.from_cache
        assert third.body == b"cached body"
        page_calls = [c for c in rsps.calls if c.request.url == url]
        assert len(page_calls) == 0


def test_raw_archive_written_with_matching_sha(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    url = f"https://{HOST}/page"
    body = b"<html>archive me</html>"
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        rsps.get(url, body=body)
        client = make_client(settings, conn, fake_clock)
        result = client.get(url, "other", store_id, scan_id)

    assert result.body_path is not None
    path = Path(result.body_path)
    assert path.exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result.body_sha256
    sidecar = path.with_suffix(".json")
    assert sidecar.exists()
    meta = json.loads(sidecar.read_text())
    assert meta["url"] == url
    assert meta["http_status"] == 200

    row = conn.execute("SELECT * FROM fetches WHERE url = ?", (url,)).fetchone()
    assert row["body_sha256"] == result.body_sha256
    assert row["body_path"] == str(path)


def test_timeout_and_connection_error_recorded(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    import requests

    store_id, scan_id = store_and_scan
    url = f"https://{HOST}/down"
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        for _ in range(4):
            rsps.get(url, body=requests.ConnectionError("boom"))
        client = make_client(settings, conn, fake_clock)
        result = client.get(url, "other", store_id, scan_id)
        assert result.skipped
        assert result.skip_reason == "connection_error"

    row = conn.execute("SELECT * FROM fetches WHERE url = ?", (url,)).fetchone()
    assert row["skip_reason"] == "connection_error"
