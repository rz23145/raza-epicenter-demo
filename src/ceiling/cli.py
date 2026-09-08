"""Ceiling command line interface.

Phase 1 ships init and doctor. Later phases add stores, labels, scan,
wayback, hiring, validate, panel, index, and export commands.
"""

from __future__ import annotations

import platform
import sqlite3
from importlib import metadata
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console

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

app = typer.Typer(
    name="ceiling",
    help="Ceiling index pipeline. Public data only, robots-respecting, rate limited.",
    no_args_is_help=True,
)
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


@app.command()
def init(config_dir: Path = CONFIG_DIR_OPTION) -> None:
    """Create the database, apply migrations, validate config, check contact email."""
    settings = _load_settings_or_exit(config_dir)
    try:
        settings.require_contact_email()
    except SettingsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

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
    logger.info(
        "init complete: db=%s migrations_applied=%s total=%s",
        settings.paths.db_path,
        applied,
        len(all_applied),
    )


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
        f"app signatures: {counts.apps_total} total, "
        f"{counts.apps_verified} verified"
    )
    console.print(
        f"plus fingerprints: {counts.fingerprints_total} total, "
        f"{counts.fingerprints_verified} verified"
    )
    console.print(f"unverified signature count: {counts.unverified}")

    db_path = settings.paths.db_path
    if db_path.exists():
        conn = connect(db_path)
        try:
            migrations = repo.applied_migrations(conn)
            console.print(
                f"database: {db_path} ({len(migrations)} migrations applied)"
            )
            robots_fetches = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM fetches WHERE page_role = 'robots'"
                ).fetchone()["n"]
            )
            console.print(f"robots cache: {robots_fetches} robots.txt fetches recorded")
        except sqlite3.OperationalError as exc:
            console.print(f"database: {db_path} [red]schema error: {exc}[/red]")
        finally:
            conn.close()
    else:
        console.print(f"database: not created yet, run ceiling init ({db_path})")

    console.print("open items for the human owner:")
    for item in OPEN_ITEMS_FOR_HUMAN_OWNER:
        console.print(f"  - {item}")


if __name__ == "__main__":
    app()
