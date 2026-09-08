"""Pre/post migration comparison using the Wayback Machine.

For every confirmed migrator, snapshots at fixed offsets relative to the
migration month. A matched control group of non-migrating negatives (matched
on product count decile when catalog data exists, else a seeded random
sample) is replayed at the same calendar months, anchored on the median
migration month, so the comparison is workaround prevalence in
migrators-before-migration versus non-migrators-at-the-same-calendar-time.

Known limitations, restated in the README: Wayback captures the home page
only, coverage is uneven for small stores, and archived HTML may omit
scripts loaded conditionally. Snapshot coverage (share of requested offsets
with a snapshot found) is a first-class output.
"""

from __future__ import annotations

import csv
import random
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ceiling.db import repo
from ceiling.detect.apps import compile_app_signatures, load_app_signatures
from ceiling.detect.fingerprints import compile_fingerprints, load_fingerprints
from ceiling.http.client import Client
from ceiling.logging import get_logger
from ceiling.settings import Settings
from ceiling.util import add_months
from ceiling.wayback.cdx import pick_snapshot, query_cdx
from ceiling.wayback.replay import replay_snapshot

logger = get_logger("wayback.backtest")

CONTROL_GROUP_SIZE = 20
RANDOM_SEED = 7


@dataclass
class BacktestRow:
    domain: str
    group: str  # migrator | control
    migration_month: str
    offset: int
    snapshot_found: bool
    workaround_any: bool | None
    workaround_capabilities: str | None
    plus_fingerprint_any: bool | None


@dataclass
class BacktestSummary:
    rows: list[BacktestRow] = field(default_factory=list)
    coverage: float | None = None
    prevalence_by_offset: dict[str, dict[int, float | None]] = field(
        default_factory=dict
    )
    earliest_workaround_offset: dict[str, int] = field(default_factory=dict)


def _select_controls(
    conn: sqlite3.Connection, migrator_ids: set[int]
) -> list[sqlite3.Row]:
    """Negatives, matched on product count decile against migrators when both
    sides have catalog data, else a seeded random sample. The method used is
    logged so the export is honest about it."""
    negatives = [
        r for r in repo.label_rows(conn, "non_plus_negative")
        if int(r["store_id"]) not in migrator_ids
    ]
    if not negatives:
        return []

    def product_count(store_id: int) -> int | None:
        row = repo.latest_catalog_for_store(conn, store_id)
        if row is None or row["product_count"] is None:
            return None
        return int(row["product_count"])

    migrator_counts = [
        c
        for c in (product_count(mid) for mid in migrator_ids)
        if c is not None
    ]
    with_counts = [
        (r, product_count(int(r["store_id"]))) for r in negatives
    ]
    usable = [(r, c) for r, c in with_counts if c is not None]

    rng = random.Random(RANDOM_SEED)
    if migrator_counts and len(usable) >= CONTROL_GROUP_SIZE:
        target_median = statistics.median(migrator_counts)
        usable.sort(key=lambda rc: abs(rc[1] - target_median))
        chosen = [r for r, _c in usable[:CONTROL_GROUP_SIZE]]
        logger.info(
            "control group matched on product count around migrator median %s",
            target_median,
        )
        return chosen
    sample = rng.sample(negatives, min(CONTROL_GROUP_SIZE, len(negatives)))
    logger.info(
        "control group is a seeded random sample of %d negatives"
        " (insufficient catalog data for decile matching)",
        len(sample),
    )
    return sample


