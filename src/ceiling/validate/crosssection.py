"""Cross-sectional validation of the ceiling score against the label sets.

The expected pattern, which is discussed in the generated report rather than
hidden: Plus positives may show FEWER workaround apps than the highest scoring
negatives, because after upgrading they replaced the workaround with the
native feature. If workaround features show AUC below 0.5 on the
Plus-vs-non-Plus task, that is consistent with the thesis, not against it.
The cross-sectional test is therefore weak for workaround features; the
Wayback backtest is the real longitudinal test.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ceiling.db import repo
from ceiling.logging import get_logger

logger = get_logger("validate.crosssection")

BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 7

INVERSION_DISCUSSION = """\
## Reading these numbers: the workaround inversion

Plus positives may show fewer workaround apps than the highest scoring
negatives, because a store that upgraded replaced its workarounds with the
native feature. If workaround features show AUC below 0.5 on this
Plus-vs-non-Plus task, that is consistent with the thesis, not against it:
the workaround is a signature of strain BEFORE the upgrade, and this
cross-section observes stores AFTER their plan status settled. The
cross-sectional test is therefore a weak test of the workaround signal. The
volume proxies (catalog size, review scale) are the components this test can
meaningfully validate. The real test of the workaround signal is the Wayback
backtest, which observes migrators before their migration month.
"""

_NUMERIC_KEYS = [
    "workaround_app_count",
    "workaround_capability_count",
    "workaround_b2b",
    "workaround_multistore",
    "workaround_launch",
    "workaround_discount",
    "workaround_sso",
    "workaround_intl",
    "product_count",
    "catalog_growth_yoy",
    "products_last_90d",
    "review_count_max",
    "hreflang_alt_count",
]


@dataclass
class LabeledScore:
    domain: str
    y: int  # 1 = plus_positive, 0 = non_plus_negative
    score: float
    features: dict[str, object] = field(default_factory=dict)


def load_scored_labels(
    conn: sqlite3.Connection, scan_id: int
) -> list[LabeledScore]:
    out: list[LabeledScore] = []
    for label_set, y in (("plus_positive", 1), ("non_plus_negative", 0)):
        for row in repo.label_rows(conn, label_set):
            feature = repo.feature_row(conn, scan_id, int(row["store_id"]))
            if feature is None or feature["ceiling_score"] is None:
                continue
            out.append(
                LabeledScore(
                    domain=str(row["domain"]),
                    y=y,
                    score=float(feature["ceiling_score"]),
                    features=json.loads(str(feature["feature_json"])),
                )
            )
    return out


def auc_score(y: list[int], scores: list[float]) -> float:
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y, scores))


def bootstrap_auc_ci(
    y: list[int],
    scores: list[float],
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    y_arr = np.asarray(y)
    s_arr = np.asarray(scores)
    n = len(y_arr)
    values: list[float] = []
    for _ in range(n_resamples):
        idx = rng.integers(0, n, n)
        y_b = y_arr[idx]
        if y_b.min() == y_b.max():
            continue
        values.append(auc_score(list(y_b), list(s_arr[idx])))
    if not values:
        return float("nan"), float("nan")
    lo, hi = np.percentile(values, [2.5, 97.5])
    return float(lo), float(hi)


def precision_recall_at_top_decile(
    y: list[int], scores: list[float]
) -> tuple[float, float]:
    n = len(y)
    k = max(1, n // 10)
    order = np.argsort(scores)[::-1][:k]
    y_arr = np.asarray(y)
    hits = int(y_arr[order].sum())
    positives = int(y_arr.sum())
    return hits / k, (hits / positives if positives else 0.0)


def precision_recall_at_threshold(
    y: list[int], scores: list[float], threshold: float
) -> tuple[float, float, int]:
    y_arr = np.asarray(y)
    flagged = np.asarray(scores) >= threshold
    n_flagged = int(flagged.sum())
    hits = int(y_arr[flagged].sum())
    positives = int(y_arr.sum())
    precision = hits / n_flagged if n_flagged else 0.0
    recall = hits / positives if positives else 0.0
    return precision, recall, n_flagged


def size_bucket_aucs(
    y: list[int], scores: list[float], product_counts: list[float | None]
) -> dict[str, float | None]:
    """AUC within each product_count quartile (quartiles computed on the union
    of labeled stores with a known product count). Shows whether the signal is
    just 'big stores'."""
    known = [
        (yy, ss, pc)
        for yy, ss, pc in zip(y, scores, product_counts, strict=True)
        if pc is not None
    ]
    if len(known) < 8:
        return {"insufficient_product_count_data": None}
    counts = np.asarray([pc for _, _, pc in known], dtype=float)
    edges = np.percentile(counts, [25, 50, 75])
    buckets: dict[str, float | None] = {}
    labels = ["q1_smallest", "q2", "q3", "q4_largest"]
    assignments = np.digitize(counts, edges)
    for q in range(4):
        members = [known[i] for i in range(len(known)) if assignments[i] == q]
        ys = [m[0] for m in members]
        ss = [m[1] for m in members]
        if len(set(ys)) < 2:
            buckets[labels[q]] = None
        else:
            buckets[labels[q]] = auc_score(ys, ss)
    return buckets


def logistic_diagnostic(
    rows: list[LabeledScore],
) -> dict[str, object]:
    """Diagnostic 5-fold CV logistic regression. Nulls imputed to zero, which
    is stated in the report. The index never uses this model."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    matrix: list[list[float]] = []
    for row in rows:
        vector: list[float] = []
        for key in _NUMERIC_KEYS:
            value = row.features.get(key)
            vector.append(
                float(value)
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else 0.0
            )
        matrix.append(vector)
    x = np.asarray(matrix)
    y = np.asarray([r.y for r in rows])
    if len(set(y.tolist())) < 2 or len(rows) < 10:
        return {"error": "insufficient labeled rows for the diagnostic model"}
    model = make_pipeline(
        StandardScaler(), LogisticRegression(max_iter=1000, random_state=7)
    )
    n_folds = min(5, int(min(np.bincount(y))))
    if n_folds < 2:
        return {"error": "insufficient class balance for cross validation"}
    cv_auc = cross_val_score(model, x, y, cv=n_folds, scoring="roc_auc")
    model.fit(x, y)
    coefs = model.named_steps["logisticregression"].coef_[0]
    return {
        "cv_folds": n_folds,
        "cv_auc_mean": float(np.mean(cv_auc)),
        "cv_auc_std": float(np.std(cv_auc)),
        "coefficients": {
            key: float(c) for key, c in zip(_NUMERIC_KEYS, coefs, strict=True)
        },
    }


