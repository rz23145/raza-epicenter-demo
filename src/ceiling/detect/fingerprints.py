"""Match against Plus fingerprints.

Fingerprints identify a store that is already on Plus. They are used for
exactly three things: building the positive label set, detecting conversions
in the panel, and excluding Plus stores from the self-serve panel. They are
never features in the ceiling score; a test fails if any fingerprint key
appears in feature_json.

The expansion_store_hreflang fingerprint is computed from extracted hreflang
alternates rather than a regex: two or more alternates pointing at domains
other than the store's own is circumstantial evidence of expansion stores.
Non-Plus merchants running two separate plans produce the same pattern, which
is why this fingerprint stays verified: false until a human reviews it.
"""

from __future__ import annotations

from pathlib import Path

from ceiling.detect.extract import ExtractResult
from ceiling.detect.signatures import (
    CompiledSignature,
    SignatureEntry,
    SignatureMatch,
    compile_entries,
    load_entries,
    unverified_keys,
)
from ceiling.logging import get_logger
from ceiling.util import host_of

logger = get_logger("detect.fingerprints")

HREFLANG_FINGERPRINT_KEY = "expansion_store_hreflang"
HREFLANG_MIN_EXTERNAL = 2


def load_fingerprints(config_dir: Path) -> list[SignatureEntry]:
    entries = load_entries(config_dir / "plus_fingerprints.yaml", "fingerprint")
    pending = unverified_keys(entries)
    if pending:
        logger.warning(
            "%d of %d Plus fingerprints are unverified: %s",
            len(pending),
            len(entries),
            ", ".join(pending),
        )
    return entries


def compile_fingerprints(entries: list[SignatureEntry]) -> list[CompiledSignature]:
    return compile_entries(entries)


def _hreflang_match(
    entry: SignatureEntry, extracts: list[ExtractResult], store_domain: str
) -> SignatureMatch | None:
    external: list[str] = []
    for ex in extracts:
        for _lang, href in ex.hreflang_alternates:
            if "//" not in href:
                continue
            host = host_of(href)
            if host and host != store_domain and not host.endswith(store_domain):
                external.append(href)
    unique_hosts = {host_of(h) for h in external}
    if len(unique_hosts) >= HREFLANG_MIN_EXTERNAL:
        return SignatureMatch(
            key=entry.key,
            capability=None,
            matched_on=";".join(sorted(unique_hosts))[:500],
            pattern=f"hreflang alternates on >= {HREFLANG_MIN_EXTERNAL} external hosts",
            verified=entry.verified,
        )
    return None


def match_fingerprints(
    entries: list[SignatureEntry],
    compiled: list[CompiledSignature],
    extracts: list[ExtractResult],
    htmls: list[str],
    store_domain: str,
) -> list[SignatureMatch]:
    matches: list[SignatureMatch] = []
    for entry, signature in zip(entries, compiled, strict=True):
        if entry.key == HREFLANG_FINGERPRINT_KEY:
            found = _hreflang_match(entry, extracts, store_domain)
        else:
            found = signature.match(extracts, htmls)
        if found is not None:
            matches.append(found)
    return matches
