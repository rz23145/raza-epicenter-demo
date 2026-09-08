"""Hiring: board fixtures parse, term matching is case-insensitive, a posting
counts once."""

from __future__ import annotations

import json
import sqlite3

from ceiling.db import repo
from ceiling.hiring.boards import (
    Posting,
    find_board_links,
    parse_greenhouse,
    parse_lever,
)
from ceiling.hiring.count import (
    count_for_month,
    match_terms,
    monthly_counts,
    record_postings,
)
from tests.conftest import FIXTURES

TERMS = [
    "shopify plus",
    "shopify+ ",
    "hydrogen",
    "headless commerce",
    "replatform",
    "re-platform",
    "checkout extensibility",
    "shopify functions",
]


def test_greenhouse_fixture_parses() -> None:
    payload = json.loads((FIXTURES / "greenhouse.json").read_text())
    postings = parse_greenhouse(payload)
    assert len(postings) == 2
    assert postings[0].external_id == "111"
    assert "Shopify Plus" in postings[0].title
    # HTML entities stripped from description
    assert "<b>" not in postings[0].description_text


def test_lever_fixture_parses() -> None:
    payload = json.loads((FIXTURES / "lever.json").read_text())
    postings = parse_lever(payload)
    assert len(postings) == 2
    assert postings[0].title == "Ecommerce Lead"
    assert postings[0].posted_at is not None
    assert postings[0].posted_at.startswith("2025-08")


def test_term_matching_case_insensitive() -> None:
    posting = Posting("1", "SHOPIFY PLUS migration lead", "", None, "u")
    assert match_terms(posting, TERMS) == ["shopify plus"]
    posting = Posting("2", "Engineer", "our Hydrogen storefront", None, "u")
    assert "hydrogen" in match_terms(posting, TERMS)
    posting = Posting("3", "Baker", "make bread", None, "u")
    assert match_terms(posting, TERMS) == []


def test_posting_counts_once_regardless_of_terms(conn: sqlite3.Connection) -> None:
    posting = Posting(
        "x1",
        "Shopify Plus replatform",
        "hydrogen and checkout extensibility and headless commerce",
        "2026-08-15T00:00:00Z",
        "https://boards.greenhouse.io/acme/jobs/x1",
    )
    n = record_postings(conn, "greenhouse", "acme", None, [posting], TERMS)
    assert n == 1
    # rerun updates last_seen, does not duplicate
    n = record_postings(conn, "greenhouse", "acme", None, [posting], TERMS)
    assert repo.count_rows(conn, "hiring_posts") == 1
    counts = monthly_counts(conn)
    month = next(iter(counts["by_first_seen"]))
    assert counts["by_first_seen"][month] == 1
    assert counts["by_posted_at"] == {"2026-08": 1}
    assert count_for_month(conn, month) == 1


def test_non_matching_postings_not_recorded(conn: sqlite3.Connection) -> None:
    posting = Posting("y1", "Barista", "espresso", None, "u")
    n = record_postings(conn, "lever", "cafe", None, [posting], TERMS)
    assert n == 0
    assert repo.count_rows(conn, "hiring_posts") == 0


def test_find_board_links() -> None:
    html = (
        "<a href='https://boards.greenhouse.io/acme'>Careers</a>"
        "<a href='https://jobs.lever.co/other-co'>Jobs</a>"
        "<a href='https://jobs.ashbyhq.com/third_co'>Openings</a>"
        "<a href='https://boards.greenhouse.io/acme'>dupe</a>"
    )
    links = find_board_links(html)
    assert ("greenhouse", "acme") in links
    assert ("lever", "other-co") in links
    assert ("ashby", "third_co") in links
    assert len(links) == 3
