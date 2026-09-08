"""Fetch archived snapshots and run the extractors on them.

The id_ flag returns the original HTML without the Wayback toolbar, so the
same extract, apps.match, and fingerprints.match code paths run unchanged.
"""

from __future__ import annotations

import sqlite3

from ceiling.db import repo
from ceiling.detect.apps import match_apps
from ceiling.detect.extract import extract
from ceiling.detect.fingerprints import match_fingerprints
from ceiling.detect.signatures import CompiledSignature, SignatureEntry
from ceiling.http.client import Client
from ceiling.logging import get_logger
from ceiling.util import utc_now_iso
from ceiling.wayback.cdx import CDX_MIN_INTERVAL_S

logger = get_logger("wayback.replay")


def snapshot_url(timestamp: str, domain: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/https://{domain}/"


def replay_snapshot(
    client: Client,
    conn: sqlite3.Connection,
    store_id: int,
    domain: str,
    target_month: str,
    offset_months: int,
    timestamp: str | None,
    app_signatures: list[CompiledSignature],
    fp_entries: list[SignatureEntry],
    fp_compiled: list[CompiledSignature],
) -> int:
    """Fetch one archived home page (if a timestamp was found), run the
    extractors, and persist everything. Returns the wb_id."""
    now = utc_now_iso()
    if timestamp is None:
        wb_id = repo.upsert_wayback_snapshot(
            conn,
            store_id=store_id,
            target_month=target_month,
            offset_months=offset_months,
            actual_timestamp=None,
            archive_url=None,
            http_status=None,
            body_sha256=None,
            body_path=None,
            fetched_at=now,
            now=now,
        )
        conn.commit()
        return wb_id

    url = snapshot_url(timestamp, domain)
    result = client.get_external(url, min_interval=CDX_MIN_INTERVAL_S)
    wb_id = repo.upsert_wayback_snapshot(
        conn,
        store_id=store_id,
        target_month=target_month,
        offset_months=offset_months,
        actual_timestamp=timestamp,
        archive_url=url,
        http_status=result.status,
        body_sha256=result.body_sha256,
        body_path=result.body_path,
        fetched_at=now,
        now=now,
    )
    if result.ok:
        html = result.text
        ex = extract(html)
        for match in match_apps(app_signatures, [ex], [html]):
            repo.insert_wayback_app_match(
                conn,
                wb_id,
                match.key,
                match.capability or "",
                match.matched_on,
                now,
            )
        for match in match_fingerprints(
            fp_entries, fp_compiled, [ex], [html], domain
        ):
            repo.insert_wayback_fingerprint_match(
                conn, wb_id, match.key, match.matched_on, now
            )
    conn.commit()
    return wb_id
