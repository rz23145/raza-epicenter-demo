"""Per-store scan orchestration: Sections 8 through 11 of the charter.

Used by both `ceiling scan run` and `ceiling panel scan`. For each store:
home page, first collection linked from the home nav (falling back to
/collections/all), first product from that collection, /products.json
pagination, review readers, app and fingerprint matching, feature build,
transparent score. Every fetch is robots-checked and recorded.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ceiling.db import repo
from ceiling.detect.apps import compile_app_signatures, load_app_signatures, match_apps
from ceiling.detect.extract import ExtractResult, extract
from ceiling.detect.fingerprints import (
    compile_fingerprints,
    load_fingerprints,
    match_fingerprints,
)
from ceiling.detect.products import fetch_catalog
from ceiling.detect.reviews import load_review_widgets, read_reviews
from ceiling.detect.shopify import is_shopify
from ceiling.features.build import build_features
from ceiling.features.score import ceiling_score
from ceiling.http.client import Client, FetchResult
from ceiling.logging import get_logger
from ceiling.settings import Settings, config_sha
from ceiling.util import host_of, utc_now_iso

logger = get_logger("pipeline")


@dataclass
class ScanStats:
    scan_id: int = 0
    scanned: int = 0
    skipped: int = 0
    not_shopify: int = 0
    failures: int = 0
    per_store_notes: dict[str, str] = field(default_factory=dict)


def git_sha() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.strip()
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"


def _first_collection_url(home_html: str, base_url: str) -> tuple[str, str]:
    """(url, heuristic_branch). First nav link containing /collections/ that is
    not /collections/all, else /collections/all."""
    soup = BeautifulSoup(home_html, "lxml")
    for anchor in soup.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        if "/collections/" in href and not href.rstrip("/").endswith(
            "/collections/all"
        ):
            return urljoin(base_url, href), "nav_link"
    return urljoin(base_url, "/collections/all"), "collections_all_fallback"


def _first_product_url(collection_html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(collection_html, "lxml")
    for anchor in soup.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href")
        if isinstance(href, str) and re.search(r"/products/[^/?#]+", href):
            return urljoin(base_url, href)
    return None


def scan_store(
    client: Client,
    conn: sqlite3.Connection,
    settings: Settings,
    config_dir: Path,
    domain: str,
    scan_id: int,
    scan_date: date,
    compiled_apps: list[object] | None = None,
) -> str:
    """Scan one store. Returns a status string for the run summary."""
    now = utc_now_iso()
    store_id = repo.upsert_store(conn, domain, now)
    conn.commit()

    app_entries = load_app_signatures(config_dir)
    apps_compiled = compile_app_signatures(app_entries)
    fp_entries = load_fingerprints(config_dir)
    fp_compiled = compile_fingerprints(fp_entries)
    review_configs = load_review_widgets(config_dir)

    home_url = f"https://{domain}/"
    home = client.get(home_url, "home", store_id, scan_id)
    if home.skipped or not home.ok:
        logger.warning(
            "home fetch failed for %s: status=%s skip=%s",
            domain,
            home.status,
            home.skip_reason,
        )
        return "home_failed"

    home_html = home.text
    home_ex = extract(home_html)
    verdict, evidence = is_shopify(home_html, home.headers, home_ex)
    notes = "is_shopify evidence: " + "; ".join(evidence) if evidence else None
    if home.cross_host_redirect and home.final_url:
        notes = (notes or "") + f" | cross-host redirect to {host_of(home.final_url)}"
    repo.update_store_shopify(
        conn, store_id, verdict, now, home_ex.myshopify_domain, notes
    )
    conn.commit()
    if not verdict:
        return "not_shopify"

    pages: list[tuple[str, str, ExtractResult, FetchResult]] = [
        (home_url, home_html, home_ex, home)
    ]

    collection_url, heuristic = _first_collection_url(home_html, home_url)
    collection = client.get(collection_url, "collection", store_id, scan_id)
    if collection.ok:
        collection_html = collection.text
        collection_ex = extract(collection_html)
        pages.append((collection_url, collection_html, collection_ex, collection))
        product_url = _first_product_url(collection_html, collection_url)
        if product_url:
            product = client.get(product_url, "product", store_id, scan_id)
            if product.ok:
                pages.append(
                    (product_url, product.text, extract(product.text), product)
                )

    # record assets per fetch
    for url, _html, ex, fetch in pages:
        fetch_row = repo.find_fetch_in_scan(conn, scan_id, url)
        if fetch_row is None:
            continue
        fetch_id = int(fetch_row["fetch_id"])
        existing = repo.fetch_one(
            conn, "SELECT COUNT(*) AS n FROM assets WHERE fetch_id = ?", (fetch_id,)
        )
        if existing is not None and int(existing["n"]) > 0:
            continue
        assets: list[tuple[str, str, str | None]] = []
        for src in ex.script_srcs:
            assets.append(("script_src", src, host_of(src) if "//" in src else None))
        for href in ex.link_hrefs:
            assets.append(("link_href", href, host_of(href) if "//" in href else None))
        if ex.myshopify_domain:
            assets.append(("inline_script_marker", ex.myshopify_domain, None))
        for key, content in ex.meta:
            assets.append(("meta", f"{key}={content}"[:500], None))
        repo.insert_assets(conn, fetch_id, assets, now)
    conn.commit()

    extracts = [p[2] for p in pages]
    htmls = [p[1] for p in pages]

    app_matches = match_apps(apps_compiled, extracts, htmls)
    for m in app_matches:
        repo.upsert_app_match(
            conn,
            scan_id,
            store_id,
            m.key,
            m.capability or "",
            m.matched_on,
            m.pattern,
            m.verified,
            now,
        )

    fp_matches = match_fingerprints(fp_entries, fp_compiled, extracts, htmls, domain)
    for m in fp_matches:
        repo.upsert_fingerprint_match(
            conn, scan_id, store_id, m.key, m.matched_on, m.pattern, m.verified, now
        )
    conn.commit()

    catalog = fetch_catalog(
        client,
        domain,
        store_id,
        scan_id,
        scan_date,
        settings.scan.products_json_max_pages,
        settings.scan.products_json_page_limit,
    )
    repo.upsert_catalog_snapshot(conn, scan_id, store_id, catalog.as_row(), now)

    readings = read_reviews(
        review_configs,
        extracts,
        [(p[0], p[1]) for p in pages],
        client=client,
        store_id=store_id,
        scan_id=scan_id,
    )
    for reading in readings:
        repo.upsert_review_snapshot(
            conn,
            scan_id,
            store_id,
            reading.vendor,
            reading.review_count,
            reading.source_url,
            reading.method,
            now,
        )

    features = build_features(
        app_matches=app_matches,
        catalog=catalog,
        reviews=readings,
        home_extract=home_ex,
        products_json_available=catalog.products_json_available,
    )
    features["_meta"] = {"collection_heuristic": heuristic}
    total, components = ceiling_score(features, settings.score)
    repo.upsert_features(
        conn,
        scan_id,
        store_id,
        json.dumps(features),
        total,
        json.dumps(components),
        plus_fingerprint_any=len(fp_matches) > 0,
        now=now,
    )
    conn.commit()
    logger.info("scanned %s: score=%.2f apps=%d", domain, total, len(app_matches))
    return "scanned"


def run_scan(
    settings: Settings,
    conn: sqlite3.Connection,
    config_dir: Path,
    domains: list[str],
    scan_date: date,
    panel_version: str,
    limit: int | None = None,
    client: Client | None = None,
) -> ScanStats:
    scan_date_str = scan_date.isoformat()
    now = utc_now_iso()
    scan_id = repo.get_or_create_scan(
        conn, scan_date_str, panel_version, git_sha(), config_sha(config_dir), now
    )
    conn.commit()
    client = client or Client(settings, conn, scan_date_str)
    stats = ScanStats(scan_id=scan_id)
    todo = domains[:limit] if limit else domains
    for domain in todo:
        try:
            status = scan_store(
                client, conn, settings, config_dir, domain, scan_id, scan_date
            )
        except Exception:
            logger.exception("unexpected failure scanning %s", domain)
            stats.failures += 1
            stats.per_store_notes[domain] = "exception"
            continue
        stats.per_store_notes[domain] = status
        if status == "scanned":
            stats.scanned += 1
        elif status == "not_shopify":
            stats.not_shopify += 1
        else:
            stats.skipped += 1
    repo.finish_scan(conn, scan_id, utc_now_iso())
    conn.commit()
    return stats


def dry_run(
    settings: Settings,
    conn: sqlite3.Connection,
    domains: list[str],
    scan_date: str,
    limit: int | None = None,
) -> list[tuple[str, str]]:
    """Print every URL that would be fetched and its robots verdict, fetching
    nothing except robots.txt itself. Returns (url, verdict) pairs."""
    client = Client(settings, conn, scan_date)
    results: list[tuple[str, str]] = []
    todo = domains[:limit] if limit else domains
    for domain in todo:
        urls = [
            f"https://{domain}/",
            f"https://{domain}/collections/all",
            (f"https://{domain}/products.json?limit="
            f"{settings.scan.products_json_page_limit}&page=1"),
        ]
        for url in urls:
            allowed, reason = client.robots.allowed(url)
            verdict = "allowed" if allowed else "DISALLOWED"
            if reason:
                verdict += f" ({reason})"
            results.append((url, verdict))
    return results
