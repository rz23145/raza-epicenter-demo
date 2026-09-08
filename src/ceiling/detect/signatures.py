"""Shared signature model for app signatures and Plus fingerprints.

Both YAML dictionaries use the same entry shape. Pattern types:
- script_host: regex matched against the parsed host of every script src and
  link href. Patterns that would match the bare host cdn.shopify.com are
  rejected at load time; that host serves app blocks for thousands of apps
  and is not specific to any one of them.
- script_src: regex matched against the full URL of script srcs and link
  hrefs, for cases where only a path segment identifies the app.
- dom_marker: regex matched against the raw HTML, case-insensitive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ceiling.detect.extract import ExtractResult
from ceiling.util import host_of

CAPABILITIES = frozenset(
    {
        "b2b_wholesale",
        "multistore_sync",
        "launch_scheduling",
        "discount_logic",
        "sso_login",
        "international_markets",
        "checkout_customization",
        "staff_permissions",
    }
)

_FORBIDDEN_HOST = "cdn.shopify.com"


class SignaturePatterns(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_host: list[str] = Field(default_factory=list)
    script_src: list[str] = Field(default_factory=list)
    dom_marker: list[str] = Field(default_factory=list)


class SignatureEntry(BaseModel):
    """One entry of app_signatures.yaml or plus_fingerprints.yaml."""

    model_config = ConfigDict(extra="forbid")

    app_key: str | None = None
    fingerprint_key: str | None = None
    display_name: str = ""
    vendor: str = ""
    capability: str | None = None
    plus_native_equivalent: str = ""
    plus_native_evidence_url: str = ""
    app_evidence_url: str = ""
    evidence_url: str = ""
    patterns: SignaturePatterns = Field(default_factory=SignaturePatterns)
    plus_compatible: bool | None = None
    used_on_plus_commonly: bool | None = None
    confidence: str = "low"
    verified: bool = False
    notes: str = ""

    @field_validator("confidence")
    @classmethod
    def _confidence_values(cls, v: str) -> str:
        if v not in {"high", "medium", "low"}:
            raise ValueError(f"confidence must be high, medium, or low, got {v!r}")
        return v

    @property
    def key(self) -> str:
        key = self.app_key or self.fingerprint_key
        if not key:
            raise ValueError("signature entry has neither app_key nor fingerprint_key")
        return key


@dataclass
class SignatureMatch:
    key: str
    capability: str | None
    matched_on: str
    pattern: str
    verified: bool


class CompiledSignature:
    def __init__(self, entry: SignatureEntry) -> None:
        self.entry = entry
        self.host_patterns = [
            re.compile(p, re.IGNORECASE) for p in entry.patterns.script_host
        ]
        self.src_patterns = [
            re.compile(p, re.IGNORECASE) for p in entry.patterns.script_src
        ]
        self.dom_patterns = [
            re.compile(p, re.IGNORECASE) for p in entry.patterns.dom_marker
        ]
        for compiled in self.host_patterns:
            if compiled.search(_FORBIDDEN_HOST):
                raise ValueError(
                    f"signature {entry.key}: script_host pattern"
                    f" {compiled.pattern!r} matches {_FORBIDDEN_HOST}, which is"
                    " shared by thousands of apps and is forbidden as a signature"
                )

    def match(
        self, extracts: list[ExtractResult], htmls: list[str]
    ) -> SignatureMatch | None:
        """First match wins. Returns None when nothing matches."""
        for ex in extracts:
            for url in ex.script_srcs + ex.link_hrefs:
                if "//" not in url:
                    continue
                host = host_of(url)
                for compiled in self.host_patterns:
                    if compiled.search(host):
                        return self._make(url, compiled.pattern)
                for compiled in self.src_patterns:
                    if compiled.search(url):
                        return self._make(url, compiled.pattern)
        for html in htmls:
            for compiled in self.dom_patterns:
                found = compiled.search(html)
                if found:
                    return self._make(found.group(0), compiled.pattern)
        return None

    def _make(self, matched_on: str, pattern: str) -> SignatureMatch:
        return SignatureMatch(
            key=self.entry.key,
            capability=self.entry.capability,
            matched_on=matched_on[:500],
            pattern=pattern,
            verified=self.entry.verified,
        )


def load_entries(path: Path, kind: str) -> list[SignatureEntry]:
    """Load and validate a signature YAML. kind is 'app' or 'fingerprint'."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise TypeError(f"{path} must contain a YAML list")
    entries: list[SignatureEntry] = []
    for item in raw:
        entry = SignatureEntry.model_validate(item)
        if kind == "app":
            if entry.app_key is None:
                raise ValueError(f"{path}: entry missing app_key")
            if entry.capability not in CAPABILITIES:
                raise ValueError(
                    f"{path}: {entry.app_key}: capability {entry.capability!r}"
                    " is not in the fixed taxonomy"
                )
        elif entry.fingerprint_key is None:
            raise ValueError(f"{path}: entry missing fingerprint_key")
        entries.append(entry)
    return entries


def compile_entries(entries: list[SignatureEntry]) -> list[CompiledSignature]:
    return [CompiledSignature(e) for e in entries]


def unverified_keys(entries: list[SignatureEntry]) -> list[str]:
    return [e.key for e in entries if not e.verified]