def run_backtest(
    settings: Settings,
    conn: sqlite3.Connection,
    client: Client,
    config_dir: Path,
    offsets: list[int] | None = None,
) -> BacktestSummary:
    offsets = offsets or settings.wayback.offsets_months
    tolerance = settings.wayback.tolerance_days

    app_compiled = compile_app_signatures(load_app_signatures(config_dir))
    fp_entries = load_fingerprints(config_dir)
    fp_compiled = compile_fingerprints(fp_entries)

    migrators = repo.label_rows(conn, "confirmed_migrator")
    if not migrators:
        logger.warning("no confirmed migrators labeled; backtest has nothing to do")
        return BacktestSummary()
    migrator_ids = {int(r["store_id"]) for r in migrators}
    migration_months = [str(r["label_value"]) for r in migrators]
    reference_month = sorted(migration_months)[len(migration_months) // 2]

    controls = _select_controls(conn, migrator_ids)

    summary = BacktestSummary()
    plans: list[tuple[sqlite3.Row, str, str]] = [
        (row, "migrator", str(row["label_value"])) for row in migrators
    ] + [(row, "control", reference_month) for row in controls]

    for row, group, anchor_month in plans:
        store_id = int(row["store_id"])
        domain = str(row["domain"])
        target_months = [add_months(anchor_month, off) for off in offsets]
        cdx_rows = query_cdx(client, domain, min(target_months), max(target_months))
        for offset, target_month in zip(offsets, target_months, strict=True):
            timestamp = pick_snapshot(cdx_rows, target_month, tolerance)
            wb_id = replay_snapshot(
                client,
                conn,
                store_id,
                domain,
                target_month,
                offset,
                timestamp,
                app_compiled,
                fp_entries,
                fp_compiled,
            )
            snapshot_row = repo.fetch_one(
                conn, "SELECT * FROM wayback_snapshots WHERE wb_id = ?", (wb_id,)
            )
            found = (
                snapshot_row is not None
                and snapshot_row["http_status"] == 200
                and snapshot_row["body_path"] is not None
            )
            if found:
                apps, fps = repo.wayback_matches(conn, wb_id)
                capabilities = sorted({str(a["capability"]) for a in apps})
                summary.rows.append(
                    BacktestRow(
                        domain,
                        group,
                        anchor_month,
                        offset,
                        True,
                        len(apps) > 0,
                        ";".join(capabilities) if capabilities else "",
                        len(fps) > 0,
                    )
                )
            else:
                summary.rows.append(
                    BacktestRow(
                        domain, group, anchor_month, offset, False, None, None, None
                    )
                )

    requested = len(summary.rows)
    found_n = sum(1 for r in summary.rows if r.snapshot_found)
    summary.coverage = found_n / requested if requested else None

    for group in ("migrator", "control"):
        by_offset: dict[int, float | None] = {}
        for offset in offsets:
            rows = [
                r
                for r in summary.rows
                if r.group == group and r.offset == offset and r.snapshot_found
            ]
            by_offset[offset] = (
                sum(1 for r in rows if r.workaround_any) / len(rows)
                if rows
                else None
            )
        summary.prevalence_by_offset[group] = by_offset

    for domain in {r.domain for r in summary.rows if r.group == "migrator"}:
        hits = sorted(
            r.offset
            for r in summary.rows
            if r.domain == domain and r.snapshot_found and r.workaround_any
        )
        if hits:
            summary.earliest_workaround_offset[domain] = hits[0]

    return summary


def export_backtest(summary: BacktestSummary, exports_dir: Path) -> Path:
    exports_dir.mkdir(parents=True, exist_ok=True)
    out = exports_dir / "wayback_backtest.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "domain",
                "group",
                "migration_month",
                "offset",
                "snapshot_found",
                "workaround_any",
                "workaround_capabilities",
                "plus_fingerprint_any",
            ]
        )
        for r in summary.rows:
            writer.writerow(
                [
                    r.domain,
                    r.group,
                    r.migration_month,
                    r.offset,
                    int(r.snapshot_found),
                    "" if r.workaround_any is None else int(r.workaround_any),
                    r.workaround_capabilities or "",
                    "" if r.plus_fingerprint_any is None else int(r.plus_fingerprint_any),
                ]
            )
    return out


def plot_backtest(summary: BacktestSummary, exports_dir: Path) -> Path | None:
    """One chart: workaround prevalence by offset, two lines."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not summary.prevalence_by_offset:
        return None
    fig, ax = plt.subplots(figsize=(8, 5))
    for group, style in (("migrator", "o-"), ("control", "s--")):
        series = summary.prevalence_by_offset.get(group, {})
        xs = sorted(series)
        ys = [series[x] for x in xs]
        pairs = [(x, y) for x, y in zip(xs, ys, strict=True) if y is not None]
        if pairs:
            ax.plot(
                [p[0] for p in pairs],
                [p[1] for p in pairs],
                style,
                label=group,
            )
    ax.set_xlabel("months relative to migration (controls: calendar-matched)")
    ax.set_ylabel("share with any workaround app")
    ax.set_title("Workaround prevalence by offset")
    ax.legend()
    ax.grid(True, alpha=0.3)
    exports_dir.mkdir(parents=True, exist_ok=True)
    out = exports_dir / "wayback_backtest.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out
