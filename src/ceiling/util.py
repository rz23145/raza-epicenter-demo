"""Small shared utilities: hashing, time, domain normalization, month math."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from urllib.parse import urlparse


def utc_now_iso() -> str:
    """Current UTC time as ISO 8601 with second precision."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def host_of(url: str) -> str:
    """Lowercased network host of a URL, without credentials or port."""
    netloc = urlparse(url).netloc.lower()
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    return netloc.split(":")[0]


def normalize_domain(raw: str) -> str:
    """Normalize a domain: lowercase, no scheme, no www, no path, no trailing dot.

    Raises ValueError when the input cannot be a domain.
    """
    value = raw.strip().lower()
    if not value:
        raise ValueError("empty domain")
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.netloc or parsed.path
    value = value.split("/")[0].split("?")[0].split("#")[0]
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    value = value.split(":")[0]
    value = value.removeprefix("www.").rstrip(".")
    if not value or "." not in value or " " in value:
        raise ValueError(f"not a domain: {raw!r}")
    return value


def parse_month(month: str) -> tuple[int, int]:
    """Parse yyyy-mm, raising ValueError on anything else."""
    parts = month.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise ValueError(f"not a yyyy-mm month: {month!r}")
    year, mon = int(parts[0]), int(parts[1])
    if not 1 <= mon <= 12:
        raise ValueError(f"not a yyyy-mm month: {month!r}")
    return year, mon


def add_months(month: str, offset: int) -> str:
    """yyyy-mm plus a signed number of months."""
    year, mon = parse_month(month)
    total = year * 12 + (mon - 1) + offset
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_midpoint(month: str) -> date:
    """The 15th of a yyyy-mm month, used as the CDX target date."""
    year, mon = parse_month(month)
    return date(year, mon, 15)


def months_between(earlier: str, later: str) -> int:
    """Whole months from earlier yyyy-mm to later yyyy-mm."""
    y1, m1 = parse_month(earlier)
    y2, m2 = parse_month(later)
    return (y2 * 12 + m2) - (y1 * 12 + m1)
