"""CLI subcommand coverage: stores, labels, hiring, validate, index, export.
All HTTP mocked."""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import responses
from typer.testing import CliRunner

from ceiling.cli import app
from ceiling.settings import CONTACT_EMAIL_ENV
from tests.conftest import REPO_ROOT, fixture_text

runner = CliRunner()
ALLOW_ALL = "User-agent: *\nAllow: /\n"
NOW = "2026-09-08T00:00:00Z"


@pytest.fixture()
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "research@example.com")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    assert runner.invoke(app, ["init"]).exit_code == 0
    return tmp_path


def db(project_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(project_dir / "data" / "ceiling.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_stores_add_and_detect(project_dir: Path) -> None:
    csv_path = project_dir / "stores.csv"
    csv_path.write_text("domain\nShopOne.example\nhttps://www.plain.example/x\n")
    result = runner.invoke(app, ["stores", "add", "--file", str(csv_path)])
    assert result.exit_code == 0, result.output
    assert "2" in result.output

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get("https://shopone.example/robots.txt", body=ALLOW_ALL)
        rsps.get("https://shopone.example/", body=fixture_text("shop_home.html"))
        rsps.get("https://plain.example/robots.txt", body=ALLOW_ALL)
        rsps.get("https://plain.example/", body=fixture_text("non_shopify.html"))
        result = runner.invoke(app, ["stores", "detect"])
    assert result.exit_code == 0, result.output
    assert "shopify=1" in result.output
    assert "not_shopify=1" in result.output

    conn = db(project_dir)
    rows = {
        r["domain"]: r["is_shopify"]
        for r in conn.execute("SELECT domain, is_shopify FROM stores")
    }
    assert rows == {"shopone.example": 1, "plain.example": 0}
    conn.close()


def write_valid_labels(labels_dir: Path) -> None:
    (labels_dir / "plus_positives.csv").write_text(
        "domain,source_url,source_type,added_by,added_at\n"
        "plusshop.example,https://example.com/showcase,shopify_showcase,t,2026-09-01\n"
    )
    (labels_dir / "non_plus_negatives.csv").write_text(
        "domain,source_url,source_type,added_by,added_at\n"
        "smallshop.example,https://example.com/dir,directory,t,2026-09-01\n"
    )
    (labels_dir / "confirmed_migrators.csv").write_text(
        "domain,migration_month,source_url,quote,added_by,added_at\n"
        "migrator.example,2025-06,https://example.com/case,quote,t,2026-01-01\n"
    )


def test_labels_ingest_and_check_fingerprints(project_dir: Path) -> None:
    write_valid_labels(project_dir / "data" / "labels")
    result = runner.invoke(app, ["labels", "ingest"])
    assert result.exit_code == 0, result.output
    assert "plus_positive: 1" in result.output

    result = runner.invoke(
        app, ["labels", "check-fingerprints", "--set", "non_plus_negatives"]
    )
    assert result.exit_code == 0, result.output
    assert "total labeled: 1" in result.output

    result = runner.invoke(app, ["labels", "check-fingerprints", "--set", "bogus"])
    assert result.exit_code == 1


def test_labels_ingest_rejects_bad_file(project_dir: Path) -> None:
    write_valid_labels(project_dir / "data" / "labels")
    (project_dir / "data" / "labels" / "plus_positives.csv").write_text(
        "domain,source_url,source_type,added_by,added_at\n"
        "badurl.example,not-a-url,manual,t,2026-09-01\n"
    )
    result = runner.invoke(app, ["labels", "ingest"])
    assert result.exit_code == 1
    assert "valid http" in result.output


def test_labels_candidates_from_page_cli(project_dir: Path) -> None:
    out = project_dir / "candidates.csv"
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get("https://directory.example/robots.txt", body=ALLOW_ALL)
        rsps.get(
            "https://directory.example/list",
            body="<a href='https://shopcandidate.example/'>store</a>",
        )
        rsps.get("https://shopcandidate.example/robots.txt", body=ALLOW_ALL)
        rsps.get(
            "https://shopcandidate.example/", body=fixture_text("shop_home.html")
        )
        result = runner.invoke(
            app,
            [
                "labels", "candidates-from-page",
                "--url", "https://directory.example/list",
                "--out", str(out),
            ],
        )
    assert result.exit_code == 0, result.output
    assert "wrote 1 candidates" in result.output


def test_scan_run_option_validation(project_dir: Path) -> None:
    result = runner.invoke(app, ["scan", "run"])
    assert result.exit_code == 1
    assert "exactly one" in result.output
    result = runner.invoke(app, ["scan", "run", "--panel", "v1"])
    assert result.exit_code == 1
    assert "has no stores" in result.output


def test_panel_scan_empty_panel_fails(project_dir: Path) -> None:
    result = runner.invoke(app, ["panel", "scan", "--version", "v9"])
    assert result.exit_code == 1
    assert "empty" in result.output


def seed_scanned_store(project_dir: Path, domain: str = "seeded.example") -> None:
    conn = db(project_dir)
    conn.execute(
        "INSERT INTO stores (domain, is_shopify, first_seen_at, created_at)"
        " VALUES (?, 1, ?, ?)",
        (domain, NOW, NOW),
    )
    store_id = conn.execute(
        "SELECT store_id FROM stores WHERE domain = ?", (domain,)
    ).fetchone()["store_id"]
    conn.execute(
        "INSERT INTO scans (scan_date, panel_version, started_at, git_sha,"
        " config_sha, created_at) VALUES ('2026-09-08', 'v1', ?, 's', 'c', ?)",
        (NOW, NOW),
    )
    scan_id = conn.execute("SELECT scan_id FROM scans").fetchone()["scan_id"]
    conn.execute(
        "INSERT INTO catalog_snapshots (scan_id, store_id,"
        " products_json_available, product_count, created_at)"
        " VALUES (?, ?, 1, 100, ?)",
        (scan_id, store_id, NOW),
    )
    conn.execute(
        "INSERT INTO features (scan_id, store_id, feature_json, ceiling_score,"
        " ceiling_components_json, plus_fingerprint_any, created_at)"
        " VALUES (?, ?, ?, 4.0, ?, 0, ?)",
        (
            scan_id,
            store_id,
            json.dumps({"workaround_app_count": 1, "product_count": 100}),
            json.dumps({"high_growth": {"active": False}}),
            NOW,
        ),
    )
    conn.commit()
    conn.close()


def test_panel_build_index_compute_and_plot_cli(project_dir: Path) -> None:
    seed_scanned_store(project_dir)
    result = runner.invoke(app, ["panel", "build", "--size", "5", "--version", "v1"])
    assert result.exit_code == 0, result.output
    assert "frozen" in result.output

    result = runner.invoke(app, ["index", "compute", "--version", "v1"])
    assert result.exit_code == 0, result.output
    assert "index rows written: 1" in result.output
    assert (project_dir / "data" / "exports" / "index_timeseries.csv").exists()

    result = runner.invoke(app, ["index", "plot", "--version", "v1"])
    assert result.exit_code == 0, result.output
    assert (project_dir / "data" / "exports" / "index_v1.png").exists()

    result = runner.invoke(app, ["index", "compute", "--version", "nope"])
    assert result.exit_code == 1


def test_validate_commands_cli(project_dir: Path) -> None:
    result = runner.invoke(app, ["validate", "crosssection"])
    assert result.exit_code == 0, result.output
    assert "crosssection_report.md" in result.output

    result = runner.invoke(app, ["validate", "falsepos", "--top", "5"])
    assert result.exit_code == 0, result.output
    review_csv = project_dir / "data" / "exports" / "falsepos_review.csv"
    assert review_csv.exists()

    result = runner.invoke(
        app, ["validate", "falsepos-summary", "--file", str(review_csv)]
    )
    assert result.exit_code == 0, result.output


def test_validate_reconcile_cli(project_dir: Path) -> None:
    disclosures = project_dir / "data" / "labels" / "disclosures.csv"
    disclosures.write_text(
        "quarter,metric,value,unit,source_url,page_ref\n"
        "2026Q2,total_mrr_usd_m,200,usd_millions,https://example.com/10q,p12\n"
        "2026Q2,plus_share_of_mrr_pct,33,percent,https://example.com/10q,p12\n"
        "2025Q2,plus_share_of_mrr_pct,31,percent,https://example.com/10q25,p11\n"
        "2026Q2,advanced_list_price_usd_monthly,399,usd,https://example.com/p,n\n"
        "2026Q2,plus_list_price_usd_monthly,2300,usd,https://example.com/p,n\n"
    )
    result = runner.invoke(
        app, ["validate", "reconcile", "--assumed-base", "100000"]
    )
    assert result.exit_code == 0, result.output
    assert "1901" in result.output
    assert (project_dir / "data" / "exports" / "reconcile_report.md").exists()

    disclosures.write_text("quarter,metric,value,unit,source_url,page_ref\n")
    result = runner.invoke(app, ["validate", "reconcile"])
    assert result.exit_code == 1
    assert "populate" in result.output


def test_hiring_discover_and_count_cli(project_dir: Path) -> None:
    # panel with one store whose home links a greenhouse board
    labels_dir = project_dir / "data" / "labels"
    (labels_dir / "panel.csv").write_text(
        "domain,added_at,panel_version\nhiring.example,2026-09-08,v1\n"
    )
    home = (
        "<html><body><a href='https://boards.greenhouse.io/acme'>Careers</a>"
        "</body></html>"
    )
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get("https://hiring.example/robots.txt", body=ALLOW_ALL)
        rsps.get("https://hiring.example/", body=home)
        for path in ("/pages/careers", "/pages/jobs", "/careers", "/jobs"):
            rsps.get(f"https://hiring.example{path}", status=404)
        result = runner.invoke(app, ["hiring", "discover", "--panel", "v1"])
    assert result.exit_code == 0, result.output
    assert "with a job board: 1" in result.output

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get("https://boards-api.greenhouse.io/robots.txt", body=ALLOW_ALL)
        rsps.get(
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
            body=(REPO_ROOT / "tests" / "fixtures" / "greenhouse.json").read_text(),
        )
        result = runner.invoke(app, ["hiring", "count"])
    assert result.exit_code == 0, result.output
    assert "matched postings: 1" in result.output

    conn = db(project_dir)
    posts = conn.execute("SELECT * FROM hiring_posts").fetchall()
    assert len(posts) == 1
    assert "shopify plus" in posts[0]["matched_terms_json"]
    conn.close()


def test_wayback_backtest_cli_without_migrators(project_dir: Path) -> None:
    result = runner.invoke(app, ["wayback", "backtest"])
    assert result.exit_code == 0, result.output
    assert "no confirmed migrators" in result.output


def test_export_samples_cli(project_dir: Path) -> None:
    exports = project_dir / "data" / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    big_csv = exports / "big.csv"
    big_csv.write_text("col\n" + "\n".join(str(i) for i in range(100)))
    (exports / "report.md").write_text("# report\n")
    result = runner.invoke(app, ["export", "samples"])
    assert result.exit_code == 0, result.output
    sample = exports / "samples" / "big.csv"
    assert sample.exists()
    assert sample.read_text() == big_csv.read_text()
    assert (exports / "samples" / "report.md").exists()
    today = datetime.now(UTC).date().isoformat()
    db_copy = exports / "samples" / f"ceiling_{today}.db"
    assert db_copy.exists()
    conn = sqlite3.connect(db_copy)
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    assert any(name == "stores" for (name,) in tables)
