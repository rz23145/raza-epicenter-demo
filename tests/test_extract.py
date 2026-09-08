"""Extraction from a fixture: script srcs, hrefs, myshopify domain, theme, hreflang."""

from __future__ import annotations

from ceiling.detect.extract import extract
from ceiling.detect.shopify import is_shopify
from tests.conftest import fixture_text


def test_extract_fields_from_fixture() -> None:
    result = extract(fixture_text("shop_home.html"))
    assert "https://cdn.shopify.com/s/files/1/0001/assets/theme.js" in result.script_srcs
    assert "https://cdn.sparklayer.io/main.js" in result.script_srcs
    assert "https://cdn.shopify.com/s/files/1/0001/theme.css" in result.link_hrefs
    assert result.myshopify_domain == "test-shop-1234.myshopify.com"
    assert result.theme_name == "Dawn"
    assert result.theme_id == 128755464321
    assert ("de", "https://shop-de.example/") in result.hreflang_alternates
    assert ("description", "A test Shopify storefront") in result.meta
    assert ("og:title", "Test Shop") in result.meta
    assert len(result.jsonld) == 1
    assert "window.Shopify" in result.inline_script_text


def test_is_shopify_requires_two_pieces_of_evidence() -> None:
    verdict, evidence = is_shopify(fixture_text("shop_home.html"), {})
    assert verdict
    assert len(evidence) >= 2

    verdict, evidence = is_shopify(fixture_text("non_shopify.html"), {})
    assert not verdict
    assert len(evidence) < 2


def test_is_shopify_counts_headers_as_evidence() -> None:
    html = "<html><body><div class='shopify-section'>x</div></body></html>"
    verdict, evidence = is_shopify(html, {"X-ShopId": "1234"})
    assert verdict
    assert any("X-ShopId" in e for e in evidence)
