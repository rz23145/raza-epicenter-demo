"""Index: conversion only on 0-to-1 transitions with verified fingerprints,
attrition is not conversion, denominators exclude skipped stores."""

from __future__ import annotations

import json
import sqlite3

from ceiling.db import repo
from ceiling.index.compute import compute_index, export_timeseries
from ceiling.index.panel import detect_conversions
from ceiling.settings import Settings

NOW = "2026-09-08T00:00:00Z"


def seed_scan(
    conn: sqlite3.Connection, scan_date: str, version: str = "v1"
) -> int:
    return repo.get_or_create_scan(conn, scan_date, version, "sha", "csha", NOW)


def seed_features(
    conn: sqlite3.Connection,
    scan_id: int,
    store_id: int,
    score: float = 1.0,
    workaround_count: int = 0,
    fp_any: bool = False,
) -> None:
    features = {
        "workaround_app_count": workaround_count,
        "product_count": 100,
    }
    components = {"high_growth": {"active": False, "contribution": 0.0}}
    repo.upsert_features(
        conn,
        scan_id,
        store_id,
        json.dumps(features),
        score,
        json.dumps(components),
        plus_fingerprint_any=fp_any,
        now=NOW,
    )


def test_conversion_detected_only_on_verified_zero_to_one(
    conn: sqlite3.Connection,
) -> None:
    store_a = repo.upsert_store(conn, "converts.example", NOW)
    store_b = repo.upsert_store(conn, "unverified-fp.example", NOW)
    store_c = repo.upsert_store(conn, "stays.example", NOW)
    scan1 = seed_scan(conn, "2026-08-08")
    scan2 = seed_scan(conn, "2026-09-08")
    for store in (store_a, store_b, store_c):
        seed_features(conn, scan1, store)
        seed_features(conn, scan2, store)
    # store A gains a VERIFIED fingerprint at scan 2: conversion
    repo.upsert_fingerprint_match(
        conn, scan2, store_a, "multipass_login", "x", "p", True, NOW
    )
    # store B gains only an UNVERIFIED fingerprint: not a conversion
    repo.upsert_fingerprint_match(
        conn, scan2, store_b, "shopify_plus_text", "x", "p", False, NOW
    )
    conn.commit()

    result = detect_conversions(conn, scan2, scan1)
    assert result.n_converted == 1
    assert result.converted_domains == ["converts.example"]
    assert result.n_prior_selfserve == 3


def test_attrition_not_counted_as_conversion(conn: sqlite3.Connection) -> None:
    store_a = repo.upsert_store(conn, "a.example", NOW)
    store_gone = repo.upsert_store(conn, "gone.example", NOW)
    scan1 = seed_scan(conn, "2026-08-08")
    scan2 = seed_scan(conn, "2026-09-08")
    seed_features(conn, scan1, store_a)
    seed_features(conn, scan1, store_gone)
    seed_features(conn, scan2, store_a)  # store_gone has no scan2 row
    conn.commit()

    result = detect_conversions(conn, scan2, scan1)
    assert result.n_converted == 0
    assert result.attrition_domains == ["gone.example"]
    # the vanished store is excluded from the denominator
    assert result.n_prior_selfserve == 1


def test_already_plus_store_not_in_denominator(conn: sqlite3.Connection) -> None:
    store_plus = repo.upsert_store(conn, "alreadyplus.example", NOW)
    store_ss = repo.upsert_store(conn, "selfserve.example", NOW)
    scan1 = seed_scan(conn, "2026-08-08")
    scan2 = seed_scan(conn, "2026-09-08")
    for scan in (scan1, scan2):
        seed_features(conn, scan, store_plus, fp_any=True)
        seed_features(conn, scan, store_ss)
        repo.upsert_fingerprint_match(
            conn, scan, store_plus, "multipass_login", "x", "p", True, NOW
        )
    conn.commit()
    result = detect_conversions(conn, scan2, scan1)
    assert result.n_prior_selfserve == 1
    assert result.n_converted == 0


def test_compute_index_shares_and_denominators(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    high = repo.upsert_store(conn, "high.example", NOW)
    low = repo.upsert_store(conn, "low.example", NOW)
    repo.upsert_store(conn, "skipped.example", NOW)  # never scanned
    scan1 = seed_scan(conn, "2026-09-08")
    seed_features(conn, scan1, high, score=5.0, workaround_count=2)
    seed_features(conn, scan1, low, score=0.5, workaround_count=0)
    conn.commit()

    written = compute_index(
        settings,
        conn,
        "v1",
        ["high.example", "low.example", "skipped.example"],
    )
    assert written == 1
    row = repo.index_values_for_version(conn, "v1")[0]
    # denominator excludes the never-scanned store
    assert row["panel_n"] == 3
    assert row["panel_n_scanned"] == 2
    assert row["share_ceiling"] == 0.5  # threshold 3.0, one of two
    assert row["share_workaround_any"] == 0.5
    # single scan: no prior, conversion series is null
    assert row["share_converted_since_prior"] is None
    assert row["n_converted"] is None

    out = export_timeseries(conn, "v1", settings.paths.exports_dir)
    content = out.read_text()
    assert "share_ceiling" in content
    assert "2026-09-08" in content
