"""CLI smoke tests: init, doctor, dry-run scan, and a full mocked mini scan.
Every HTTP call is mocked; no test hits the network."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
import responses
from typer.testing import CliRunner

from ceiling.cli import app
from ceiling.settings import CONTACT_EMAIL_ENV
from tests.conftest import REPO_ROOT, fixture_text

runner = CliRunner()

ALLOW_ALL = "User-agent: *\nAllow: /\n"


@pytest.fixture()
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "research@example.com")
    # rate limits are floors and stay untouched; the clock is mocked instead
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    return tmp_path


def test_init_refuses_without_contact_email(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CONTACT_EMAIL_ENV, raising=False)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "contact email" in result.output.lower()
    assert not (project_dir / "data" / "ceiling.db").exists()


def test_init_creates_db_and_is_idempotent(project_dir: Path) -> None:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (project_dir / "data" / "ceiling.db").exists()
    assert "001_init.sql" in result.output
    again = runner.invoke(app, ["init"])
    assert again.exit_code == 0, again.output
    assert "0 new" in again.output


def test_init_rejects_rate_limit_below_floor(project_dir: Path) -> None:
    settings_path = project_dir / "config" / "settings.yaml"
    text = settings_path.read_text().replace(
        "per_host_min_interval_s: 1.0", "per_host_min_interval_s: 0.1"
    )
    settings_path.write_text(text)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "invalid settings" in result.output.lower()


def test_doctor_reports_status(project_dir: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    out = result.output.lower()
    assert "config sha256" in out
    assert "unverified signature count" in out
    assert "open items for the human owner" in out
    assert "robots cache" in out


def test_scan_dry_run_prints_robots_verdicts(project_dir: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    domains_csv = project_dir / "domains.csv"
    domains_csv.write_text("domain\nshopone.example\nshoptwo.example\n")
    with responses.RequestsMock() as rsps:
        rsps.get("https://shopone.example/robots.txt", body=ALLOW_ALL)
        rsps.get(
            "https://shoptwo.example/robots.txt",
            body="User-agent: *\nDisallow: /products.json\n",
        )
        result = runner.invoke(
            app, ["scan", "run", "--domains", str(domains_csv), "--dry-run"]
        )
        # only the two robots.txt files were requested
        assert len(rsps.calls) == 2
    assert result.exit_code == 0, result.output
    assert "dry run" in result.output.lower()
    assert "DISALLOWED" in result.output
    assert "allowed" in result.output


def test_full_mocked_scan_populates_all_tables(project_dir: Path) -> None:
    """A two-fixture-domain scan through the real CLI path, HTTP fully mocked."""
    import sqlite3

    assert runner.invoke(app, ["init"]).exit_code == 0
    domains_csv = project_dir / "domains.csv"
    domains_csv.write_text("domain\nshopone.example\nplain.example\n")

    shop_home = fixture_text("shop_home.html")
    collection_html = (
        "<html><body><a href='/products/test-product'>A product</a></body></html>"
    )
    product_html = fixture_text("product_judgeme.html")

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get("https://shopone.example/robots.txt", body=ALLOW_ALL)
        rsps.get("https://shopone.example/", body=shop_home)
        rsps.get("https://shopone.example/collections/summer", body=collection_html)
        rsps.get("https://shopone.example/products/test-product", body=product_html)
        rsps.get(
            "https://shopone.example/products.json?limit=250&page=1",
            json={
                "products": [
                    {
                        "created_at": "2026-08-01T00:00:00Z",
                        "vendor": "acme",
                        "variants": [{"price": "19.99", "available": True}],
                    }
                ]
            },
        )
        rsps.get(
            "https://shopone.example/products.json?limit=250&page=2",
            json={"products": []},
        )
        rsps.get("https://plain.example/robots.txt", body=ALLOW_ALL)
        rsps.get("https://plain.example/", body=fixture_text("non_shopify.html"))
        result = runner.invoke(
            app, ["scan", "run", "--domains", str(domains_csv), "--date", "2026-09-08"]
        )
    assert result.exit_code == 0, result.output
    assert "scanned=1" in result.output
    assert "not_shopify=1" in result.output

    conn = sqlite3.connect(project_dir / "data" / "ceiling.db")
    conn.row_factory = sqlite3.Row
    features = conn.execute("SELECT * FROM features").fetchall()
    assert len(features) == 1
    assert features[0]["ceiling_score"] is not None
    apps = conn.execute("SELECT app_key FROM app_matches").fetchall()
    assert {r["app_key"] for r in apps} >= {"sparklayer", "weglot"}
    catalog = conn.execute("SELECT * FROM catalog_snapshots").fetchone()
    assert catalog["product_count"] == 1
    reviews = conn.execute("SELECT vendor, review_count FROM review_snapshots").fetchall()
    vendors = {r["vendor"]: r["review_count"] for r in reviews}
    assert vendors.get("judgeme") == 1284
    assets = conn.execute("SELECT COUNT(*) AS n FROM assets").fetchone()
    assert assets["n"] > 0
    store = conn.execute(
        "SELECT * FROM stores WHERE domain = 'plain.example'"
    ).fetchone()
    assert store["is_shopify"] == 0

    # rerunning the same scan on the same day is idempotent
    with responses.RequestsMock(assert_all_requests_are_fired=False):
        result2 = runner.invoke(
            app, ["scan", "run", "--domains", str(domains_csv), "--date", "2026-09-08"]
        )
    assert result2.exit_code == 0, result2.output
    features2 = conn.execute("SELECT COUNT(*) AS n FROM features").fetchone()
    assert features2["n"] == 1
    scans = conn.execute("SELECT COUNT(*) AS n FROM scans").fetchone()
    assert scans["n"] == 1
    conn.close()
