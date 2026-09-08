"""Thin repository layer over sqlite3.

Plain functions taking a connection first. Uniqueness constraints in the
schema make writes idempotent: INSERT OR IGNORE for append-only match rows,
INSERT OR REPLACE for per-scan snapshots that a rerun may recompute.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from typing import cast


def fetch_one(
    conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()
) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", conn.execute(sql, params).fetchone())

_IDENTIFIER_TABLES: frozenset[str] = frozenset(
    {
        "stores",
        "scans",
        "fetches",
        "assets",
        "app_matches",
        "fingerprint_matches",
        "catalog_snapshots",
        "review_snapshots",
        "features",
        "labels",
        "wayback_snapshots",
        "wayback_app_matches",
        "wayback_fingerprint_matches",
        "hiring_posts",
        "company_boards",
        "index_values",
    }
)


def applied_migrations(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT migration FROM schema_migrations ORDER BY migration"
    ).fetchall()
    return [str(row["migration"]) for row in rows]


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [str(row["name"]) for row in rows]


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """Row count for a known table. Rejects unknown table names."""
    if table not in _IDENTIFIER_TABLES:
        raise ValueError(f"unknown table: {table!r}")
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])


# stores


def upsert_store(conn: sqlite3.Connection, domain: str, now: str) -> int:
    conn.execute(
        "INSERT INTO stores (domain, first_seen_at, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT(domain) DO NOTHING",
        (domain, now, now),
    )
    row = conn.execute(
        "SELECT store_id FROM stores WHERE domain = ?", (domain,)
    ).fetchone()
    return int(row["store_id"])


def get_store(conn: sqlite3.Connection, domain: str) -> sqlite3.Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM stores WHERE domain = ?", (domain,)
    )


def stores_unchecked(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM stores WHERE is_shopify IS NULL ORDER BY store_id")
    )


def update_store_shopify(
    conn: sqlite3.Connection,
    store_id: int,
    is_shopify: bool,
    checked_at: str,
    myshopify_domain: str | None,
    notes: str | None,
) -> None:
    conn.execute(
        "UPDATE stores SET is_shopify = ?, is_shopify_checked_at = ?,"
        " myshopify_domain = COALESCE(?, myshopify_domain), notes = ?"
        " WHERE store_id = ?",
        (1 if is_shopify else 0, checked_at, myshopify_domain, notes, store_id),
    )


# scans


def get_or_create_scan(
    conn: sqlite3.Connection,
    scan_date: str,
    panel_version: str,
    git_sha: str,
    config_sha: str,
    now: str,
) -> int:
    conn.execute(
        "INSERT INTO scans (scan_date, panel_version, started_at, git_sha,"
        " config_sha, created_at) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(scan_date, panel_version) DO NOTHING",
        (scan_date, panel_version, now, git_sha, config_sha, now),
    )
    row = conn.execute(
        "SELECT scan_id FROM scans WHERE scan_date = ? AND panel_version = ?",
        (scan_date, panel_version),
    ).fetchone()
    return int(row["scan_id"])


def finish_scan(conn: sqlite3.Connection, scan_id: int, finished_at: str) -> None:
    conn.execute(
        "UPDATE scans SET finished_at = ? WHERE scan_id = ?", (finished_at, scan_id)
    )


def scans_for_version(
    conn: sqlite3.Connection, panel_version: str
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM scans WHERE panel_version = ? ORDER BY scan_date",
            (panel_version,),
        )
    )


def latest_scan(
    conn: sqlite3.Connection, panel_version: str | None = None
) -> sqlite3.Row | None:
    if panel_version is None:
        return fetch_one(
        conn,
        "SELECT * FROM scans ORDER BY scan_date DESC, scan_id DESC LIMIT 1"
    )
    return fetch_one(
        conn,
        "SELECT * FROM scans WHERE panel_version = ?"
        " ORDER BY scan_date DESC, scan_id DESC LIMIT 1",
        (panel_version,),
    )


# fetches


def insert_fetch(
    conn: sqlite3.Connection,
    *,
    scan_id: int,
    store_id: int,
    url: str,
    page_role: str,
    fetched_at: str,
    robots_allowed: bool,
    http_status: int | None = None,
    skip_reason: str | None = None,
    body_sha256: str | None = None,
    body_path: str | None = None,
    content_type: str | None = None,
    elapsed_ms: int | None = None,
    now: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO fetches (scan_id, store_id, url, page_role, fetched_at,"
        " http_status, robots_allowed, skip_reason, body_sha256, body_path,"
        " content_type, elapsed_ms, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            scan_id,
            store_id,
            url,
            page_role,
            fetched_at,
            http_status,
            1 if robots_allowed else 0,
            skip_reason,
            body_sha256,
            body_path,
            content_type,
            elapsed_ms,
            now,
        ),
    )
    return int(cur.lastrowid or 0)


def find_fetch_in_scan(
    conn: sqlite3.Connection, scan_id: int, url: str
) -> sqlite3.Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM fetches WHERE scan_id = ? AND url = ?"
        " ORDER BY fetch_id DESC LIMIT 1",
        (scan_id, url),
    )


def find_cached_body(
    conn: sqlite3.Connection, url: str, scan_date: str
) -> sqlite3.Row | None:
    """A same-day 200 fetch of the same URL with a body on disk, if any."""
    return fetch_one(
        conn,
        "SELECT * FROM fetches WHERE url = ? AND http_status = 200"
        " AND body_path IS NOT NULL AND fetched_at LIKE ?"
        " ORDER BY fetch_id DESC LIMIT 1",
        (url, f"{scan_date}%"),
    )


# assets and matches


def insert_assets(
    conn: sqlite3.Connection,
    fetch_id: int,
    assets: Iterable[tuple[str, str, str | None]],
    now: str,
) -> int:
    n = 0
    for asset_type, value, host in assets:
        conn.execute(
            "INSERT INTO assets (fetch_id, asset_type, value, host, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (fetch_id, asset_type, value, host, now),
        )
        n += 1
    return n


def upsert_app_match(
    conn: sqlite3.Connection,
    scan_id: int,
    store_id: int,
    app_key: str,
    capability: str,
    matched_on: str,
    pattern: str,
    signature_verified: bool,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO app_matches (scan_id, store_id, app_key,"
        " capability, matched_on, pattern, signature_verified, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            scan_id,
            store_id,
            app_key,
            capability,
            matched_on,
            pattern,
            1 if signature_verified else 0,
            now,
        ),
    )


def app_matches_for(
    conn: sqlite3.Connection, scan_id: int, store_id: int
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM app_matches WHERE scan_id = ? AND store_id = ?",
            (scan_id, store_id),
        )
    )


def upsert_fingerprint_match(
    conn: sqlite3.Connection,
    scan_id: int,
    store_id: int,
    fingerprint_key: str,
    matched_on: str,
    pattern: str,
    fingerprint_verified: bool,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO fingerprint_matches (scan_id, store_id,"
        " fingerprint_key, matched_on, pattern, fingerprint_verified, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            scan_id,
            store_id,
            fingerprint_key,
            matched_on,
            pattern,
            1 if fingerprint_verified else 0,
            now,
        ),
    )


def fingerprint_store_ids(
    conn: sqlite3.Connection, scan_id: int, verified_only: bool
) -> set[int]:
    sql = "SELECT DISTINCT store_id FROM fingerprint_matches WHERE scan_id = ?"
    if verified_only:
        sql += " AND fingerprint_verified = 1"
    return {int(row["store_id"]) for row in conn.execute(sql, (scan_id,))}


# snapshots and features


def upsert_catalog_snapshot(
    conn: sqlite3.Connection,
    scan_id: int,
    store_id: int,
    values: Mapping[str, object],
    now: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO catalog_snapshots (scan_id, store_id,"
        " products_json_available, product_count, pages_fetched, truncated,"
        " earliest_created_at, latest_created_at, products_created_last_90d,"
        " products_created_last_365d, products_created_prior_365d,"
        " variant_count, vendor_count, available_share, median_price,"
        " currency, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            scan_id,
            store_id,
            values["products_json_available"],
            values.get("product_count"),
            values.get("pages_fetched"),
            values.get("truncated"),
            values.get("earliest_created_at"),
            values.get("latest_created_at"),
            values.get("products_created_last_90d"),
            values.get("products_created_last_365d"),
            values.get("products_created_prior_365d"),
            values.get("variant_count"),
            values.get("vendor_count"),
            values.get("available_share"),
            values.get("median_price"),
            values.get("currency"),
            now,
        ),
    )


def catalog_for(
    conn: sqlite3.Connection, scan_id: int, store_id: int
) -> sqlite3.Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM catalog_snapshots WHERE scan_id = ? AND store_id = ?",
        (scan_id, store_id),
    )


def latest_catalog_for_store(
    conn: sqlite3.Connection, store_id: int
) -> sqlite3.Row | None:
    return fetch_one(
        conn,
        "SELECT cs.* FROM catalog_snapshots cs JOIN scans s ON s.scan_id = cs.scan_id"
        " WHERE cs.store_id = ? ORDER BY s.scan_date DESC LIMIT 1",
        (store_id,),
    )


def upsert_review_snapshot(
    conn: sqlite3.Connection,
    scan_id: int,
    store_id: int,
    vendor: str,
    review_count: int | None,
    source_url: str | None,
    method: str,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO review_snapshots (scan_id, store_id, vendor,"
        " review_count, source_url, method, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (scan_id, store_id, vendor, review_count, source_url, method, now),
    )


def upsert_features(
    conn: sqlite3.Connection,
    scan_id: int,
    store_id: int,
    feature_json: str,
    ceiling_score: float | None,
    ceiling_components_json: str | None,
    plus_fingerprint_any: bool,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO features (scan_id, store_id, feature_json,"
        " ceiling_score, ceiling_components_json, plus_fingerprint_any, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            scan_id,
            store_id,
            feature_json,
            ceiling_score,
            ceiling_components_json,
            1 if plus_fingerprint_any else 0,
            now,
        ),
    )


def features_for_scan(conn: sqlite3.Connection, scan_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT f.*, st.domain FROM features f"
            " JOIN stores st ON st.store_id = f.store_id"
            " WHERE f.scan_id = ? ORDER BY f.store_id",
            (scan_id,),
        )
    )


def feature_row(
    conn: sqlite3.Connection, scan_id: int, store_id: int
) -> sqlite3.Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM features WHERE scan_id = ? AND store_id = ?",
        (scan_id, store_id),
    )


# labels


def upsert_label(
    conn: sqlite3.Connection,
    store_id: int,
    label_set: str,
    label_value: str | None,
    source_url: str,
    source_type: str,
    quote: str | None,
    added_by: str,
    added_at: str,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO labels (store_id, label_set, label_value,"
        " source_url, source_type, quote, added_by, added_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            store_id,
            label_set,
            label_value,
            source_url,
            source_type,
            quote,
            added_by,
            added_at,
            now,
        ),
    )


def label_rows(conn: sqlite3.Connection, label_set: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT l.*, st.domain FROM labels l"
            " JOIN stores st ON st.store_id = l.store_id"
            " WHERE l.label_set = ? ORDER BY st.domain",
            (label_set,),
        )
    )


# wayback


def upsert_wayback_snapshot(
    conn: sqlite3.Connection,
    *,
    store_id: int,
    target_month: str,
    offset_months: int,
    actual_timestamp: str | None,
    archive_url: str | None,
    http_status: int | None,
    body_sha256: str | None,
    body_path: str | None,
    fetched_at: str,
    now: str,
) -> int:
    conn.execute(
        "INSERT OR REPLACE INTO wayback_snapshots (store_id, target_month,"
        " offset_months, actual_timestamp, archive_url, http_status,"
        " body_sha256, body_path, fetched_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            store_id,
            target_month,
            offset_months,
            actual_timestamp,
            archive_url,
            http_status,
            body_sha256,
            body_path,
            fetched_at,
            now,
        ),
    )
    row = conn.execute(
        "SELECT wb_id FROM wayback_snapshots WHERE store_id = ? AND offset_months = ?",
        (store_id, offset_months),
    ).fetchone()
    return int(row["wb_id"])


def insert_wayback_app_match(
    conn: sqlite3.Connection,
    wb_id: int,
    app_key: str,
    capability: str,
    matched_on: str,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO wayback_app_matches"
        " (wb_id, app_key, capability, matched_on, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (wb_id, app_key, capability, matched_on, now),
    )


def insert_wayback_fingerprint_match(
    conn: sqlite3.Connection,
    wb_id: int,
    fingerprint_key: str,
    matched_on: str,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO wayback_fingerprint_matches"
        " (wb_id, fingerprint_key, matched_on, created_at)"
        " VALUES (?, ?, ?, ?)",
        (wb_id, fingerprint_key, matched_on, now),
    )


def wayback_rows_for_store(
    conn: sqlite3.Connection, store_id: int
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM wayback_snapshots WHERE store_id = ?"
            " ORDER BY offset_months",
            (store_id,),
        )
    )


def wayback_matches(
    conn: sqlite3.Connection, wb_id: int
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    apps = list(
        conn.execute("SELECT * FROM wayback_app_matches WHERE wb_id = ?", (wb_id,))
    )
    fps = list(
        conn.execute(
            "SELECT * FROM wayback_fingerprint_matches WHERE wb_id = ?", (wb_id,)
        )
    )
    return apps, fps


# hiring


def upsert_company_board(
    conn: sqlite3.Connection,
    board: str,
    company_slug: str,
    store_id: int | None,
    discovered_from_url: str,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO company_boards"
        " (board, company_slug, store_id, discovered_from_url, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (board, company_slug, store_id, discovered_from_url, now),
    )


def company_boards(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM company_boards ORDER BY board, company_slug"))


def upsert_hiring_post(
    conn: sqlite3.Connection,
    *,
    board: str,
    company_slug: str,
    store_id: int | None,
    external_id: str,
    title: str,
    posted_at: str | None,
    seen_at: str,
    matched_terms_json: str,
    source_url: str,
    now: str,
) -> None:
    conn.execute(
        "INSERT INTO hiring_posts (board, company_slug, store_id, external_id,"
        " title, posted_at, first_seen_at, last_seen_at, matched_terms_json,"
        " source_url, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(board, company_slug, external_id) DO UPDATE SET"
        " last_seen_at = excluded.last_seen_at,"
        " matched_terms_json = excluded.matched_terms_json,"
        " title = excluded.title",
        (
            board,
            company_slug,
            store_id,
            external_id,
            title,
            posted_at,
            seen_at,
            seen_at,
            matched_terms_json,
            source_url,
            now,
        ),
    )


def hiring_posts_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM hiring_posts ORDER BY post_id"))


# index


def upsert_index_value(
    conn: sqlite3.Connection,
    *,
    scan_id: int,
    panel_version: str,
    panel_n: int,
    panel_n_scanned: int,
    share_ceiling: float | None,
    share_workaround_any: float | None,
    share_high_growth: float | None,
    share_converted_since_prior: float | None,
    n_converted: int | None,
    n_prior_selfserve: int | None,
    hiring_plus_posts_month: int | None,
    computed_at: str,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO index_values (scan_id, panel_version, panel_n,"
        " panel_n_scanned, share_ceiling, share_workaround_any, share_high_growth,"
        " share_converted_since_prior, n_converted, n_prior_selfserve,"
        " hiring_plus_posts_month, computed_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            scan_id,
            panel_version,
            panel_n,
            panel_n_scanned,
            share_ceiling,
            share_workaround_any,
            share_high_growth,
            share_converted_since_prior,
            n_converted,
            n_prior_selfserve,
            hiring_plus_posts_month,
            computed_at,
            now,
        ),
    )


def index_values_for_version(
    conn: sqlite3.Connection, panel_version: str
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT iv.*, s.scan_date FROM index_values iv"
            " JOIN scans s ON s.scan_id = iv.scan_id"
            " WHERE iv.panel_version = ? ORDER BY s.scan_date",
            (panel_version,),
        )
    )
