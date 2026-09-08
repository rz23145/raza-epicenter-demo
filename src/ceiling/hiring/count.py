"""Plus-related posting counts.

A posting counts once regardless of how many terms match. Counts are grouped
by first_seen_at month and by posted_at month where available. This module is
an aggregate cross-check only; board coverage over a storefront panel is a
small fraction and the README says so.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from ceiling.db import repo
from ceiling.hiring.boards import Posting
from ceiling.logging import get_logger
from ceiling.util import utc_now_iso

logger = get_logger("hiring.count")


def match_terms(posting: Posting, terms: list[str]) -> list[str]:
    haystack = f"{posting.title}\n{posting.description_text}".lower()
    return [t for t in terms if t.lower() in haystack]


def record_postings(
    conn: sqlite3.Connection,
    board: str,
    slug: str,
    store_id: int | None,
    postings: list[Posting],
    terms: list[str],
) -> int:
    """Persist postings that match at least one term. Returns matches recorded."""
    now = utc_now_iso()
    recorded = 0
    for posting in postings:
        matched = match_terms(posting, terms)
        if not matched:
            continue
        repo.upsert_hiring_post(
            conn,
            board=board,
            company_slug=slug,
            store_id=store_id,
            external_id=posting.external_id,
            title=posting.title,
            posted_at=posting.posted_at,
            seen_at=now,
            matched_terms_json=json.dumps(matched),
            source_url=posting.url,
            now=now,
        )
        recorded += 1
    conn.commit()
    return recorded


def monthly_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """{'by_first_seen': {yyyy-mm: n}, 'by_posted_at': {yyyy-mm: n}}"""
    by_first_seen: Counter[str] = Counter()
    by_posted: Counter[str] = Counter()
    for row in repo.hiring_posts_all(conn):
        first_seen = str(row["first_seen_at"])[:7]
        by_first_seen[first_seen] += 1
        posted = row["posted_at"]
        if posted:
            by_posted[str(posted)[:7]] += 1
    return {
        "by_first_seen": dict(sorted(by_first_seen.items())),
        "by_posted_at": dict(sorted(by_posted.items())),
    }


def count_for_month(conn: sqlite3.Connection, month: str) -> int:
    """Postings first seen in a yyyy-mm month; feeds index_values."""
    return monthly_counts(conn)["by_first_seen"].get(month, 0)
