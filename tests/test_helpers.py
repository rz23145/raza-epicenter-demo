"""Label helpers: candidate extraction from a public page, fingerprint check."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import responses

from ceiling.db import repo
from ceiling.http.client import Client
from ceiling.labels.helpers import candidates_from_page, check_fingerprints
from ceiling.settings import Settings
from tests.conftest import FakeClock, fixture_text

NOW = "2026-09-08T00:00:00Z"
ALLOW_ALL = "User-agent: *\nAllow: /\n"


def test_candidates_from_page(
    settings: Settings,
    conn: sqlite3.Connection,
    fake_clock: FakeClock,
    tmp_path: Path,
) -> None:
    directory_html = (
        "<html><body>"
        "<a href='https://shopcandidate.example/'>A Shopify store</a>"
        "<a href='https://plaincandidate.example/about'>Not Shopify</a>"
        "<a href='/internal'>internal link</a>"
        "</body></html>"
    )
    out = tmp_path / "candidates.csv"
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get("https://directory.example/robots.txt", body=ALLOW_ALL)
        rsps.get("https://directory.example/stores", body=directory_html)
        rsps.get("https://shopcandidate.example/robots.txt", body=ALLOW_ALL)
        rsps.get(
            "https://shopcandidate.example/", body=fixture_text("shop_home.html")
        )
        rsps.get("https://plaincandidate.example/robots.txt", body=ALLOW_ALL)
        rsps.get(
            "https://plaincandidate.example/", body=fixture_text("non_shopify.html")
        )
        client = Client(
            settings, conn, "2026-09-08",
            clock=fake_clock.clock, sleeper=fake_clock.sleep,
        )
        n = candidates_from_page(client, "https://directory.example/stores", out)
    assert n == 1
    content = out.read_text()
    assert "shopcandidate.example" in content
    assert "plaincandidate.example" not in content
    assert "candidates-from-page" in content


def test_check_fingerprints_shares_and_flags(conn: sqlite3.Connection) -> None:
    scan_id = repo.get_or_create_scan(conn, "2026-09-08", "v1", "s", "c", NOW)
    flagged = repo.upsert_store(conn, "flagged-neg.example", NOW)
    clean = repo.upsert_store(conn, "clean-neg.example", NOW)
    unscanned = repo.upsert_store(conn, "unscanned-neg.example", NOW)
    for store in (flagged, clean, unscanned):
        repo.upsert_label(
            conn, store, "non_plus_negative", None,
            "https://example.com", "manual", None, "t", "2026-09-01", NOW,
        )
    repo.upsert_features(
        conn, scan_id, flagged, json.dumps({}), 1.0, json.dumps({}), True, NOW
    )
    repo.upsert_features(
        conn, scan_id, clean, json.dumps({}), 1.0, json.dumps({}), False, NOW
    )
    conn.commit()
    report = check_fingerprints(conn, "non_plus_negative")
    assert report["total"] == 3
    assert report["scanned"] == 2
    assert report["with_fingerprint"] == 1
    assert report["share_with_fingerprint"] == 0.5
    assert report["flagged_domains"] == ["flagged-neg.example"]
    assert report["unscanned_domains"] == ["unscanned-neg.example"]
