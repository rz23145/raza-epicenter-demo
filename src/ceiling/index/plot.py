"""Index chart: share_ceiling and share_converted_since_prior on a dual axis
over scan dates, hiring posts as bars. Renders with a single point."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ceiling.db import repo
from ceiling.logging import get_logger

logger = get_logger("index.plot")


def plot_index(
    conn: sqlite3.Connection, panel_version: str, exports_dir: Path
) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = repo.index_values_for_version(conn, panel_version)
    if not rows:
        logger.warning("nothing to plot for panel version %s", panel_version)
        return None

    dates = [str(r["scan_date"]) for r in rows]
    share_ceiling = [
        float(r["share_ceiling"]) if r["share_ceiling"] is not None else None
        for r in rows
    ]
    share_converted = [
        float(r["share_converted_since_prior"])
        if r["share_converted_since_prior"] is not None
        else None
        for r in rows
    ]
    hiring = [
        int(r["hiring_plus_posts_month"])
        if r["hiring_plus_posts_month"] is not None
        else 0
        for r in rows
    ]

    fig, ax1 = plt.subplots(figsize=(9, 5))
    xs = range(len(dates))
    ax1.bar(xs, hiring, alpha=0.2, color="gray", label="hiring posts (month)")
    ceiling_pairs = [(x, y) for x, y in zip(xs, share_ceiling, strict=True) if y is not None]
    if ceiling_pairs:
        ax1.plot(
            [p[0] for p in ceiling_pairs],
            [p[1] for p in ceiling_pairs],
            "o-",
            color="tab:blue",
            label="share_ceiling",
        )
    ax1.set_ylabel("share_ceiling / hiring posts")
    ax1.set_xticks(list(xs))
    ax1.set_xticklabels(dates, rotation=45, ha="right")

    ax2 = ax1.twinx()
    converted_pairs = [
        (x, y) for x, y in zip(xs, share_converted, strict=True) if y is not None
    ]
    if converted_pairs:
        ax2.plot(
            [p[0] for p in converted_pairs],
            [p[1] for p in converted_pairs],
            "s--",
            color="tab:red",
            label="share_converted_since_prior",
        )
    ax2.set_ylabel("share_converted_since_prior")

    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2, loc="upper left")
    ax1.set_title(f"Ceiling index, panel {panel_version}")

    exports_dir.mkdir(parents=True, exist_ok=True)
    out = exports_dir / f"index_{panel_version}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("index chart written to %s", out)
    return out
