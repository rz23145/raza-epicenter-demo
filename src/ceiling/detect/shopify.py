"""Is this domain a Shopify storefront.

Requires at least two independent pieces of evidence for a True verdict.
The evidence list is recorded in stores.notes so the verdict is auditable.
"""

from __future__ import annotations

from collections.abc import Mapping

from ceiling.detect.extract import ExtractResult, extract
from ceiling.util import host_of


def is_shopify(
    home_html: str,
    response_headers: Mapping[str, str],
    extracted: ExtractResult | None = None,
) -> tuple[bool, list[str]]:
    ex = extracted if extracted is not None else extract(home_html)
    evidence: list[str] = []

    asset_urls = ex.script_srcs + ex.link_hrefs
    if any(host_of(u) == "cdn.shopify.com" for u in asset_urls if "//" in u):
        evidence.append("cdn.shopify.com asset host")

    inline = ex.inline_script_text
    if "window.Shopify" in inline or "Shopify.theme" in inline:
        evidence.append("window.Shopify or Shopify.theme inline script")

    header_keys = {k.lower() for k in response_headers}
    if "x-shopid" in header_keys:
        evidence.append("X-ShopId response header")
    if "x-shopify-stage" in header_keys:
        evidence.append("X-Shopify-Stage response header")

    if "/cdn/shop/" in home_html:
        evidence.append("/cdn/shop/ asset path")

    if "shopify-section" in home_html:
        evidence.append("shopify-section class name")

    if ex.myshopify_domain:
        evidence.append(f"myshopify domain in inline script: {ex.myshopify_domain}")

    return len(evidence) >= 2, evidence
