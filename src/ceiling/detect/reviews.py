"""Review count readers, driven by config/review_widgets.yaml.

Two allowed methods only:
- html_widget: the count is rendered into HTML we already fetched.
- json_endpoint_referenced_in_page: the page source literally contains a URL
  to a public vendor JSON endpoint that the page itself loads. Only such
  literal URLs are fetched, robots-checked on the vendor host. Constructing
  vendor API URLs from shop IDs found in the page is inference and forbidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ceiling.detect.extract import ExtractResult
from ceiling.http.client import Client
from ceiling.logging import get_logger
from ceiling.util import host_of

logger = get_logger("detect.reviews")

METHOD_HTML = "html_widget"
METHOD_ENDPOINT = "json_endpoint_referenced_in_page"
METHOD_NONE = "not_available"


class ReviewVendorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: str
    detect_patterns: list[str] = Field(default_factory=list)
    count_regexes: list[str] = Field(default_factory=list)
    endpoint_url_regex: str | None = None
    verified: bool = False
    notes: str = ""


@dataclass
class ReviewReading:
    vendor: str
    review_count: int | None
    source_url: str | None
    method: str


def load_review_widgets(config_dir: Path) -> list[ReviewVendorConfig]:
    raw: Any = yaml.safe_load(
        (config_dir / "review_widgets.yaml").read_text(encoding="utf-8")
    )
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise TypeError("review_widgets.yaml must contain a YAML list")
    return [ReviewVendorConfig.model_validate(item) for item in raw]


def _detect(config: ReviewVendorConfig, extracts: list[ExtractResult], htmls: list[str]) -> bool:
    for pattern in config.detect_patterns:
        compiled = re.compile(pattern, re.IGNORECASE)
        for ex in extracts:
            for url in ex.script_srcs + ex.link_hrefs:
                if "//" in url and compiled.search(host_of(url)):
                    return True
        for html in htmls:
            if compiled.search(html):
                return True
    return False


def _count_from_text(config: ReviewVendorConfig, text: str) -> int | None:
    for pattern in config.count_regexes:
        found = re.search(pattern, text, re.IGNORECASE)
        if found:
            digits = found.group(1).replace(",", "")
            try:
                return int(digits)
            except ValueError:
                continue
    return None


def read_reviews(
    configs: list[ReviewVendorConfig],
    extracts: list[ExtractResult],
    pages: list[tuple[str, str]],
    client: Client | None = None,
    store_id: int | None = None,
    scan_id: int | None = None,
) -> list[ReviewReading]:
    """pages is a list of (url, html) already fetched. Returns one reading per
    detected vendor, or a single 'none' reading when nothing is detected."""
    htmls = [html for _url, html in pages]
    readings: list[ReviewReading] = []
    for config in configs:
        if not _detect(config, extracts, htmls):
            continue
        reading = ReviewReading(config.vendor, None, None, METHOD_NONE)
        for url, html in pages:
            count = _count_from_text(config, html)
            if count is not None:
                reading = ReviewReading(config.vendor, count, url, METHOD_HTML)
                break
        if (
            reading.review_count is None
            and config.endpoint_url_regex
            and client is not None
            and store_id is not None
            and scan_id is not None
        ):
            for url, html in pages:
                found = re.search(config.endpoint_url_regex, html, re.IGNORECASE)
                if not found:
                    continue
                endpoint = found.group(0)
                if not endpoint.startswith("http"):
                    logger.debug(
                        "endpoint match for %s is not a full literal URL, skipping",
                        config.vendor,
                    )
                    continue
                result = client.get(endpoint, "review_api", store_id, scan_id)
                if result.ok:
                    count = _count_from_text(config, result.text)
                    if count is not None:
                        reading = ReviewReading(
                            config.vendor, count, endpoint, METHOD_ENDPOINT
                        )
                break
        readings.append(reading)
    if not readings:
        readings.append(ReviewReading("none", None, None, METHOD_NONE))
    return readings
