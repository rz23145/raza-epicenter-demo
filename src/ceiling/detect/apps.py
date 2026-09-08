"""Match extracted assets against the workaround app dictionary.

One match per (scan, store, app_key), first match wins. Signatures with
verified: false are matched and recorded, but the score halves their weight
and ceiling doctor reports them until a human confirms the evidence URLs.
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

logger = get_logger("detect.apps")


def load_app_signatures(config_dir: Path) -> list[SignatureEntry]:
    entries = load_entries(config_dir / "app_signatures.yaml", "app")
    pending = unverified_keys(entries)
    if pending:
        logger.warning(
            "%d of %d app signatures are unverified: %s",
            len(pending),
            len(entries),
            ", ".join(pending),
        )
    return entries


def compile_app_signatures(entries: list[SignatureEntry]) -> list[CompiledSignature]:
    return compile_entries(entries)


def match_apps(
    compiled: list[CompiledSignature],
    extracts: list[ExtractResult],
    htmls: list[str],
) -> list[SignatureMatch]:
    matches: list[SignatureMatch] = []
    for signature in compiled:
        found = signature.match(extracts, htmls)
        if found is not None:
            matches.append(found)
    return matches
