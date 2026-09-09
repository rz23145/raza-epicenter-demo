"""Ceiling command line interface."""

from __future__ import annotations

import csv
import platform
import shutil
import sqlite3
from datetime import UTC, date, datetime
from importlib import metadata
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from ceiling import __version__
from ceiling.db import repo
from ceiling.db.connection import apply_migrations, connect
from ceiling.logging import setup_logging
from ceiling.settings import (
    CONTACT_EMAIL_ENV,
    Settings,
    SettingsError,
    config_sha,
    load_settings,
    signature_counts,
)
from ceiling.util import normalize_domain, utc_now_iso

app = typer.Typer(
    name="ceiling",
    help="Ceiling index pipeline. Public data only, robots-respecting, rate limited.",
    no_args_is_help=True,
)
stores_app = typer.Typer(help="Store universe management.", no_args_is_help=True)
labels_app = typer.Typer(help="Label CSV validation and helpers.", no_args_is_help=True)
scan_app = typer.Typer(help="Scans of stores or the panel.", no_args_is_help=True)
wayback_app = typer.Typer(help="Wayback Machine backtest.", no_args_is_help=True)
hiring_app = typer.Typer(help="Job board cross-check.", no_args_is_help=True)
validate_app = typer.Typer(help="Validation and reconciliation.", no_args_is_help=True)
panel_app = typer.Typer(help="Panel build and scan.", no_args_is_help=True)
index_app = typer.Typer(help="Index computation and plotting.", no_args_is_help=True)
export_app = typer.Typer(help="Exports.", no_args_is_help=True)
app.add_typer(stores_app, name="stores")
app.add_typer(labels_app, name="labels")
app.add_typer(scan_app, name="scan")
app.add_typer(wayback_app, name="wayback")
app.add_typer(hiring_app, name="hiring")
app.add_typer(validate_app, name="validate")
app.add_typer(panel_app, name="panel")
app.add_typer(index_app, name="index")
app.add_typer(export_app, name="export")

console = Console(soft_wrap=True)

CONFIG_DIR_OPTION = typer.Option(
    Path("config"),
    "--config-dir",
    help="Directory containing settings.yaml and the signature dictionaries.",
)

_KEY_DEPENDENCIES = (
    "requests",
    "beautifulsoup4",
    "lxml",
    "pandas",
    "numpy",
    "scikit-learn",
    "pydantic",
    "pyyaml",
    "typer",
    "rich",
    "tenacity",
    "tqdm",
    "matplotlib",
)

# Section 23 of the project charter: items only the human owner can resolve.
OPEN_ITEMS_FOR_HUMAN_OWNER = (
    (
        "Confirm the current Plus-gated feature list against Shopify's help "
        "center and update plus_native_evidence_url on every signature."
    ),
    "Read the evidence URL for every app signature and flip verified where confirmed.",
    (
        "Populate data/labels/disclosures.csv from the latest 10-K and quarterly "
        "deck with page references. Do not rely on remembered figures."
    ),
    (
        "Build the three label CSVs to target sizes. Every row needs a "
        "source_url that a reviewer can open."
    ),
    f"Set {CONTACT_EMAIL_ENV}.",
    "Hand-review the false positive export before writing the memo.",
)


def _load_settings_or_exit(config_dir: Path) -> Settings:
    try:
        return load_settings(config_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]Config file not found:[/red] {exc}")
        raise typer.Exit(1) from exc
    except (ValidationError, SettingsError) as exc:
        console.print(f"[red]Invalid settings:[/red]\n{exc}")
        raise typer.Exit(1) from exc


def _require_email(settings: Settings) -> None:
    try:
        settings.require_contact_email()
    except SettingsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _open_db(settings: Settings) -> sqlite3.Connection:
    conn = connect(settings.paths.db_path)
    apply_migrations(conn)
    return conn


def _setup(config_dir: Path, need_email: bool = True) -> tuple[Settings, sqlite3.Connection]:
    settings = _load_settings_or_exit(config_dir)
    if need_email:
        _require_email(settings)
    setup_logging(settings.paths.logs_dir)
    return settings, _open_db(settings)


def _today() -> date:
    return datetime.now(UTC).date()


