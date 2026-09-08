"""Catalog pagination, truncation, created_at windows, robots-disallowed nulls."""

from __future__ import annotations

import sqlite3
from datetime import date

import responses

from ceiling.detect.products import _ProductSlim, compute_catalog, fetch_catalog
from ceiling.http.client import Client
from ceiling.settings import Settings
from tests.conftest import FakeClock

HOST = "shop.example"
ALLOW_ALL = "User-agent: *\nAllow: /\n"
SCAN_DATE = date(2026, 9, 8)


def make_client(
    settings: Settings, conn: sqlite3.Connection, fake_clock: FakeClock
) -> Client:
    return Client(
        settings, conn, "2026-09-08", clock=fake_clock.clock, sleeper=fake_clock.sleep
    )


def product(created: str, vendor: str = "acme", n_variants: int = 1) -> dict[str, object]:
    return {
        "created_at": created,
        "vendor": vendor,
        "variants": [
            {"price": "19.99", "available": True} for _ in range(n_variants)
        ],
    }


def page_url(page: int) -> str:
    return f"https://{HOST}/products.json?limit=250&page={page}"


def test_pagination_stops_on_empty_page(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        rsps.get(page_url(1), json={"products": [product("2026-08-01T00:00:00-04:00")]})
        rsps.get(page_url(2), json={"products": []})
        client = make_client(settings, conn, fake_clock)
        result = fetch_catalog(client, HOST, store_id, scan_id, SCAN_DATE, 40, 250)
    assert result.products_json_available
    assert result.product_count == 1
    assert result.pages_fetched == 2
    assert result.truncated is False


def test_truncation_flag_set_at_max_pages(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        rsps.get(page_url(1), json={"products": [product("2026-01-01T00:00:00Z")]})
        rsps.get(page_url(2), json={"products": [product("2026-02-01T00:00:00Z")]})
        client = make_client(settings, conn, fake_clock)
        result = fetch_catalog(client, HOST, store_id, scan_id, SCAN_DATE, 2, 250)
    assert result.truncated is True
    assert result.pages_fetched == 2
    assert result.product_count == 2


def test_created_windows_with_frozen_clock() -> None:
    products = [
        _ProductSlim("2026-08-15T00:00:00Z", "a", [{"price": "10.0", "available": True}]),
        _ProductSlim("2026-01-10T00:00:00Z", "a", [{"price": "20.0", "available": False}]),
        _ProductSlim("2025-03-01T00:00:00Z", "b", [{"price": "30.0", "available": True}]),
        _ProductSlim("2023-05-01T00:00:00Z", "c", [{"price": "40.0", "available": True}]),
    ]
    result = compute_catalog(products, SCAN_DATE, pages_fetched=1, truncated=False)
    assert result.product_count == 4
    # 2026-08-15 is within 90 days of 2026-09-08
    assert result.products_created_last_90d == 1
    # 2026-08-15 and 2026-01-10 within 365 days
    assert result.products_created_last_365d == 2
    # 2025-03-01 is between 366 and 730 days back; 2023-05-01 is older
    assert result.products_created_prior_365d == 1
    assert result.earliest_created_at == "2023-05-01"
    assert result.latest_created_at == "2026-08-15"
    assert result.variant_count == 4
    assert result.vendor_count == 3
    assert result.available_share == 0.75
    assert result.median_price == 25.0
    assert result.currency is None


def test_robots_disallowed_products_json_yields_nulls(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    with responses.RequestsMock() as rsps:
        rsps.get(
            f"https://{HOST}/robots.txt",
            body="User-agent: *\nDisallow: /products.json\n",
        )
        client = make_client(settings, conn, fake_clock)
        result = fetch_catalog(client, HOST, store_id, scan_id, SCAN_DATE, 40, 250)
    assert not result.products_json_available
    assert result.skip_reason == "robots_disallow"
    assert result.product_count is None
    assert result.median_price is None
    row = result.as_row()
    assert row["products_json_available"] == 0
    assert row["product_count"] is None


def test_html_response_treated_as_not_available(
    settings: Settings,
    conn: sqlite3.Connection,
    store_and_scan: tuple[int, int],
    fake_clock: FakeClock,
) -> None:
    store_id, scan_id = store_and_scan
    with responses.RequestsMock() as rsps:
        rsps.get(f"https://{HOST}/robots.txt", body=ALLOW_ALL)
        rsps.get(page_url(1), body="<html>password page</html>")
        client = make_client(settings, conn, fake_clock)
        result = fetch_catalog(client, HOST, store_id, scan_id, SCAN_DATE, 40, 250)
    assert not result.products_json_available
    assert result.skip_reason == "not_available"
    assert result.product_count is None
