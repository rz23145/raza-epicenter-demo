"""Validation: AUC matches sklearn, four size buckets, reconcile arithmetic."""

from __future__ import annotations

from pathlib import Path

import pytest
from sklearn.metrics import roc_auc_score

from ceiling.validate.crosssection import (
    auc_score,
    bootstrap_auc_ci,
    precision_recall_at_threshold,
    precision_recall_at_top_decile,
    size_bucket_aucs,
)
from ceiling.validate.falsepos import summarize_review
from ceiling.validate.reconcile import ReconcileError, run_reconcile

Y = [1, 1, 1, 1, 0, 0, 0, 0, 1, 0]
S = [0.9, 0.8, 0.4, 0.7, 0.3, 0.2, 0.6, 0.1, 0.5, 0.35]


def test_auc_matches_sklearn_on_toy_set() -> None:
    assert auc_score(Y, S) == pytest.approx(float(roc_auc_score(Y, S)))


def test_bootstrap_ci_brackets_auc() -> None:
    lo, hi = bootstrap_auc_ci(Y, S, n_resamples=200, seed=7)
    auc = auc_score(Y, S)
    assert lo <= auc <= hi
    assert 0.0 <= lo <= hi <= 1.0


def test_precision_recall_helpers() -> None:
    precision, recall = precision_recall_at_top_decile(Y, S)
    # top decile of 10 is 1 store: the 0.9 scorer, a positive
    assert precision == 1.0
    assert recall == pytest.approx(0.2)
    precision, recall, n_flagged = precision_recall_at_threshold(Y, S, 0.5)
    assert n_flagged == 5
    assert precision == pytest.approx(4 / 5)
    assert recall == pytest.approx(4 / 5)


def test_size_bucket_split_has_four_buckets() -> None:
    y = [1, 0] * 8
    scores = [0.9, 0.1] * 8
    counts: list[float | None] = [
        10, 12, 20, 22, 100, 110, 200, 220, 11, 13, 21, 23, 105, 115, 205, 225
    ]
    buckets = size_bucket_aucs(y, scores, counts)
    assert set(buckets) == {"q1_smallest", "q2", "q3", "q4_largest"}


def write_disclosures(path: Path, include_prior_share: bool = True) -> None:
    lines = [
        "quarter,metric,value,unit,source_url,page_ref",
        "2026Q2,total_mrr_usd_m,200,usd_millions,https://example.com/10q,p12",
        "2026Q2,plus_share_of_mrr_pct,33,percent,https://example.com/10q,p12",
        "2026Q2,advanced_list_price_usd_monthly,399,usd,https://example.com/pricing,n/a",
        "2026Q2,plus_list_price_usd_monthly,2300,usd,https://example.com/plus,n/a",
    ]
    if include_prior_share:
        lines.append(
            "2025Q2,plus_share_of_mrr_pct,31,percent,https://example.com/10q-2025,p11"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_reconcile_arithmetic_on_known_inputs(tmp_path: Path) -> None:
    path = tmp_path / "disclosures.csv"
    write_disclosures(path)
    report = run_reconcile(path, share_ceiling=0.10, assumed_selfserve_base=100_000)
    assert report.values["monthly_delta_usd"] == 2300 - 399
    # 0.01 * 200_000_000 / 1901
    assert report.values["upgrades_for_1pp_of_mrr"] == pytest.approx(
        2_000_000 / 1901
    )
    # share moved 2pp: 0.02 * 200M / 1901
    assert report.values["implied_annual_upgrades"] == pytest.approx(
        4_000_000 / 1901
    )
    # 10% of 100k base flagged
    assert report.values["flagged_stores_implied"] == pytest.approx(10_000)
    assert report.values["required_upgrade_rate_among_flagged"] == pytest.approx(
        (4_000_000 / 1901) / 10_000
    )
    text = report.text()
    assert "https://example.com/10q" in text
    assert "ASSUMPTION" in text


def test_reconcile_missing_metric_fails(tmp_path: Path) -> None:
    path = tmp_path / "disclosures.csv"
    path.write_text(
        "quarter,metric,value,unit,source_url,page_ref\n"
        "2026Q2,total_mrr_usd_m,200,usd_millions,https://example.com,p1\n",
        encoding="utf-8",
    )
    with pytest.raises(ReconcileError, match="missing required metrics"):
        run_reconcile(path)


def test_reconcile_empty_file_fails(tmp_path: Path) -> None:
    path = tmp_path / "disclosures.csv"
    path.write_text("quarter,metric,value,unit,source_url,page_ref\n")
    with pytest.raises(ReconcileError, match="populate"):
        run_reconcile(path)


def test_falsepos_summary_tabulates(tmp_path: Path) -> None:
    csv_path = tmp_path / "review.csv"
    csv_path.write_text(
        "domain,ceiling_score,reviewer_category\n"
        "a.example,5.0,dropshipper\n"
        "b.example,4.0,dropshipper\n"
        "c.example,3.0,legitimate ceiling case\n"
        "d.example,2.0,\n",
        encoding="utf-8",
    )
    summary = summarize_review(csv_path)
    assert summary["dropshipper"] == 2
    assert summary["legitimate ceiling case"] == 1
    assert summary["(blank)"] == 1
