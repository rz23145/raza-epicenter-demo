"""False positive review: export the top-scoring negatives for hand review,
then tabulate the reviewer's categories."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path

from ceiling.db import repo
from ceiling.logging import get_logger

logger = get_logger("validate.falsepos")

REVIEWER_CATEGORIES = [
    "dropshipper",
    "agency demo store",
    "actually Plus and mislabeled",
    "legitimate ceiling case",
    "other",
]


def export_top_negatives(
    conn: sqlite3.Connection, exports_dir: Path, top: int = 30
) -> Path:
    scan = repo.latest_scan(conn)
    exports_dir.mkdir(parents=True, exist_ok=True)
    out = exports_dir / "falsepos_review.csv"
    rows: list[dict[str, object]] = []
    if scan is not None:
        for label_row in repo.label_rows(conn, "non_plus_negative"):
            feature = repo.feature_row(
                conn, int(scan["scan_id"]), int(label_row["store_id"])
            )
            if feature is None or feature["ceiling_score"] is None:
                continue
            features = json.loads(str(feature["feature_json"]))
            matches = repo.app_matches_for(
                conn, int(scan["scan_id"]), int(label_row["store_id"])
            )
            rows.append(
                {
                    "domain": str(label_row["domain"]),
                    "ceiling_score": float(feature["ceiling_score"]),
                    "components": str(feature["ceiling_components_json"] or ""),
                    "product_count": features.get("product_count"),
                    "review_count_max": features.get("review_count_max"),
                    "matched_app_keys": ";".join(
                        sorted(str(m["app_key"]) for m in matches)
                    ),
                }
            )
    rows.sort(key=lambda r: -float(str(r["ceiling_score"])))
    rows = rows[:top]
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "domain",
                "ceiling_score",
                "components",
                "product_count",
                "review_count_max",
                "matched_app_keys",
                "reviewer_category",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r["domain"],
                    r["ceiling_score"],
                    r["components"],
                    r["product_count"],
                    r["review_count_max"],
                    r["matched_app_keys"],
                    "",
                ]
            )
    logger.info("wrote %d top-scoring negatives to %s", len(rows), out)
    return out


def summarize_review(csv_path: Path) -> dict[str, int]:
    """Tabulate the reviewer_category column filled in by the human."""
    counts: Counter[str] = Counter()
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            category = (row.get("reviewer_category") or "").strip()
            counts[category if category else "(blank)"] += 1
    return dict(counts.most_common())
