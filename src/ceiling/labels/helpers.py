"""Assistive label-building utilities. All read-only against public pages,
all robots-checked. These tools never write to the label CSVs directly: the
human reviews candidate output and copies rows in by hand.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from ceiling.db import repo
from ceiling.detect.shopify import is_shopify
from ceiling.http.client import Client
from ceiling.logging import get_logger
from ceiling.util import normalize_domain, utc_now_iso

logger = get_logger("labels.helpers")


def candidates_from_page(
    client: Client, page_url: str, out_path: Path, max_candidates: int = 200
) -> int:
    """Fetch one public page, extract external links, keep hosts that pass
    is_shopify on a home fetch, write candidates to out_path. Returns the
    number of candidates written."""
    page = client.get_external(page_url)
    if not page.ok:
        raise RuntimeError(
            f"could not fetch {page_url}:"
            f" status={page.status} skip_reason={page.skip_reason}"
        )
    soup = BeautifulSoup(page.text, "lxml")
    page_host = urlparse(page_url).netloc.lower()
    domains: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        absolute = urljoin(page_url, href.strip())
        if not absolute.startswith("http"):
            continue
        try:
            domain = normalize_domain(absolute)
        except ValueError:
            continue
        if domain == normalize_domain(page_host) or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
        if len(domains) >= max_candidates:
            break

    now = utc_now_iso()
    written = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["domain", "source_url", "source_type", "added_by", "added_at", "evidence"]
        )
        for domain in domains:
            home = client.get_external(f"https://{domain}/")
            if not home.ok:
                logger.debug("candidate %s: home fetch failed, skipping", domain)
                continue
            verdict, evidence = is_shopify(home.text, home.headers)
            if not verdict:
                continue
            writer.writerow(
                [
                    domain,
                    page_url,
                    "directory",
                    "candidates-from-page",
                    now,
                    "; ".join(evidence),
                ]
            )
            written += 1
    logger.info("wrote %d Shopify candidates from %s to %s", written, page_url, out_path)
    return written


def check_fingerprints(
    conn: sqlite3.Connection, label_set: str
) -> dict[str, object]:
    """Share of a label set showing any fingerprint match at each store's
    latest scanned features row. This is the label quality check: positives
    should show fingerprints often, negatives should not. Flagged negatives
    are returned for manual removal."""
    rows = repo.label_rows(conn, label_set)
    total = len(rows)
    with_fp: list[str] = []
    unscanned: list[str] = []
    for row in rows:
        feature = repo.fetch_one(
            conn,
            "SELECT f.plus_fingerprint_any FROM features f"
            " JOIN scans s ON s.scan_id = f.scan_id"
            " WHERE f.store_id = ? ORDER BY s.scan_date DESC LIMIT 1",
            (int(row["store_id"]),),
        )
        if feature is None:
            unscanned.append(str(row["domain"]))
        elif int(feature["plus_fingerprint_any"]) == 1:
            with_fp.append(str(row["domain"]))
    scanned = total - len(unscanned)
    return {
        "label_set": label_set,
        "total": total,
        "scanned": scanned,
        "with_fingerprint": len(with_fp),
        "share_with_fingerprint": (len(with_fp) / scanned) if scanned else None,
        "flagged_domains": with_fp,
        "unscanned_domains": unscanned,
    }
