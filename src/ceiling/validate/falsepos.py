"""False positive review: export the top-scoring negatives for hand review,
then tabulate the reviewer's categories."""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ceiling.db import repo
from ceiling.logging import get_logger

logger = get_logger("validate.falsepos")

REVIEWER_CATEGORIES = [
    "legitimate_ceiling_case",
    "possibly_plus_mislabeled",
    "volume_only_legit",
    "volume_only_dropship",
    "new_store_zero_base",
    "score_zero_not_flagged",
    "unknown",
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


@dataclass(frozen=True)
class ReviewSummary:
    """Tabulation of a hand-reviewed false positive export."""

    total_rows: int
    counts: dict[str, int]
    flagged_rows: int
    share_denominator: int
    flagged_shares: dict[str, float]


def summarize_review(csv_path: Path) -> ReviewSummary:
    """Tabulate the reviewer_category column filled in by the human.

    Only reviewer_category and ceiling_score are read; any extra columns
    (reviewer_note or otherwise) are ignored. Categories must come from
    REVIEWER_CATEGORIES. The flagged set is rows with ceiling_score > 0;
    shares are computed over that set, excluding rows the reviewer
    categorized as score_zero_not_flagged.
    """
    counts: dict[str, int] = {c: 0 for c in REVIEWER_CATEGORIES}
    flagged_counts: dict[str, int] = {c: 0 for c in REVIEWER_CATEGORIES}
    total_rows = 0
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        for required in ("reviewer_category", "ceiling_score"):
            if required not in fields:
                raise ValueError(f"{csv_path} is missing the {required} column")
        for row in reader:
            category = (row.get("reviewer_category") or "").strip()
            if category not in REVIEWER_CATEGORIES:
                raise ValueError(
                    f"{csv_path}: reviewer_category {category!r} is not in the"
                    f" accepted vocabulary {REVIEWER_CATEGORIES}; use 'unknown'"
                    " for undecided rows"
                )
            score = float(str(row.get("ceiling_score") or "0"))
            total_rows += 1
            counts[category] += 1
            if score > 0:
                flagged_counts[category] += 1
    flagged_rows = sum(flagged_counts.values())
    share_denominator = flagged_rows - flagged_counts["score_zero_not_flagged"]
    flagged_shares = {
        c: (flagged_counts[c] / share_denominator if share_denominator else 0.0)
        for c in REVIEWER_CATEGORIES
        if c != "score_zero_not_flagged"
    }
    return ReviewSummary(
        total_rows=total_rows,
        counts=counts,
        flagged_rows=flagged_rows,
        share_denominator=share_denominator,
        flagged_shares=flagged_shares,
    )


def render_summary_markdown(summary: ReviewSummary, source: Path) -> str:
    """Render the review summary as a markdown table plus derived lines."""
    lines = [
        "# False positive review summary",
        "",
        f"Source: {source} ({summary.total_rows} rows reviewed)",
        "",
        "| reviewer_category | count | share_of_flagged |",
        "| --- | ---: | ---: |",
    ]
    for category in REVIEWER_CATEGORIES:
        if category == "score_zero_not_flagged":
            share = "excluded"
        else:
            share = f"{summary.flagged_shares[category]:.3f}"
        lines.append(f"| {category} | {summary.counts[category]} | {share} |")
    lines += [
        "",
        (
            "Rows with ceiling_score > 0 (the real flagged set):"
            f" {summary.flagged_rows}"
        ),
        (
            "Share denominator (flagged set excluding score_zero_not_flagged):"
            f" {summary.share_denominator}"
        ),
        "",
    ]
    return "\n".join(lines)
