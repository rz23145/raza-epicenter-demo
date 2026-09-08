"""Label validation: overlaps rejected, bad months rejected, recent migration
months rejected, valid files load."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from ceiling.db import repo
from ceiling.labels.ingest import LabelValidationError, ingest_labels

TODAY = date(2026, 9, 8)


def write_labels(
    labels_dir: Path,
    positives: list[str],
    negatives: list[str],
    migrators: list[tuple[str, str]],
) -> None:
    labels_dir.mkdir(parents=True, exist_ok=True)
    header = "domain,source_url,source_type,added_by,added_at\n"
    with (labels_dir / "plus_positives.csv").open("w") as fh:
        fh.write(header)
        for d in positives:
            fh.write(f"{d},https://example.com/src,shopify_showcase,tester,2026-09-01\n")
    with (labels_dir / "non_plus_negatives.csv").open("w") as fh:
        fh.write(header)
        for d in negatives:
            fh.write(f"{d},https://example.com/src,directory,tester,2026-09-01\n")
    with (labels_dir / "confirmed_migrators.csv").open("w") as fh:
        fh.write("domain,migration_month,source_url,quote,added_by,added_at\n")
        for d, month in migrators:
            fh.write(
                f"{d},{month},https://example.com/case,a quote,tester,2026-09-01\n"
            )


def test_valid_labels_load(conn: sqlite3.Connection, tmp_path: Path) -> None:
    labels_dir = tmp_path / "labels"
    write_labels(
        labels_dir,
        ["plusshop.example", "WWW.PlusTwo.example"],
        ["smallshop.example"],
        [("migrator.example", "2025-06")],
    )
    counts = ingest_labels(conn, labels_dir, today=TODAY)
    assert counts == {
        "plus_positive": 2,
        "non_plus_negative": 1,
        "confirmed_migrator": 1,
    }
    # domain was normalized
    rows = repo.label_rows(conn, "plus_positive")
    assert {r["domain"] for r in rows} == {"plusshop.example", "plustwo.example"}
    migrator = repo.label_rows(conn, "confirmed_migrator")[0]
    assert migrator["label_value"] == "2025-06"


def test_overlapping_domains_rejected(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    labels_dir = tmp_path / "labels"
    write_labels(
        labels_dir, ["overlap.example"], ["overlap.example"], []
    )
    with pytest.raises(LabelValidationError, match="both"):
        ingest_labels(conn, labels_dir, today=TODAY)
    # nothing was written
    assert repo.count_rows(conn, "labels") == 0


def test_bad_month_rejected(conn: sqlite3.Connection, tmp_path: Path) -> None:
    labels_dir = tmp_path / "labels"
    write_labels(labels_dir, [], [], [("m.example", "June 2025")])
    with pytest.raises(LabelValidationError, match="yyyy-mm"):
        ingest_labels(conn, labels_dir, today=TODAY)


def test_recent_migration_month_rejected(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    labels_dir = tmp_path / "labels"
    write_labels(labels_dir, [], [], [("m.example", "2026-08")])
    with pytest.raises(LabelValidationError, match="less than 3 months"):
        ingest_labels(conn, labels_dir, today=TODAY)


def test_bad_source_url_rejected(conn: sqlite3.Connection, tmp_path: Path) -> None:
    labels_dir = tmp_path / "labels"
    write_labels(labels_dir, [], [], [])
    with (labels_dir / "plus_positives.csv").open("a") as fh:
        fh.write("shop.example,not-a-url,manual,tester,2026-09-01\n")
    with pytest.raises(LabelValidationError, match="valid http"):
        ingest_labels(conn, labels_dir, today=TODAY)


def test_missing_column_rejected(conn: sqlite3.Connection, tmp_path: Path) -> None:
    labels_dir = tmp_path / "labels"
    write_labels(labels_dir, [], [], [])
    (labels_dir / "plus_positives.csv").write_text("domain,source_url\n")
    with pytest.raises(LabelValidationError, match="missing required columns"):
        ingest_labels(conn, labels_dir, today=TODAY)
