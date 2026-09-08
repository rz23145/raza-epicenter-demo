"""End-to-end crosssection and falsepos runs against a seeded database."""

from __future__ import annotations

import json
import sqlite3

from ceiling.db import repo
from ceiling.settings import Settings
from ceiling.validate.crosssection import run_crosssection
from ceiling.validate.falsepos import export_top_negatives

NOW = "2026-09-08T00:00:00Z"


def seed_labeled_scan(conn: sqlite3.Connection, n_pos: int = 8, n_neg: int = 8) -> int:
    scan_id = repo.get_or_create_scan(conn, "2026-09-08", "v1", "sha", "csha", NOW)
    for i in range(n_pos):
        store_id = repo.upsert_store(conn, f"pos{i}.example", NOW)
        repo.upsert_label(
            conn, store_id, "plus_positive", None,
            "https://example.com/src", "manual", None, "tester", "2026-09-01", NOW,
        )
        features = {
            "workaround_app_count": 1 if i % 2 else 0,
            "product_count": 800 + 10 * i,
            "catalog_growth_yoy": 0.6,
            "products_last_90d": 25,
            "review_count_max": 6000,
        }
        repo.upsert_features(
            conn, scan_id, store_id, json.dumps(features),
            5.0 + 0.1 * i, json.dumps({}), False, NOW,
        )
    for i in range(n_neg):
        store_id = repo.upsert_store(conn, f"neg{i}.example", NOW)
        repo.upsert_label(
            conn, store_id, "non_plus_negative", None,
            "https://example.com/src", "manual", None, "tester", "2026-09-01", NOW,
        )
        features = {
            "workaround_app_count": 0,
            "product_count": 20 + i,
            "catalog_growth_yoy": 0.0,
            "products_last_90d": 1,
            "review_count_max": 50,
        }
        repo.upsert_features(
            conn, scan_id, store_id, json.dumps(features),
            0.5 + 0.1 * i, json.dumps({}), False, NOW,
        )
    conn.commit()
    return scan_id


def test_run_crosssection_writes_report_and_scores(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    seed_labeled_scan(conn)
    report = run_crosssection(conn, 3.0, settings.paths.exports_dir)
    text = report.read_text()
    assert "AUC of ceiling_score" in text
    # scores are perfectly separated in this synthetic setup
    assert "1.000" in text
    assert "Bootstrap 95% CI" in text
    assert "workaround inversion" in text
    assert "Diagnostic logistic regression" in text
    scores = (settings.paths.exports_dir / "crosssection_scores.csv").read_text()
    assert "pos0.example" in scores
    assert "neg0.example" in scores


def test_run_crosssection_without_scan_is_truthful(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    report = run_crosssection(conn, 3.0, settings.paths.exports_dir)
    assert "No scan exists yet" in report.read_text()


def test_run_crosssection_with_only_negatives_is_truthful(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    seed_labeled_scan(conn, n_pos=0, n_neg=4)
    report = run_crosssection(conn, 3.0, settings.paths.exports_dir)
    assert "Insufficient labeled" in report.read_text()


def test_export_top_negatives(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    seed_labeled_scan(conn)
    out = export_top_negatives(conn, settings.paths.exports_dir, top=3)
    lines = out.read_text().strip().splitlines()
    assert lines[0].startswith("domain,ceiling_score")
    assert len(lines) == 4  # header + 3
    # sorted by score descending: neg7 has the highest negative score
    assert lines[1].startswith("neg7.example")
    assert lines[1].endswith(",")  # reviewer_category left blank
