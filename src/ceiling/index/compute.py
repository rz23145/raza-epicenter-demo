"""Index time series: one index_values row per scan of a panel version.

share_ceiling: share of scanned self-serve panel stores at or above the score
threshold (the leading read). share_converted_since_prior: share of prior
self-serve stores showing a verified Plus fingerprint this scan (the
confirmation). Denominators exclude robots skips and failed stores: only
stores with a features row count as scanned.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from ceiling.db import repo
from ceiling.hiring.count import count_for_month
from ceiling.index.panel import detect_conversions
from ceiling.logging import get_logger
from ceiling.settings import Settings
from ceiling.util import utc_now_iso

logger = get_logger("index.compute")


def compute_index(
    settings: Settings,
    conn: sqlite3.Connection,
    panel_version: str,
    panel_domains: list[str],
) -> int:
    """Compute index_values for every scan of the panel version. Returns the
    number of rows written."""
    scans = repo.scans_for_version(conn, panel_version)
    if not scans:
        logger.warning("no scans for panel version %s", panel_version)
        return 0
    threshold = settings.score.thresholds.get("ceiling_score", 3.0)
    panel_n = len(panel_domains)
    panel_set = set(panel_domains)
    written = 0
    prior_scan_id: int | None = None
    for scan in scans:
        scan_id = int(scan["scan_id"])
        feature_rows = [
            r
            for r in repo.features_for_scan(conn, scan_id)
            if str(r["domain"]) in panel_set or not panel_set
        ]
        # self-serve at this scan: no fingerprint at all (verified or not)
        fp_any = repo.fingerprint_store_ids(conn, scan_id, verified_only=False)
        selfserve_rows = [
            r for r in feature_rows if int(r["store_id"]) not in fp_any
        ]
        n_scanned = len(feature_rows)

        def share(rows: list[sqlite3.Row], predicate_key: str) -> float | None:
            if not rows:
                return None
            hits = 0
            for r in rows:
                features = json.loads(str(r["feature_json"]))
                if predicate_key == "ceiling":
                    if (
                        r["ceiling_score"] is not None
                        and float(r["ceiling_score"]) >= threshold
                    ):
                        hits += 1
                elif predicate_key == "workaround_any":
                    if int(features.get("workaround_app_count") or 0) > 0:
                        hits += 1
                elif predicate_key == "high_growth":
                    components = json.loads(str(r["ceiling_components_json"] or "{}"))
                    hg = components.get("high_growth", {})
                    if hg.get("active"):
                        hits += 1
            return hits / len(rows)

        if prior_scan_id is not None:
            conversions = detect_conversions(conn, scan_id, prior_scan_id)
            n_converted: int | None = conversions.n_converted
            n_prior: int | None = conversions.n_prior_selfserve
            share_converted = (
                conversions.n_converted / conversions.n_prior_selfserve
                if conversions.n_prior_selfserve
                else None
            )
        else:
            n_converted = None
            n_prior = None
            share_converted = None

        month = str(scan["scan_date"])[:7]
        repo.upsert_index_value(
            conn,
            scan_id=scan_id,
            panel_version=panel_version,
            panel_n=panel_n if panel_n else n_scanned,
            panel_n_scanned=n_scanned,
            share_ceiling=share(selfserve_rows, "ceiling"),
            share_workaround_any=share(selfserve_rows, "workaround_any"),
            share_high_growth=share(selfserve_rows, "high_growth"),
            share_converted_since_prior=share_converted,
            n_converted=n_converted,
            n_prior_selfserve=n_prior,
            hiring_plus_posts_month=count_for_month(conn, month),
            computed_at=utc_now_iso(),
            now=utc_now_iso(),
        )
        conn.commit()
        written += 1
        prior_scan_id = scan_id
    logger.info("index computed for %d scan(s) of %s", written, panel_version)
    return written


def export_timeseries(
    conn: sqlite3.Connection, panel_version: str, exports_dir: Path
) -> Path:
    exports_dir.mkdir(parents=True, exist_ok=True)
    out = exports_dir / "index_timeseries.csv"
    columns = [
        "scan_date",
        "panel_version",
        "panel_n",
        "panel_n_scanned",
        "share_ceiling",
        "share_workaround_any",
        "share_high_growth",
        "share_converted_since_prior",
        "n_converted",
        "n_prior_selfserve",
        "hiring_plus_posts_month",
        "computed_at",
    ]
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in repo.index_values_for_version(conn, panel_version):
            writer.writerow([row[c] for c in columns])
    return out
