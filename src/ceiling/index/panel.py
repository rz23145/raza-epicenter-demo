"""Panel management and conversion detection.

The panel is a fixed, rescannable set of self-serve stores. Once a version is
written to data/labels/panel.csv it is frozen: new selections get new version
strings and all versions are kept. Conversion: a store with no verified Plus
fingerprint at the prior scan and at least one at this scan. Stores that go
dark (home 4xx or no longer Shopify) are attrition, never conversion.
"""

from __future__ import annotations

import csv
import random
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ceiling.db import repo
from ceiling.logging import get_logger
from ceiling.util import utc_now_iso

logger = get_logger("index.panel")

PANEL_SEED = 7


class PanelError(Exception):
    pass


def load_panel(labels_dir: Path, version: str) -> list[str]:
    path = labels_dir / "panel.csv"
    if not path.exists():
        return []
    domains: list[str] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("panel_version") or "").strip() == version:
                domains.append((row.get("domain") or "").strip())
    return [d for d in domains if d]


def build_panel(
    conn: sqlite3.Connection,
    labels_dir: Path,
    size: int,
    version: str,
    seed: int = PANEL_SEED,
) -> list[str]:
    """Select self-serve stores already in the database: is_shopify, no Plus
    fingerprint at the latest scan, products_json_available at the latest
    catalog snapshot, not labeled plus_positive. Stratified by product_count
    quartile so the panel is not all tiny stores."""
    if load_panel(labels_dir, version):
        raise PanelError(
            f"panel version {version} already exists in panel.csv and panels"
            " are frozen once written; choose a new version string"
        )
    latest = repo.latest_scan(conn)
    if latest is None:
        raise PanelError("no scan exists yet; scan candidate stores first")
    scan_id = int(latest["scan_id"])
    positive_ids = {
        int(r["store_id"]) for r in repo.label_rows(conn, "plus_positive")
    }
    fp_ids = repo.fingerprint_store_ids(conn, scan_id, verified_only=False)

    candidates: list[tuple[str, int | None]] = []
    for row in conn.execute(
        "SELECT s.store_id, s.domain, cs.products_json_available, cs.product_count"
        " FROM stores s"
        " JOIN catalog_snapshots cs ON cs.store_id = s.store_id AND cs.scan_id = ?"
        " WHERE s.is_shopify = 1",
        (scan_id,),
    ):
        store_id = int(row["store_id"])
        if store_id in positive_ids or store_id in fp_ids:
            continue
        if int(row["products_json_available"]) != 1:
            continue
        pc = row["product_count"]
        candidates.append(
            (str(row["domain"]), int(pc) if pc is not None else None)
        )
    if not candidates:
        raise PanelError(
            "no eligible stores: need scanned Shopify stores with"
            " products_json_available and no Plus fingerprint"
        )

    counts = sorted(pc for _d, pc in candidates if pc is not None)
    rng = random.Random(seed)
    selected: list[str]
    if len(counts) >= 4:
        q1 = counts[len(counts) // 4]
        q2 = counts[len(counts) // 2]
        q3 = counts[(3 * len(counts)) // 4]

        def bucket(pc: int | None) -> int:
            if pc is None:
                return 0
            if pc <= q1:
                return 0
            if pc <= q2:
                return 1
            if pc <= q3:
                return 2
            return 3

        by_bucket: dict[int, list[str]] = {0: [], 1: [], 2: [], 3: []}
        for domain, pc in candidates:
            by_bucket[bucket(pc)].append(domain)
        per_bucket = max(1, size // 4)
        selected = []
        for b in range(4):
            pool = by_bucket[b]
            rng.shuffle(pool)
            selected.extend(pool[:per_bucket])
        # top up from the remainder if buckets were short
        remainder = [d for d, _pc in candidates if d not in set(selected)]
        rng.shuffle(remainder)
        selected.extend(remainder[: max(0, size - len(selected))])
    else:
        pool = [d for d, _pc in candidates]
        rng.shuffle(pool)
        selected = pool[:size]
    selected = selected[:size]

    path = labels_dir / "panel.csv"
    now = utc_now_iso()
    exists = path.exists()
    labels_dir.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not exists or path.stat().st_size == 0:
            writer.writerow(["domain", "added_at", "panel_version"])
        for domain in selected:
            writer.writerow([domain, now, version])
    logger.info("panel %s written with %d stores", version, len(selected))
    return selected


@dataclass
class ConversionResult:
    n_converted: int = 0
    n_prior_selfserve: int = 0
    converted_domains: list[str] = field(default_factory=list)
    attrition_domains: list[str] = field(default_factory=list)


def detect_conversions(
    conn: sqlite3.Connection, current_scan_id: int, prior_scan_id: int
) -> ConversionResult:
    """Conversions between two scans of the same panel, using only verified
    fingerprints. Denominator: stores scanned in both, self-serve at prior."""
    prior_features = {
        int(r["store_id"]): r for r in repo.features_for_scan(conn, prior_scan_id)
    }
    current_features = {
        int(r["store_id"]): r for r in repo.features_for_scan(conn, current_scan_id)
    }
    prior_verified = repo.fingerprint_store_ids(conn, prior_scan_id, verified_only=True)
    current_verified = repo.fingerprint_store_ids(
        conn, current_scan_id, verified_only=True
    )

    result = ConversionResult()
    for store_id, prior_row in prior_features.items():
        was_selfserve = store_id not in prior_verified
        if not was_selfserve:
            continue
        if store_id not in current_features:
            # went dark or failed this scan: attrition, never conversion
            result.attrition_domains.append(str(prior_row["domain"]))
            continue
        result.n_prior_selfserve += 1
        if store_id in current_verified:
            result.n_converted += 1
            result.converted_domains.append(str(prior_row["domain"]))
    return result
