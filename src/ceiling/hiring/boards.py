"""Public job board JSON readers: Greenhouse, Lever, Ashby.

These endpoints require no authentication. Each API host gets a robots check
and a 2 second minimum interval. Coverage across a storefront panel is small
and the README must not oversell this module.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from ceiling.http.client import Client
from ceiling.logging import get_logger

logger = get_logger("hiring.boards")

BOARD_API = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}

SLUG_PATTERNS = {
    "greenhouse": re.compile(
        r"boards\.greenhouse\.io/([A-Za-z0-9_-]+)", re.IGNORECASE
    ),
    "lever": re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)", re.IGNORECASE),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)", re.IGNORECASE),
}

CAREERS_PATHS = ["/pages/careers", "/pages/jobs", "/careers", "/jobs"]

HIRING_MIN_INTERVAL_S = 2.0


@dataclass
class Posting:
    external_id: str
    title: str
    description_text: str
    posted_at: str | None
    url: str


def _strip_html(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def board_api_url(board: str, slug: str) -> str:
    return BOARD_API[board].format(slug=slug)


def parse_greenhouse(payload: Any) -> list[Posting]:
    postings: list[Posting] = []
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        # Greenhouse returns HTML-escaped markup in `content`
        content = html_module.unescape(str(job.get("content", "")))
        postings.append(
            Posting(
                external_id=str(job.get("id", "")),
                title=str(job.get("title", "")),
                description_text=_strip_html(content),
                posted_at=job.get("updated_at") or job.get("first_published"),
                url=str(job.get("absolute_url", "")),
            )
        )
    return postings


def parse_lever(payload: Any) -> list[Posting]:
    postings: list[Posting] = []
    for job in payload if isinstance(payload, list) else []:
        if not isinstance(job, dict):
            continue
        created = job.get("createdAt")
        posted_at: str | None = None
        if isinstance(created, (int, float)):
            from datetime import UTC, datetime

            posted_at = datetime.fromtimestamp(created / 1000, UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        postings.append(
            Posting(
                external_id=str(job.get("id", "")),
                title=str(job.get("text", "")),
                description_text=_strip_html(str(job.get("description", ""))),
                posted_at=posted_at,
                url=str(job.get("hostedUrl", "")),
            )
        )
    return postings


def parse_ashby(payload: Any) -> list[Posting]:
    postings: list[Posting] = []
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        postings.append(
            Posting(
                external_id=str(job.get("id", "")),
                title=str(job.get("title", "")),
                description_text=_strip_html(str(job.get("descriptionHtml", ""))),
                posted_at=job.get("publishedAt"),
                url=str(job.get("jobUrl", "")),
            )
        )
    return postings


_PARSERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "ashby": parse_ashby,
}


def fetch_postings(client: Client, board: str, slug: str) -> list[Posting] | None:
    """Fetch and parse a board. None on fetch failure (recorded by caller)."""
    url = board_api_url(board, slug)
    result = client.get_external(url, min_interval=HIRING_MIN_INTERVAL_S)
    if not result.ok:
        logger.warning(
            "board fetch failed: %s/%s status=%s skip=%s",
            board,
            slug,
            result.status,
            result.skip_reason,
        )
        return None
    try:
        payload = json.loads(result.text)
    except ValueError:
        logger.warning("board %s/%s returned non-JSON", board, slug)
        return None
    return _PARSERS[board](payload)


def find_board_links(html: str) -> list[tuple[str, str]]:
    """(board, slug) pairs referenced anywhere in a page."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for board, pattern in SLUG_PATTERNS.items():
        for match in pattern.finditer(html):
            slug = match.group(1)
            if slug.lower() in {"embed", "job", "jobs"}:
                continue
            pair = (board, slug)
            if pair not in seen:
                seen.add(pair)
                found.append(pair)
    return found
