"""Panel build: eligibility filters, freezing, stratification, plotting."""

from __future__ import annotations

import json
import sqlite3

import pytest

from ceiling.db import repo
from ceiling.index.compute import compute_index
from ceiling.index.panel import PanelError, build_panel, load_panel
from ceiling.index.plot import plot_index
from ceiling.settings import Settings

NOW = "2026-09-08T00:00:00Z"


def seed_candidates(conn: sqlite3.Connection, n: int = 24) -> int:
    scan_id = repo.get_or_create_scan(conn, "2026-09-08", "seed", "sha", "csha", NOW)
    for i in range(n):
        domain = f"cand{i}.example"
        store_id = repo.upsert_store(conn, domain, NOW)
        repo.update_store_shopify(conn, store_id, True, NOW, None, None)
        repo.upsert_catalog_snapshot(
            conn,
            scan_id,
            store_id,
            {"products_json_available": 1, "product_count": 10 * (i + 1)},
            NOW,
        )
    conn.commit()
    return scan_id


def test_build_panel_writes_and_freezes(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    seed_candidates(conn)
    labels_dir = settings.paths.labels_dir
    labels_dir.mkdir(parents=True, exist_ok=True)
    selected = build_panel(conn, labels_dir, size=8, version="v1")
    assert len(selected) == 8
    assert load_panel(labels_dir, "v1") == selected
    # frozen: same version cannot be rebuilt
    with pytest.raises(PanelError, match="frozen"):
        build_panel(conn, labels_dir, size=8, version="v1")
    # a second version appends without touching v1
    build_panel(conn, labels_dir, size=4, version="v2")
    assert len(load_panel(labels_dir, "v1")) == 8
    assert len(load_panel(labels_dir, "v2")) == 4


def test_build_panel_excludes_plus_and_labeled(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    scan_id = seed_candidates(conn, n=6)
    # cand0 is labeled plus_positive, cand1 has a fingerprint match
    store0 = repo.upsert_store(conn, "cand0.example", NOW)
    repo.upsert_label(
        conn, store0, "plus_positive", None,
        "https://example.com", "manual", None, "t", "2026-09-01", NOW,
    )
    store1 = repo.upsert_store(conn, "cand1.example", NOW)
    repo.upsert_fingerprint_match(
        conn, scan_id, store1, "shopify_plus_text", "x", "p", False, NOW
    )
    conn.commit()
    labels_dir = settings.paths.labels_dir
    selected = build_panel(conn, labels_dir, size=10, version="v1")
    assert "cand0.example" not in selected
    assert "cand1.example" not in selected
    assert len(selected) == 4


def test_build_panel_requires_scan(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    with pytest.raises(PanelError, match="no scan"):
        build_panel(conn, settings.paths.labels_dir, size=5, version="v1")


def test_plot_index_renders_png(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    store = repo.upsert_store(conn, "a.example", NOW)
    scan_id = repo.get_or_create_scan(conn, "2026-09-08", "v1", "sha", "csha", NOW)
    repo.upsert_features(
        conn, scan_id, store,
        json.dumps({"workaround_app_count": 1, "product_count": 10}),
        4.0, json.dumps({"high_growth": {"active": True}}), False, NOW,
    )
    conn.commit()
    compute_index(settings, conn, "v1", ["a.example"])
    out = plot_index(conn, "v1", settings.paths.exports_dir)
    assert out is not None and out.exists()
    assert out.stat().st_size > 0


def test_plot_index_nothing_to_plot(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    assert plot_index(conn, "nope", settings.paths.exports_dir) is None
