"""Validate and load the label CSVs.

Any error rejects the whole file with a line-numbered message. Rules:
- required columns present
- domains normalize cleanly
- source_url is a real http(s) URL
- no domain appears in both plus_positives and non_plus_negatives
- confirmed_migrators.migration_month parses as yyyy-mm and is at least
  3 months before today
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

from ceiling.db import repo
from ceiling.logging import get_logger
from ceiling.util import normalize_domain, parse_month, utc_now_iso

logger = get_logger("labels.ingest")

LABEL_FILES: dict[str, tuple[str, list[str]]] = {
    "plus_positives.csv": (
        "plus_positive",
        ["domain", "source_url", "source_type", "added_by", "added_at"],
    ),
    "non_plus_negatives.csv": (
        "non_plus_negative",
        ["domain", "source_url", "source_type", "added_by", "added_at"],
    ),
    "confirmed_migrators.csv": (
        "confirmed_migrator",
        ["domain", "migration_month", "source_url", "quote", "added_by", "added_at"],
    ),
}


class LabelValidationError(Exception):
    def __init__(self, filename: str, errors: list[str]) -> None:
        self.filename = filename
        self.errors = errors
        message = f"{filename}: {len(errors)} error(s)\n" + "\n".join(errors)
        super().__init__(message)


def _check_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _read_rows(
    path: Path, required: list[str]
) -> tuple[list[dict[str, str]], list[str]]:
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = [c for c in required if c not in header]
        if missing:
            return [], [f"line 1: missing required columns: {', '.join(missing)}"]
        rows: list[dict[str, str]] = []
        for line_no, row in enumerate(reader, start=2):
            cleaned = {k: (v or "").strip() for k, v in row.items() if k}
            if not any(cleaned.values()):
                continue
            for col in required:
                if col != "quote" and not cleaned.get(col):
                    errors.append(f"line {line_no}: empty required column {col}")
            cleaned["_line"] = str(line_no)
            rows.append(cleaned)
    return rows, errors


def validate_label_file(
    path: Path, label_set: str, required: list[str], today: date
) -> list[dict[str, str]]:
    rows, errors = _read_rows(path, required)
    seen: set[str] = set()
    for row in rows:
        line = row["_line"]
        try:
            domain = normalize_domain(row.get("domain", ""))
            row["domain"] = domain
        except ValueError as exc:
            errors.append(f"line {line}: {exc}")
            continue
        if domain in seen:
            errors.append(f"line {line}: duplicate domain {domain}")
        seen.add(domain)
        url = row.get("source_url", "")
        if not _check_url(url):
            errors.append(f"line {line}: source_url is not a valid http(s) URL: {url!r}")
        if label_set == "confirmed_migrator":
            month = row.get("migration_month", "")
            try:
                year, mon = parse_month(month)
            except ValueError as exc:
                errors.append(f"line {line}: {exc}")
                continue
            months_ago = (today.year * 12 + today.month) - (year * 12 + mon)
            if months_ago < 3:
                errors.append(
                    f"line {line}: migration_month {month} is less than 3 months"
                    " before today; too recent to confirm"
                )
    if errors:
        raise LabelValidationError(path.name, errors)
    return rows


def ingest_labels(
    conn: sqlite3.Connection, labels_dir: Path, today: date | None = None
) -> dict[str, int]:
    """Validate all label CSVs, then load them. Validation of every file
    happens before any write, so one bad file rejects the whole ingest."""
    today = today or datetime.now(UTC).date()
    validated: dict[str, list[dict[str, str]]] = {}
    domains_by_set: dict[str, set[str]] = {}
    for filename, (label_set, required) in LABEL_FILES.items():
        path = labels_dir / filename
        rows = validate_label_file(path, label_set, required, today)
        validated[label_set] = rows
        domains_by_set[label_set] = {r["domain"] for r in rows}

    overlap = domains_by_set["plus_positive"] & domains_by_set["non_plus_negative"]
    if overlap:
        raise LabelValidationError(
            "plus_positives.csv / non_plus_negatives.csv",
            [
                f"domain in both plus_positives and non_plus_negatives: {d}"
                for d in sorted(overlap)
            ],
        )

    now = utc_now_iso()
    counts: dict[str, int] = {}
    for label_set, rows in validated.items():
        for row in rows:
            store_id = repo.upsert_store(conn, row["domain"], now)
            repo.upsert_label(
                conn,
                store_id=store_id,
                label_set=label_set,
                label_value=row.get("migration_month") or None,
                source_url=row["source_url"],
                source_type=row.get("source_type", "manual") or "manual",
                quote=row.get("quote") or None,
                added_by=row["added_by"],
                added_at=row["added_at"],
                now=now,
            )
        counts[label_set] = len(rows)
    conn.commit()
    logger.info("labels ingested: %s", counts)
    return counts
