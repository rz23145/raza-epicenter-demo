"""Catalog features from the public /products.json endpoint.

robots.txt is checked for /products.json explicitly; many stores disallow it.
If disallowed or unavailable, products_json_available is 0 and every catalog
feature is NULL. Raw JSON pages are archived like any other fetch. Only
aggregates are stored, never product titles or images.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ceiling.http.client import Client
from ceiling.logging import get_logger

logger = get_logger("detect.products")


@dataclass
class CatalogResult:
    products_json_available: bool
    skip_reason: str | None = None
    product_count: int | None = None
    pages_fetched: int | None = None
    truncated: bool | None = None
    earliest_created_at: str | None = None
    latest_created_at: str | None = None
    products_created_last_90d: int | None = None
    products_created_last_365d: int | None = None
    products_created_prior_365d: int | None = None
    variant_count: int | None = None
    vendor_count: int | None = None
    available_share: float | None = None
    median_price: float | None = None
    currency: str | None = None

    def as_row(self) -> dict[str, object]:
        return {
            "products_json_available": 1 if self.products_json_available else 0,
            "product_count": self.product_count,
            "pages_fetched": self.pages_fetched,
            "truncated": (
                None if self.truncated is None else (1 if self.truncated else 0)
            ),
            "earliest_created_at": self.earliest_created_at,
            "latest_created_at": self.latest_created_at,
            "products_created_last_90d": self.products_created_last_90d,
            "products_created_last_365d": self.products_created_last_365d,
            "products_created_prior_365d": self.products_created_prior_365d,
            "variant_count": self.variant_count,
            "vendor_count": self.vendor_count,
            "available_share": self.available_share,
            "median_price": self.median_price,
            "currency": self.currency,
        }


@dataclass
class _ProductSlim:
    created_at: str | None
    vendor: str | None
    variants: list[dict[str, Any]] = field(default_factory=list)


def _parse_created(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def compute_catalog(
    products: list[_ProductSlim],
    scan_date: date,
    pages_fetched: int,
    truncated: bool,
) -> CatalogResult:
    """Aggregate catalog features. Pure function so tests can freeze the clock
    by passing a fixed scan_date."""
    created_dates = [d for d in (_parse_created(p.created_at) for p in products) if d]
    d90 = scan_date - timedelta(days=90)
    d365 = scan_date - timedelta(days=365)
    d730 = scan_date - timedelta(days=730)

    variants = [v for p in products for v in p.variants]
    prices: list[float] = []
    available_flags: list[bool] = []
    for v in variants:
        price = v.get("price")
        if price is not None:
            try:
                prices.append(float(price))
            except (TypeError, ValueError):
                pass
        if "available" in v:
            available_flags.append(bool(v["available"]))

    vendors = {p.vendor for p in products if p.vendor}

    return CatalogResult(
        products_json_available=True,
        product_count=len(products),
        pages_fetched=pages_fetched,
        truncated=truncated,
        earliest_created_at=min(created_dates).isoformat() if created_dates else None,
        latest_created_at=max(created_dates).isoformat() if created_dates else None,
        products_created_last_90d=sum(1 for d in created_dates if d >= d90),
        products_created_last_365d=sum(1 for d in created_dates if d >= d365),
        products_created_prior_365d=sum(
            1 for d in created_dates if d730 <= d < d365
        ),
        variant_count=len(variants),
        vendor_count=len(vendors),
        available_share=(
            sum(available_flags) / len(available_flags) if available_flags else None
        ),
        median_price=statistics.median(prices) if prices else None,
        # /products.json exposes prices without a currency code; the column
        # stays NULL rather than guessing. Never fabricate a measurement.
        currency=None,
    )


def fetch_catalog(
    client: Client,
    domain: str,
    store_id: int,
    scan_id: int,
    scan_date: date,
    max_pages: int,
    page_limit: int,
) -> CatalogResult:
    products: list[_ProductSlim] = []
    pages_fetched = 0
    truncated = False

    for page in range(1, max_pages + 1):
        url = f"https://{domain}/products.json?limit={page_limit}&page={page}"
        result = client.get(url, "products_json", store_id, scan_id)
        if result.skipped or result.status != 200 or result.body is None:
            if pages_fetched == 0:
                return CatalogResult(
                    products_json_available=False,
                    skip_reason=result.skip_reason or "not_available",
                )
            # a mid-pagination failure ends the walk with what we have
            truncated = True
            break
        try:
            payload = json.loads(result.body)
        except ValueError:
            if pages_fetched == 0:
                return CatalogResult(
                    products_json_available=False, skip_reason="not_available"
                )
            truncated = True
            break
        page_products = payload.get("products") if isinstance(payload, dict) else None
        if not isinstance(page_products, list):
            if pages_fetched == 0:
                return CatalogResult(
                    products_json_available=False, skip_reason="not_available"
                )
            truncated = True
            break
        pages_fetched += 1
        if not page_products:
            break
        for item in page_products:
            if not isinstance(item, dict):
                continue
            raw_variants = item.get("variants")
            variants = [v for v in raw_variants if isinstance(v, dict)] if isinstance(
                raw_variants, list
            ) else []
            products.append(
                _ProductSlim(
                    created_at=item.get("created_at"),
                    vendor=item.get("vendor"),
                    variants=variants,
                )
            )
        if page == max_pages and page_products:
            truncated = True

    return compute_catalog(products, scan_date, pages_fetched, truncated)
