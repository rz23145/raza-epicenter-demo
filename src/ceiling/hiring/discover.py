"""Job board slug discovery for panel stores.

Looks for links to boards.greenhouse.io/{slug}, jobs.lever.co/{slug}, or
jobs.ashbyhq.com/{slug} in the home page HTML and in a fixed set of careers
paths, all robots-checked. Most stores will have none; the coverage number
is reported, not hidden.
"""

from __future__ import annotations

import sqlite3

from ceiling.db import repo
from ceiling.hiring.boards import CAREERS_PATHS, find_board_links
from ceiling.http.client import Client
from ceiling.logging import get_logger
from ceiling.util import utc_now_iso

logger = get_logger("hiring.discover")


def discover_for_store(
    client: Client,
    conn: sqlite3.Connection,
    store_id: int,
    domain: str,
    scan_id: int,
) -> list[tuple[str, str]]:
    """Fetch the home page and careers paths, record any board slugs found."""
    now = utc_now_iso()
    found: list[tuple[str, str]] = []
    pages = [f"https://{domain}/"] + [f"https://{domain}{p}" for p in CAREERS_PATHS]
    for url in pages:
        result = client.get(url, "other", store_id, scan_id)
        if not result.ok:
            continue
        for board, slug in find_board_links(result.text):
            repo.upsert_company_board(conn, board, slug, store_id, url, now)
            found.append((board, slug))
    conn.commit()
    if found:
        logger.info("store %s: boards found %s", domain, found)
    return found
