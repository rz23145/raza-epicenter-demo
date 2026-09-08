"""Wayback backtest end to end with a fully mocked archive."""

from __future__ import annotations

import sqlite3

import responses

from ceiling.db import repo
from ceiling.http.client import Client
from ceiling.settings import Settings
from ceiling.wayback.backtest import export_backtest, plot_backtest, run_backtest
from tests.conftest import REPO_ROOT, FakeClock, fixture_text

NOW = "2026-09-08T00:00:00Z"
CONFIG_DIR = REPO_ROOT / "config"

WAYBACK_ROBOTS = "https://web.archive.org/robots.txt"
ALLOW_ALL = "User-agent: *\nAllow: /\n"


def seed_migrator_and_controls(conn: sqlite3.Connection) -> None:
    m = repo.upsert_store(conn, "migrator.example", NOW)
    repo.upsert_label(
        conn, m, "confirmed_migrator", "2025-06",
        "https://example.com/case", "case_study", "we moved to plus",
        "tester", "2026-01-01", NOW,
    )
    for i in range(2):
        s = repo.upsert_store(conn, f"control{i}.example", NOW)
        repo.upsert_label(
            conn, s, "non_plus_negative", None,
            "https://example.com/src", "directory", None, "tester", "2026-01-01", NOW,
        )
    conn.commit()


def cdx_body(timestamps: list[str]) -> str:
    rows = [["timestamp", "statuscode", "mimetype", "digest"]] + [
        [ts, "200", "text/html", f"D{ts}"] for ts in timestamps
    ]
    import json

    return json.dumps(rows)


def test_backtest_end_to_end(
    settings: Settings, conn: sqlite3.Connection, fake_clock: FakeClock
) -> None:
    seed_migrator_and_controls(conn)
    client = Client(
        settings, conn, "2026-09-08",
        clock=fake_clock.clock, sleeper=fake_clock.sleep,
    )
    offsets = [-6, 3]
    # migration month 2025-06: targets 2024-12 and 2025-09
    shop_html = fixture_text("shop_home.html")

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get(WAYBACK_ROBOTS, body=ALLOW_ALL)

        def cdx_callback(request: object) -> tuple[int, dict[str, str], str]:
            # snapshot available near 2024-12 only
            return (200, {}, cdx_body(["20241214120000"]))

        rsps.add_callback(
            responses.GET,
            "https://web.archive.org/cdx/search/cdx",
            callback=cdx_callback,
        )
        # replay of the pre-migration snapshot serves the workaround fixture
        for domain in ("migrator.example", "control0.example", "control1.example"):
            rsps.get(
                f"https://web.archive.org/web/20241214120000id_/https://{domain}/",
                body=shop_html,
            )
        summary = run_backtest(settings, conn, client, CONFIG_DIR, offsets)

    # every store contributes one row per offset
    assert len(summary.rows) == 3 * len(offsets)
    # target 2024-12 found for the migrator, 2025-09 not
    migrator_rows = {r.offset: r for r in summary.rows if r.group == "migrator"}
    assert migrator_rows[-6].snapshot_found
    assert migrator_rows[-6].workaround_any is True
    assert migrator_rows[3].snapshot_found is False
    assert summary.coverage is not None and 0 < summary.coverage < 1
    # prevalence series exists for both groups
    assert summary.prevalence_by_offset["migrator"][-6] == 1.0
    assert summary.earliest_workaround_offset["migrator.example"] == -6

    out = export_backtest(summary, settings.paths.exports_dir)
    content = out.read_text()
    assert "migrator.example" in content
    assert "control0.example" in content
    chart = plot_backtest(summary, settings.paths.exports_dir)
    assert chart is not None and chart.exists()

    # wayback tables were populated with provenance
    wb_rows = repo.wayback_rows_for_store(
        conn, repo.upsert_store(conn, "migrator.example", NOW)
    )
    assert len(wb_rows) == len(offsets)
    found = [r for r in wb_rows if r["http_status"] == 200]
    assert len(found) == 1
    assert found[0]["body_sha256"] is not None


def test_backtest_with_no_migrators_is_empty(
    settings: Settings, conn: sqlite3.Connection, fake_clock: FakeClock
) -> None:
    client = Client(
        settings, conn, "2026-09-08",
        clock=fake_clock.clock, sleeper=fake_clock.sleep,
    )
    summary = run_backtest(settings, conn, client, CONFIG_DIR, [-6])
    assert summary.rows == []
    assert summary.coverage is None
