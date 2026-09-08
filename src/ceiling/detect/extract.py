"""Static extraction from storefront HTML. Never executes JavaScript."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from ceiling.logging import get_logger

logger = get_logger("detect.extract")

INLINE_SCRIPT_CAP_BYTES = 2 * 1024 * 1024

_MYSHOPIFY_RE = re.compile(r"([a-z0-9-]+\.myshopify\.com)")
_THEME_OBJ_RE = re.compile(r"Shopify\.theme\s*=\s*(\{.*?\})", re.DOTALL)
_THEME_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]*)"')
_THEME_ID_RE = re.compile(r'"id"\s*:\s*(\d+)')


@dataclass
class ExtractResult:
    script_srcs: list[str] = field(default_factory=list)
    link_hrefs: list[str] = field(default_factory=list)
    inline_script_text: str = ""
    meta: list[tuple[str, str]] = field(default_factory=list)
    jsonld: list[object] = field(default_factory=list)
    hreflang_alternates: list[tuple[str, str]] = field(default_factory=list)
    myshopify_domain: str | None = None
    theme_name: str | None = None
    theme_id: int | None = None


def extract(html: str) -> ExtractResult:
    soup = BeautifulSoup(html, "lxml")
    result = ExtractResult()

    inline_parts: list[str] = []
    inline_size = 0
    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue
        src = script.get("src")
        if isinstance(src, str) and src.strip():
            result.script_srcs.append(src.strip())
            continue
        text = script.get_text()
        if not text:
            continue
        stype = script.get("type")
        if isinstance(stype, str) and "ld+json" in stype:
            try:
                result.jsonld.append(json.loads(text))
            except (ValueError, TypeError):
                logger.debug("unparseable JSON-LD block skipped")
            continue
        if inline_size < INLINE_SCRIPT_CAP_BYTES:
            take = text[: INLINE_SCRIPT_CAP_BYTES - inline_size]
            inline_parts.append(take)
            inline_size += len(take)
    result.inline_script_text = "\n".join(inline_parts)

    for link in soup.find_all("link"):
        if not isinstance(link, Tag):
            continue
        href = link.get("href")
        if isinstance(href, str) and href.strip():
            result.link_hrefs.append(href.strip())
        rel = link.get("rel")
        rels = [rel] if isinstance(rel, str) else list(rel or [])
        hreflang = link.get("hreflang")
        if "alternate" in rels and isinstance(hreflang, str) and isinstance(href, str):
            result.hreflang_alternates.append((hreflang, href.strip()))

    for meta_tag in soup.find_all("meta"):
        if not isinstance(meta_tag, Tag):
            continue
        key = meta_tag.get("name") or meta_tag.get("property")
        content = meta_tag.get("content")
        if isinstance(key, str) and isinstance(content, str):
            result.meta.append((key, content))

    myshopify = _MYSHOPIFY_RE.search(result.inline_script_text)
    if myshopify:
        result.myshopify_domain = myshopify.group(1)

    theme_match = _THEME_OBJ_RE.search(result.inline_script_text)
    if theme_match:
        blob = theme_match.group(1)
        try:
            theme = json.loads(blob)
            if isinstance(theme, dict):
                name = theme.get("name")
                theme_id = theme.get("id")
                result.theme_name = str(name) if name is not None else None
                result.theme_id = int(theme_id) if theme_id is not None else None
        except (ValueError, TypeError):
            name_m = _THEME_NAME_RE.search(blob)
            id_m = _THEME_ID_RE.search(blob)
            result.theme_name = name_m.group(1) if name_m else None
            result.theme_id = int(id_m.group(1)) if id_m else None

    return result
