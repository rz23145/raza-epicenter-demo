"""Reconcile the panel signal against Shopify's own disclosures.

Reads data/labels/disclosures.csv, which the human populates from the latest
10-K and quarterly materials with page references. Never relies on remembered
figures: if a required metric is missing the command fails and says which.

Expected metric names (one row per quarter per metric):
- total_mrr_usd_m               total MRR in millions of USD
- plus_share_of_mrr_pct         Plus share of MRR, percent, if disclosed
- plus_merchant_count           if disclosed
- advanced_list_price_usd_monthly
- plus_list_price_usd_monthly
- selfserve_merchant_count      if disclosed, used for the panel bound
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from ceiling.logging import get_logger

logger = get_logger("validate.reconcile")

REQUIRED_METRICS = [
    "total_mrr_usd_m",
    "plus_share_of_mrr_pct",
    "advanced_list_price_usd_monthly",
    "plus_list_price_usd_monthly",
]


class ReconcileError(Exception):
    pass


@dataclass
class Disclosure:
    quarter: str
    metric: str
    value: float
    unit: str
    source_url: str
    page_ref: str


@dataclass
class ReconcileReport:
    lines: list[str] = field(default_factory=list)
    values: dict[str, float] = field(default_factory=dict)

    def add(self, line: str) -> None:
        self.lines.append(line)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def read_disclosures(path: Path) -> list[Disclosure]:
    rows: list[Disclosure] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for line_no, row in enumerate(csv.DictReader(fh), start=2):
            if not any((v or "").strip() for v in row.values()):
                continue
            try:
                rows.append(
                    Disclosure(
                        quarter=(row.get("quarter") or "").strip(),
                        metric=(row.get("metric") or "").strip(),
                        value=float((row.get("value") or "").replace(",", "")),
                        unit=(row.get("unit") or "").strip(),
                        source_url=(row.get("source_url") or "").strip(),
                        page_ref=(row.get("page_ref") or "").strip(),
                    )
                )
            except ValueError as exc:
                raise ReconcileError(
                    f"disclosures.csv line {line_no}: bad value: {exc}"
                ) from exc
    return rows


def _latest(rows: list[Disclosure], metric: str) -> Disclosure | None:
    candidates = [r for r in rows if r.metric == metric]
    return max(candidates, key=lambda r: r.quarter) if candidates else None


def _at_quarter(
    rows: list[Disclosure], metric: str, quarter: str
) -> Disclosure | None:
    for r in rows:
        if r.metric == metric and r.quarter == quarter:
            return r
    return None


def _cite(d: Disclosure) -> str:
    return f"{d.value:g} {d.unit} ({d.quarter}, {d.source_url}, {d.page_ref})"


def run_reconcile(
    disclosures_path: Path,
    share_ceiling: float | None = None,
    assumed_selfserve_base: int | None = None,
) -> ReconcileReport:
    rows = read_disclosures(disclosures_path)
    report = ReconcileReport()
    if not rows:
        raise ReconcileError(
            f"{disclosures_path} is empty. The human owner must populate it"
            " from the latest 10-K and quarterly deck with page references."
            f" Required metrics: {', '.join(REQUIRED_METRICS)}"
        )
    missing = [m for m in REQUIRED_METRICS if _latest(rows, m) is None]
    if missing:
        raise ReconcileError(
            f"disclosures.csv is missing required metrics: {', '.join(missing)}"
        )

    total_mrr = _latest(rows, "total_mrr_usd_m")
    plus_share = _latest(rows, "plus_share_of_mrr_pct")
    advanced_price = _latest(rows, "advanced_list_price_usd_monthly")
    plus_price = _latest(rows, "plus_list_price_usd_monthly")
    assert total_mrr and plus_share and advanced_price and plus_price

    report.add("# Reconciliation against disclosures")
    report.add("")
    report.add("Inputs, each cited to the row in disclosures.csv:")
    report.add(f"- total MRR: {_cite(total_mrr)}")
    report.add(f"- Plus share of MRR: {_cite(plus_share)}")
    report.add(f"- Advanced list price: {_cite(advanced_price)}")
    report.add(f"- Plus list price: {_cite(plus_price)}")
    report.add("")

    delta = plus_price.value - advanced_price.value
    report.values["monthly_delta_usd"] = delta
    report.add(
        f"Subscription revenue delta from one Advanced-to-Plus upgrade at list:"
        f" {plus_price.value:g} - {advanced_price.value:g} = {delta:g} USD/month"
    )

    total_mrr_usd = total_mrr.value * 1_000_000
    upgrades_for_1pp = (0.01 * total_mrr_usd) / delta
    report.values["upgrades_for_1pp_of_mrr"] = upgrades_for_1pp
    report.add(
        "Upgrades needed to move Plus share of MRR by one percentage point"
        " (approximation: denominator held fixed):"
        f" 0.01 x {total_mrr_usd:,.0f} / {delta:g} = {upgrades_for_1pp:,.0f}"
    )

    prior_quarter = _prior_year_quarter(total_mrr.quarter)
    prior_share = _at_quarter(rows, "plus_share_of_mrr_pct", prior_quarter)
    if prior_share is not None:
        share_change_pp = plus_share.value - prior_share.value
        implied_added_mrr = (share_change_pp / 100.0) * total_mrr_usd
        implied_upgrades = implied_added_mrr / delta
        report.values["implied_annual_upgrades"] = implied_upgrades
        report.add(
            f"Trailing change in Plus share ({prior_quarter} ->"
            f" {plus_share.quarter}): {prior_share.value:g}% ->"
            f" {plus_share.value:g}% = {share_change_pp:+.2f}pp"
        )
        report.add(
            "Implied annual upgrade count consistent with that change"
            " (attributing the whole change to Advanced-to-Plus upgrades at"
            f" list): {share_change_pp:+.2f}pp x {total_mrr_usd:,.0f} / 100 /"
            f" {delta:g} = {implied_upgrades:,.0f}"
        )
    else:
        report.add(
            f"No plus_share_of_mrr_pct row for {prior_quarter}; the implied"
            " annual upgrade count cannot be computed. Add the year-ago"
            " quarter to disclosures.csv."
        )

    report.add("")
    report.add("## Panel bound")
    base_row = _latest(rows, "selfserve_merchant_count")
    base = (
        int(base_row.value)
        if base_row is not None
        else assumed_selfserve_base
    )
    if share_ceiling is None or base is None:
        report.add(
            "Panel bound not computed. It needs a computed index"
            " (share_ceiling) and a self-serve base: either a"
            " selfserve_merchant_count disclosure row or an explicit"
            " --assumed-base argument, stated as an assumption."
        )
    else:
        source = (
            f"disclosed ({base_row.source_url}, {base_row.page_ref})"
            if base_row is not None
            else "ASSUMPTION supplied by the operator, not a disclosure"
        )
        flagged = share_ceiling * base
        report.values["flagged_stores_implied"] = flagged
        report.add(
            f"Self-serve base: {base:,} merchants ({source})."
            f" At share_ceiling = {share_ceiling:.3f}, the panel implies"
            f" {flagged:,.0f} flagged stores in the base."
        )
        implied = report.values.get("implied_annual_upgrades")
        if implied is not None and flagged > 0:
            rate = implied / flagged
            report.values["required_upgrade_rate_among_flagged"] = rate
            report.add(
                f"To account for the implied {implied:,.0f} annual upgrades,"
                f" {rate:.1%} of flagged stores would need to upgrade per"
                " year. If this rate is implausibly precise or above 100%,"
                " the signal cannot carry the claim and the README must say so."
            )
    logger.info("reconcile computed: %s", report.values)
    return report


def _prior_year_quarter(quarter: str) -> str:
    """'2026Q2' -> '2025Q2'. Falls back to the input on unparseable values."""
    if len(quarter) >= 6 and quarter[:4].isdigit() and "Q" in quarter:
        return f"{int(quarter[:4]) - 1}{quarter[4:]}"
    return quarter