def run_crosssection(
    conn: sqlite3.Connection,
    ceiling_threshold: float,
    exports_dir: Path,
    scan_date: str | None = None,
) -> Path:
    """Produce crosssection_report.md and crosssection_scores.csv."""
    if scan_date is None:
        scan = repo.latest_scan(conn)
    else:
        scan = repo.fetch_one(
            conn,
            "SELECT * FROM scans WHERE scan_date = ?"
            " ORDER BY scan_id DESC LIMIT 1",
            (scan_date,),
        )
    exports_dir.mkdir(parents=True, exist_ok=True)
    report_path = exports_dir / "crosssection_report.md"
    scores_path = exports_dir / "crosssection_scores.csv"

    if scan is None:
        report_path.write_text(
            "# Cross-sectional validation\n\nNo scan exists yet. Run a scan of"
            " the labeled stores first.\n",
            encoding="utf-8",
        )
        scores_path.write_text("domain,label,ceiling_score\n", encoding="utf-8")
        logger.warning("crosssection: no scan available")
        return report_path

    rows = load_scored_labels(conn, int(scan["scan_id"]))
    with scores_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["domain", "label", "ceiling_score"])
        for r in sorted(rows, key=lambda r: -r.score):
            writer.writerow([r.domain, r.y, f"{r.score:.4f}"])

    lines: list[str] = ["# Cross-sectional validation", ""]
    lines.append(f"Scan date: {scan['scan_date']}")
    n_pos = sum(1 for r in rows if r.y == 1)
    n_neg = sum(1 for r in rows if r.y == 0)
    lines.append(f"Positives (plus_positive) with features: {n_pos}")
    lines.append(f"Negatives (non_plus_negative) with features: {n_neg}")

    if n_pos == 0 or n_neg == 0:
        lines += [
            "",
            ("Insufficient labeled, scanned stores to compute AUC. Both a"
            " positive and a negative set with scanned features are required."),
            "",
            INVERSION_DISCUSSION,
        ]
        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.warning("crosssection: insufficient labels (pos=%d neg=%d)", n_pos, n_neg)
        return report_path

    y = [r.y for r in rows]
    scores = [r.score for r in rows]
    base_rate = n_pos / len(rows)
    auc = auc_score(y, scores)
    ci_lo, ci_hi = bootstrap_auc_ci(y, scores)
    p_top, r_top = precision_recall_at_top_decile(y, scores)
    p_thr, r_thr, n_flagged = precision_recall_at_threshold(
        y, scores, ceiling_threshold
    )

    def single_feature_auc(key: str) -> float | None:
        values = [
            (r.y, r.features.get(key))
            for r in rows
            if isinstance(r.features.get(key), (int, float))
        ]
        ys = [v[0] for v in values]
        if len(set(ys)) < 2:
            return None
        return auc_score(ys, [float(v[1]) for v in values])  # type: ignore[arg-type]

    product_counts: list[float | None] = [
        float(pc)
        if isinstance((pc := r.features.get("product_count")), (int, float))
        else None
        for r in rows
    ]
    buckets = size_bucket_aucs(y, scores, product_counts)
    diagnostic = logistic_diagnostic(rows)

    lines += [
        f"Base rate: {base_rate:.3f}",
        "",
        "## Headline numbers",
        "",
        f"- AUC of ceiling_score: {auc:.3f}",
        (f"- Bootstrap 95% CI ({BOOTSTRAP_RESAMPLES} resamples, seed"
        f" {BOOTSTRAP_SEED}): [{ci_lo:.3f}, {ci_hi:.3f}]"),
        f"- Precision at top decile: {p_top:.3f}, recall: {r_top:.3f}",
        (f"- At threshold {ceiling_threshold}: precision {p_thr:.3f}, recall"
        f" {r_thr:.3f}, flagged {n_flagged}"),
        "",
        "## Single-feature AUCs (size control)",
        "",
        f"- product_count alone: {_fmt(single_feature_auc('product_count'))}",
        (f"- workaround_app_count alone:"
        f" {_fmt(single_feature_auc('workaround_app_count'))}"),
        "",
        "## AUC within product_count quartiles",
        "",
    ]
    for bucket, value in buckets.items():
        lines.append(f"- {bucket}: {_fmt(value)}")
    lines += ["", INVERSION_DISCUSSION, "## Diagnostic logistic regression", ""]
    if "error" in diagnostic:
        lines.append(f"Not computed: {diagnostic['error']}")
    else:
        lines.append(
            f"5-fold CV AUC: {diagnostic['cv_auc_mean']:.3f}"
            f" +/- {diagnostic['cv_auc_std']:.3f}"
            f" ({diagnostic['cv_folds']} folds)"
        )
        lines.append("")
        lines.append("Coefficients (standardized inputs, nulls imputed to zero,")
        lines.append("diagnostic only, the index uses the transparent score):")
        lines.append("")
        coefficients = diagnostic["coefficients"]
        assert isinstance(coefficients, dict)
        for key, coef in coefficients.items():
            lines.append(f"- {key}: {coef:+.3f}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("crosssection report written to %s", report_path)
    return report_path


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"
