"""Review readers: count parsed from fixture, endpoints only fetched when the
full URL is literally present in the page."""

from __future__ import annotations

import sqlite3

import responses

from ceiling.detect.extract import extract
from ceiling.detect.reviews import (
    ReviewVendorConfig,
    load_review_widgets,
    read_reviews,
)
from ceiling.http.client import Client
from ceiling.settings import Settings
from tests.conftest import REPO_ROOT, FakeClock, fixture_text

CONFIG_DIR = REPO_ROOT / "config"


def test_judgeme_count_parsed_from_fixture() -> None:
    html = fixture_text("product_judgeme.html")
    configs = load_review_widgets(CONFIG_DIR)
    readings = read_reviews(
        configs, [extract(html)], [("https://shop.example/products/x", html)]
    )
    by_vendor = {r.vendor: r for r in readings}
    assert "judgeme" in by_vendor
    reading = by_vendor["judgeme"]
    assert reading.review_count == 1284
    assert reading.method == "html_widget"
    assert reading.source_url == "https://shop.example/products/x"


def test_no_vendor_detected_yields_none_row() -> None:
    html = "<html><body>no widgets here</body></html>"
    configs = load_review_widgets(CONFIG_DIR)
    readings = read_reviews(configs, [extract(html)], [("https://x.example/", html)])
    assert len(readings) == 1
    assert readings[0].vendor == "none"
    assert readings[0].review_count is None
    assert readings[0].method == "not_available"


def test_endpoint_not_fetched_when_not_literally_in_page(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    config = ReviewVendorConfig(
        vendor="fakevendor",
        detect_patterns=["fakevendor"],
        count_regexes=['"count":\\s*([0-9]+)'],
        endpoint_url_regex="https://api\\.fakevendor\\.example/counts/[a-z0-9]+",
    )
    # page references the vendor script but contains no literal endpoint URL
    html = (
        "<html><head>"
        "<script src='https://cdn.fakevendor.example/w.js'></script>"
        "<script>var shopId = 'abc123';</script>"
        "</head><body></body></html>"
    )
    with responses.RequestsMock() as rsps:
        client = Client(
            settings, conn, "2026-09-08",
            clock=fake_clock.clock, sleeper=fake_clock.sleep,
        )
        readings = read_reviews(
            [config],
            [extract(html)],
            [("https://shop.example/", html)],
            client=client,
            store_id=store_id,
            scan_id=scan_id,
        )
        # no HTTP call happened at all: the endpoint was never constructed
        assert len(rsps.calls) == 0
    assert readings[0].vendor == "fakevendor"
    assert readings[0].review_count is None
    assert readings[0].method == "not_available"


def test_endpoint_fetched_when_literally_present(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    endpoint = "https://api.fakevendor.example/counts/abc123"
    config = ReviewVendorConfig(
        vendor="fakevendor",
        detect_patterns=["fakevendor"],
        count_regexes=['"count":\\s*([0-9]+)'],
        endpoint_url_regex="https://api\\.fakevendor\\.example/counts/[a-z0-9]+",
    )
    html = (
        "<html><head>"
        "<script src='https://cdn.fakevendor.example/w.js'></script>"
        f"<script>fetch('{endpoint}');</script>"
        "</head><body></body></html>"
    )
    with responses.RequestsMock() as rsps:
        rsps.get(
            "https://api.fakevendor.example/robots.txt",
            body="User-agent: *\nAllow: /\n",
        )
        rsps.get(endpoint, json={"count": 777})
        client = Client(
            settings, conn, "2026-09-08",
            clock=fake_clock.clock, sleeper=fake_clock.sleep,
        )
        readings = read_reviews(
            [config],
            [extract(html)],
            [("https://shop.example/", html)],
            client=client,
            store_id=store_id,
            scan_id=scan_id,
        )
    assert readings[0].review_count == 777
    assert readings[0].method == "json_endpoint_referenced_in_page"
    assert readings[0].source_url == endpoint