def _read_domains_csv(path: Path) -> list[str]:
    domains: list[str] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("domain") or "").strip()
            if not raw:
                continue
            try:
                domains.append(normalize_domain(raw))
            except ValueError:
                console.print(f"[yellow]skipping unparseable domain: {raw!r}[/yellow]")
    seen: set[str] = set()
    unique: list[str] = []
    for d in domains:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


# top level commands


@app.command()
def init(config_dir: Path = CONFIG_DIR_OPTION) -> None:
    """Create the database, apply migrations, validate config, check contact email."""
    settings = _load_settings_or_exit(config_dir)
    _require_email(settings)
    for directory in (
        settings.paths.data_dir,
        settings.paths.raw_dir,
        settings.paths.exports_dir,
        settings.paths.exports_dir / "samples",
        settings.paths.logs_dir,
        settings.paths.labels_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(settings.paths.logs_dir)
    conn = connect(settings.paths.db_path)
    try:
        applied = apply_migrations(conn)
        all_applied = repo.applied_migrations(conn)
    finally:
        conn.close()
    if applied:
        console.print(f"Applied migrations: {', '.join(applied)}")
    else:
        console.print("Migrations: 0 new, schema already current")
    console.print(f"Database: {settings.paths.db_path}")
    console.print(f"Config sha256: {config_sha(config_dir)}")
    console.print(f"Contact email: {settings.contact_email}")
    logger.info("init complete: migrations=%s total=%s", applied, len(all_applied))


@app.command()
def doctor(config_dir: Path = CONFIG_DIR_OPTION) -> None:
    """Print environment, config, signature verification, and database status."""
    console.print(f"ceiling version: {__version__}")
    console.print(f"python: {platform.python_version()} ({platform.platform()})")
    console.print("dependency versions:")
    for name in _KEY_DEPENDENCIES:
        try:
            console.print(f"  {name}: {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            console.print(f"  {name}: [red]not installed[/red]")
    settings = _load_settings_or_exit(config_dir)
    console.print(f"config sha256: {config_sha(config_dir)}")
    if settings.contact_email:
        console.print(f"contact email: set ({settings.contact_email})")
    else:
        console.print(
            f"contact email: [red]NOT SET[/red], set {CONTACT_EMAIL_ENV} "
            "before any command that fetches"
        )
    counts = signature_counts(config_dir)
    console.print(
        f"app signatures: {counts.apps_total} total, {counts.apps_verified} verified"
    )
    console.print(
        f"plus fingerprints: {counts.fingerprints_total} total,"
        f" {counts.fingerprints_verified} verified"
    )
    console.print(f"unverified signature count: {counts.unverified}")
    db_path = settings.paths.db_path
    if db_path.exists():
        conn = connect(db_path)
        try:
            migrations = repo.applied_migrations(conn)
            console.print(f"database: {db_path} ({len(migrations)} migrations applied)")
            robots_fetches = repo.fetch_one(
                conn, "SELECT COUNT(*) AS n FROM fetches WHERE page_role = 'robots'"
            )
            n_robots = int(robots_fetches["n"]) if robots_fetches else 0
            console.print(f"robots cache: {n_robots} robots.txt fetches recorded")
        except sqlite3.OperationalError as exc:
            console.print(f"database: {db_path} [red]schema error: {exc}[/red]")
        finally:
            conn.close()
    else:
        console.print(f"database: not created yet, run ceiling init ({db_path})")
    console.print("open items for the human owner:")
    for item in OPEN_ITEMS_FOR_HUMAN_OWNER:
        console.print(f"  - {item}")


# stores


@stores_app.command("add")
def stores_add(
    file: Path = typer.Option(..., "--file", help="CSV with a domain column."),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Add domains to the store universe from a CSV with a domain column."""
    _settings, conn = _setup(config_dir, need_email=False)
    domains = _read_domains_csv(file)
    now = utc_now_iso()
    for domain in domains:
        repo.upsert_store(conn, domain, now)
    conn.commit()
    console.print(f"stores added or already present: {len(domains)}")


@stores_app.command("detect")
def stores_detect(
    config_dir: Path = CONFIG_DIR_OPTION,
    limit: int = typer.Option(0, "--limit", help="Max stores to check (0 = all)."),
) -> None:
    """Run is_shopify on stores not yet checked (fetches each home page)."""
    from ceiling.detect.extract import extract
    from ceiling.detect.shopify import is_shopify
    from ceiling.http.client import Client

    settings, conn = _setup(config_dir)
    unchecked = repo.stores_unchecked(conn)
    if limit:
        unchecked = unchecked[:limit]
    if not unchecked:
        console.print("no unchecked stores")
        return
    scan_date = _today().isoformat()
    scan_id = repo.get_or_create_scan(
        conn, scan_date, "detect", "n/a", config_sha(config_dir), utc_now_iso()
    )
    conn.commit()
    client = Client(settings, conn, scan_date)
    n_yes = n_no = n_fail = 0
    for row in unchecked:
        domain = str(row["domain"])
        result = client.get(f"https://{domain}/", "home", int(row["store_id"]), scan_id)
        if not result.ok:
            n_fail += 1
            continue
        ex = extract(result.text)
        verdict, evidence = is_shopify(result.text, result.headers, ex)
        repo.update_store_shopify(
            conn,
            int(row["store_id"]),
            verdict,
            utc_now_iso(),
            ex.myshopify_domain,
            "is_shopify evidence: " + "; ".join(evidence) if evidence else None,
        )
        conn.commit()
        n_yes += 1 if verdict else 0
        n_no += 0 if verdict else 1
    console.print(
        f"checked {len(unchecked)}: shopify={n_yes} not_shopify={n_no} failed={n_fail}"
    )


# labels


@labels_app.command("ingest")
def labels_ingest(config_dir: Path = CONFIG_DIR_OPTION) -> None:
    """Validate and load all label CSVs. Any error rejects the whole file."""
    from ceiling.labels.ingest import LabelValidationError, ingest_labels

    settings, conn = _setup(config_dir, need_email=False)
    try:
        counts = ingest_labels(conn, settings.paths.labels_dir)
    except LabelValidationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    for label_set, n in counts.items():
        console.print(f"{label_set}: {n} row(s) loaded")


@labels_app.command("candidates-from-page")
def labels_candidates_from_page(
    url: str = typer.Option(..., "--url", help="A public page listing stores."),
    out: Path = typer.Option(..., "--out", help="Output CSV for candidates."),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Extract Shopify storefront candidates from one public page. The output
    is for human review; this never writes to the label CSVs."""
    from ceiling.http.client import Client
    from ceiling.labels.helpers import candidates_from_page

    settings, conn = _setup(config_dir)
    client = Client(settings, conn, _today().isoformat())
    try:
        n = candidates_from_page(client, url, out)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"wrote {n} candidates to {out}")


@labels_app.command("check-fingerprints")
def labels_check_fingerprints(
    label_set: str = typer.Option(
        ..., "--set", help="plus_positives or non_plus_negatives"
    ),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Share of a label set showing any Plus fingerprint at the latest scan."""
    from ceiling.labels.helpers import check_fingerprints

    mapping = {
        "plus_positives": "plus_positive",
        "non_plus_negatives": "non_plus_negative",
    }
    if label_set not in mapping:
        console.print(f"[red]--set must be one of: {', '.join(mapping)}[/red]")
        raise typer.Exit(1)
    _settings, conn = _setup(config_dir, need_email=False)
    report = check_fingerprints(conn, mapping[label_set])
    console.print(f"label set: {report['label_set']}")
    console.print(f"total labeled: {report['total']}, scanned: {report['scanned']}")
    console.print(f"with any fingerprint: {report['with_fingerprint']}")
    share = report["share_with_fingerprint"]
    console.print(
        "share with fingerprint: "
        + (f"{share:.3f}" if isinstance(share, float) else "n/a (nothing scanned)")
    )
    flagged = report["flagged_domains"]
    if label_set == "non_plus_negatives" and isinstance(flagged, list) and flagged:
        console.print(
            "[yellow]negatives showing a Plus fingerprint, review for removal:[/yellow]"
        )
        for domain in flagged:
            console.print(f"  - {domain}")


# scan


@scan_app.command("run")
def scan_run(
    domains_file: Path | None = typer.Option(
        None, "--domains", help="CSV of domains to scan."
    ),
    panel_version: str | None = typer.Option(
        None, "--panel", help="Panel version to scan (e.g. v1)."
    ),
    scan_date: str | None = typer.Option(
        None, "--date", help="Scan date yyyy-mm-dd (default today, UTC)."
    ),
    limit: int = typer.Option(0, "--limit", help="Max stores (0 = all)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print URLs and robots verdicts, fetch nothing."
    ),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Scan stores: home, collection, product, products.json, reviews, score."""
    from ceiling import pipeline
    from ceiling.index.panel import load_panel

    settings, conn = _setup(config_dir)
    if (domains_file is None) == (panel_version is None):
        console.print("[red]provide exactly one of --domains or --panel[/red]")
        raise typer.Exit(1)
    if domains_file is not None:
        domains = _read_domains_csv(domains_file)
        version = "adhoc"
    else:
        assert panel_version is not None
        domains = load_panel(settings.paths.labels_dir, panel_version)
        version = panel_version
        if not domains:
            console.print(
                f"[red]panel version {panel_version} has no stores in"
                f" {settings.paths.labels_dir / 'panel.csv'}; build it with"
                " ceiling panel build[/red]"
            )
            raise typer.Exit(1)
    day = date.fromisoformat(scan_date) if scan_date else _today()

    if dry_run:
        results = pipeline.dry_run(
            settings, conn, domains, day.isoformat(), limit or None
        )
        table = Table("url", "robots verdict")
        for url, verdict in results:
            table.add_row(url, verdict)
        console.print(table)
        console.print("dry run: nothing was fetched except robots.txt")
        return

    unverified = signature_counts(config_dir).unverified
    if unverified:
        console.print(
            f"[yellow]warning: {unverified} signatures are unverified; their"
            " score contributions are halved[/yellow]"
        )
    stats = pipeline.run_scan(
        settings, conn, config_dir, domains, day, version, limit or None
    )
    console.print(
        f"scan {stats.scan_id} done: scanned={stats.scanned}"
        f" not_shopify={stats.not_shopify} skipped={stats.skipped}"
        f" failures={stats.failures}"
    )


# wayback


@wayback_app.command("backtest")
def wayback_backtest(
    offsets: str = typer.Option(
        "", "--offsets", help="Comma separated month offsets, e.g. -18,-12,-6,-3,3."
    ),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Pre/post migration comparison for confirmed migrators plus controls."""
    from ceiling.http.client import Client
    from ceiling.wayback.backtest import export_backtest, plot_backtest, run_backtest

    settings, conn = _setup(config_dir)
    offset_list = (
        [int(x) for x in offsets.split(",") if x.strip()]
        if offsets
        else settings.wayback.offsets_months
    )
    client = Client(settings, conn, _today().isoformat())
    summary = run_backtest(settings, conn, client, config_dir, offset_list)
    if not summary.rows:
        console.print(
            "no confirmed migrators labeled; nothing to backtest."
            " Populate data/labels/confirmed_migrators.csv and run"
            " ceiling labels ingest first."
        )
        return
    out = export_backtest(summary, settings.paths.exports_dir)
    chart = plot_backtest(summary, settings.paths.exports_dir)
    coverage = summary.coverage if summary.coverage is not None else 0.0
    console.print(f"backtest rows: {len(summary.rows)}")
    console.print(f"snapshot coverage: {coverage:.3f}")
    for group, series in summary.prevalence_by_offset.items():
        console.print(f"{group} workaround prevalence by offset: {series}")
    console.print(f"exported: {out}")
    if chart:
        console.print(f"chart: {chart}")


# hiring


@hiring_app.command("discover")
def hiring_discover(
    panel_version: str = typer.Option(..., "--panel", help="Panel version."),
    limit: int = typer.Option(0, "--limit", help="Max stores (0 = all)."),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Find Greenhouse, Lever, and Ashby board slugs linked from panel stores."""
    from ceiling.hiring.discover import discover_for_store
    from ceiling.http.client import Client
    from ceiling.index.panel import load_panel

    settings, conn = _setup(config_dir)
    domains = load_panel(settings.paths.labels_dir, panel_version)
    if not domains:
        console.print(f"[red]panel {panel_version} is empty[/red]")
        raise typer.Exit(1)
    if limit:
        domains = domains[:limit]
    scan_date = _today().isoformat()
    scan_id = repo.get_or_create_scan(
        conn, scan_date, "hiring", "n/a", config_sha(config_dir), utc_now_iso()
    )
    conn.commit()
    client = Client(settings, conn, scan_date)
    with_boards = 0
    for domain in domains:
        store_id = repo.upsert_store(conn, domain, utc_now_iso())
        found = discover_for_store(client, conn, store_id, domain, scan_id)
        if found:
            with_boards += 1
    coverage = with_boards / len(domains) if domains else 0.0
    console.print(
        f"panel stores checked: {len(domains)}, with a job board: {with_boards}"
        f" (coverage {coverage:.3f})"
    )


@hiring_app.command("count")
def hiring_count(config_dir: Path = CONFIG_DIR_OPTION) -> None:
    """Fetch postings for every discovered board and count Plus-related terms."""
    from ceiling.hiring.boards import fetch_postings
    from ceiling.hiring.count import monthly_counts, record_postings
    from ceiling.http.client import Client

    settings, conn = _setup(config_dir)
    boards = repo.company_boards(conn)
    if not boards:
        console.print("no boards discovered yet; run ceiling hiring discover first")
        return
    client = Client(settings, conn, _today().isoformat())
    total_matched = 0
    for row in boards:
        postings = fetch_postings(client, str(row["board"]), str(row["company_slug"]))
        if postings is None:
            continue
        total_matched += record_postings(
            conn,
            str(row["board"]),
            str(row["company_slug"]),
            int(row["store_id"]) if row["store_id"] is not None else None,
            postings,
            settings.hiring.terms,
        )
    console.print(f"boards fetched: {len(boards)}, matched postings: {total_matched}")
    counts = monthly_counts(conn)
    console.print(f"monthly counts by first_seen: {counts['by_first_seen']}")
    console.print(f"monthly counts by posted_at: {counts['by_posted_at']}")


# validate


@validate_app.command("crosssection")
def validate_crosssection(
    scan_date: str | None = typer.Option(None, "--scan-date", help="yyyy-mm-dd"),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """AUC, precision at k, size-bucket controls, bootstrap CI, diagnostic model."""
    from ceiling.validate.crosssection import run_crosssection

    settings, conn = _setup(config_dir, need_email=False)
    threshold = settings.score.thresholds.get("ceiling_score", 3.0)
    report = run_crosssection(conn, threshold, settings.paths.exports_dir, scan_date)
    console.print(f"report: {report}")
    console.print(report.read_text())


@validate_app.command("falsepos")
def validate_falsepos(
    top: int = typer.Option(30, "--top", help="How many top-scoring negatives."),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Export the top-scoring negatives for hand review."""
    from ceiling.validate.falsepos import export_top_negatives

    settings, conn = _setup(config_dir, need_email=False)
    out = export_top_negatives(conn, settings.paths.exports_dir, top)
    console.print(f"exported: {out}")
    from ceiling.validate.falsepos import REVIEWER_CATEGORIES

    console.print(
        "fill in reviewer_category ("
        + ", ".join(REVIEWER_CATEGORIES)
        + "), then run ceiling validate falsepos-summary --file "
        + str(out)
    )


@validate_app.command("falsepos-summary")
def validate_falsepos_summary(
    file: Path = typer.Option(..., "--file", help="The reviewed CSV."),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Tabulate the hand-reviewed false positive categories and write the
    summary table to data/exports/falsepos_summary.md."""
    from ceiling.validate.falsepos import render_summary_markdown, summarize_review

    settings = _load_settings_or_exit(config_dir)
    try:
        summary = summarize_review(file)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    markdown = render_summary_markdown(summary, file)
    out = settings.paths.exports_dir / "falsepos_summary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    console.print(markdown)
    console.print(f"written: {out}")


@validate_app.command("reconcile")
def validate_reconcile(
    assumed_base: int | None = typer.Option(
        None,
        "--assumed-base",
        help="Assumed self-serve merchant base when not disclosed. Stated as an"
        " assumption in the output.",
    ),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Implied upgrade arithmetic against disclosures.csv, with every input cited."""
    from ceiling.validate.reconcile import ReconcileError, run_reconcile

    settings, conn = _setup(config_dir, need_email=False)
    share_ceiling: float | None = None
    latest = repo.fetch_one(
        conn,
        "SELECT share_ceiling FROM index_values ORDER BY index_id DESC LIMIT 1",
    )
    if latest is not None and latest["share_ceiling"] is not None:
        share_ceiling = float(latest["share_ceiling"])
    try:
        report = run_reconcile(
            settings.paths.labels_dir / "disclosures.csv",
            share_ceiling=share_ceiling,
            assumed_selfserve_base=assumed_base,
        )
    except ReconcileError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(report.text())
    out = settings.paths.exports_dir / "reconcile_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.text(), encoding="utf-8")
    console.print(f"written to {out}")


# panel


@panel_app.command("build")
def panel_build(
    size: int = typer.Option(750, "--size", help="Panel size."),
    version: str = typer.Option(..., "--version", help="New panel version string."),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Construct and freeze a panel from scanned, eligible self-serve stores."""
    from ceiling.index.panel import PanelError, build_panel

    settings, conn = _setup(config_dir, need_email=False)
    try:
        selected = build_panel(conn, settings.paths.labels_dir, size, version)
    except PanelError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(
        f"panel {version} written with {len(selected)} stores to"
        f" {settings.paths.labels_dir / 'panel.csv'} (frozen)"
    )


@panel_app.command("scan")
def panel_scan(
    version: str = typer.Option(..., "--version", help="Panel version to scan."),
    limit: int = typer.Option(0, "--limit", help="Max stores (0 = all)."),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Run the full per-store pipeline across the panel for today."""
    from ceiling import pipeline
    from ceiling.index.panel import load_panel

    settings, conn = _setup(config_dir)
    domains = load_panel(settings.paths.labels_dir, version)
    if not domains:
        console.print(f"[red]panel {version} is empty; run ceiling panel build[/red]")
        raise typer.Exit(1)
    stats = pipeline.run_scan(
        settings, conn, config_dir, domains, _today(), version, limit or None
    )
    console.print(
        f"panel scan {stats.scan_id}: scanned={stats.scanned}"
        f" not_shopify={stats.not_shopify} skipped={stats.skipped}"
        f" failures={stats.failures}"
    )


# index


@index_app.command("compute")
def index_compute(
    version: str = typer.Option(..., "--version", help="Panel version."),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Write one index_values row per scan and export the time series CSV."""
    from ceiling.index.compute import compute_index, export_timeseries
    from ceiling.index.panel import load_panel

    settings, conn = _setup(config_dir, need_email=False)
    domains = load_panel(settings.paths.labels_dir, version)
    n = compute_index(settings, conn, version, domains)
    if n == 0:
        console.print(f"[red]no scans exist for panel version {version}[/red]")
        raise typer.Exit(1)
    out = export_timeseries(conn, version, settings.paths.exports_dir)
    console.print(f"index rows written: {n}, exported: {out}")
    for row in repo.index_values_for_version(conn, version):
        console.print(
            f"  {row['scan_date']}: share_ceiling={row['share_ceiling']}"
            f" share_converted={row['share_converted_since_prior']}"
            f" n_scanned={row['panel_n_scanned']}"
        )


@index_app.command("plot")
def index_plot(
    version: str = typer.Option(..., "--version", help="Panel version."),
    config_dir: Path = CONFIG_DIR_OPTION,
) -> None:
    """Render the index chart (renders with a single point)."""
    from ceiling.index.plot import plot_index

    settings, conn = _setup(config_dir, need_email=False)
    out = plot_index(conn, version, settings.paths.exports_dir)
    if out is None:
        console.print(f"[red]no index values for {version}; run index compute[/red]")
        raise typer.Exit(1)
    console.print(f"chart: {out}")


# export


@export_app.command("samples")
def export_samples(config_dir: Path = CONFIG_DIR_OPTION) -> None:
    """Copy every export CSV and markdown file, plus a dated copy of the
    SQLite database, into data/exports/samples/."""
    settings, conn = _setup(config_dir, need_email=False)
    exports = settings.paths.exports_dir
    samples = exports / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for pattern in ("*.csv", "*.md"):
        for path in sorted(exports.glob(pattern)):
            shutil.copy(path, samples / path.name)
            copied.append(path.name)
    db_name = f"ceiling_{datetime.now(UTC).date().isoformat()}.db"
    dest = sqlite3.connect(samples / db_name)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    copied.append(db_name)
    for name in copied:
        console.print(f"  {name}")
    console.print(f"copied {len(copied)} file(s) into {samples}")


if __name__ == "__main__":
    app()
