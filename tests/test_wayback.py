"""Wayback: snapshot selection within tolerance, id_ URL construction, CDX parse."""

from __future__ import annotations

from ceiling.wayback.cdx import CdxRow, cdx_url, parse_cdx, pick_snapshot
from ceiling.wayback.replay import snapshot_url


def rows(*timestamps: str) -> list[CdxRow]:
    return [CdxRow(ts, "200", "text/html", f"D{ts}") for ts in timestamps]


def test_closest_snapshot_within_tolerance_chosen() -> None:
    candidates = rows("20250601120000", "20250614080000", "20250710000000")
    # target month 2025-06, midpoint June 15
    chosen = pick_snapshot(candidates, "2025-06", tolerance_days=45)
    assert chosen == "20250614080000"


def test_none_within_tolerance_yields_null() -> None:
    candidates = rows("20240101000000", "20260101000000")
    assert pick_snapshot(candidates, "2025-06", tolerance_days=45) is None
    assert pick_snapshot([], "2025-06", tolerance_days=45) is None


def test_tolerance_boundary() -> None:
    # 45 days after June 15 2025 is July 30
    candidates = rows("20250730000000")
    assert pick_snapshot(candidates, "2025-06", tolerance_days=45) == "20250730000000"
    candidates = rows("20250731000000")
    assert pick_snapshot(candidates, "2025-06", tolerance_days=45) is None


def test_id_url_constructed_correctly() -> None:
    assert (
        snapshot_url("20250614080000", "shop.example")
        == "https://web.archive.org/web/20250614080000id_/https://shop.example/"
    )


def test_cdx_url_and_parse() -> None:
    url = cdx_url("shop.example", "2024-01", "2025-06")
    assert "url=shop.example" in url
    assert "from=202401" in url and "to=202506" in url
    assert "filter=statuscode:200" in url

    body = (
        '[["timestamp","statuscode","mimetype","digest"],'
        '["20250614080000","200","text/html","ABCD"]]'
    )
    parsed = parse_cdx(body)
    assert len(parsed) == 1
    assert parsed[0].timestamp == "20250614080000"

    assert parse_cdx("not json") == []
    assert parse_cdx("[]") == []
