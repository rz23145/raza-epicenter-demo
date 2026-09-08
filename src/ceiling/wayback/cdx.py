"""Wayback Machine CDX API queries.

Rate limited at 1 request per 2 seconds on web.archive.org. Responses are
cached in the raw archive like everything else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from ceiling.http.client import Client
from ceiling.logging import get_logger
from ceiling.util import month_midpoint

logger = get_logger("wayback.cdx")

CDX_MIN_INTERVAL_S = 2.0


@dataclass
class CdxRow:
    timestamp: str
    statuscode: str
    mimetype: str
    digest: str


def cdx_url(domain: str, from_month: str, to_month: str) -> str:
    frm = from_month.replace("-", "")
    to = to_month.replace("-", "")
    return (
        "https://web.archive.org/cdx/search/cdx"
        f"?url={domain}&output=json&fl=timestamp,statuscode,mimetype,digest"
        "&filter=statuscode:200&filter=mimetype:text/html"
        f"&from={frm}&to={to}&collapse=digest"
    )


def parse_cdx(body: str) -> list[CdxRow]:
    try:
        raw = json.loads(body)
    except ValueError:
        logger.warning("unparseable CDX response")
        return []
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    rows: list[CdxRow] = []
    for item in raw[1:]:
        if isinstance(item, list) and len(item) >= 4:
            rows.append(
                CdxRow(str(item[0]), str(item[1]), str(item[2]), str(item[3]))
            )
    return rows


def query_cdx(
    client: Client, domain: str, from_month: str, to_month: str
) -> list[CdxRow]:
    url = cdx_url(domain, from_month, to_month)
    result = client.get_external(url, min_interval=CDX_MIN_INTERVAL_S)
    if not result.ok:
        logger.warning(
            "CDX query failed for %s: status=%s skip=%s",
            domain,
            result.status,
            result.skip_reason,
        )
        return []
    return parse_cdx(result.text)


def _timestamp_date(timestamp: str) -> date | None:
    raw = timestamp[:8]
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def pick_snapshot(
    rows: list[CdxRow], target_month: str, tolerance_days: int
) -> str | None:
    """The snapshot timestamp closest to the 15th of the target month, within
    tolerance_days. None when no snapshot qualifies."""
    target = month_midpoint(target_month)
    best: tuple[int, str] | None = None
    for row in rows:
        snap_date = _timestamp_date(row.timestamp)
        if snap_date is None:
            continue
        distance = abs((snap_date - target).days)
        if distance > tolerance_days:
            continue
        if best is None or distance < best[0]:
            best = (distance, row.timestamp)
    return best[1] if best else None
