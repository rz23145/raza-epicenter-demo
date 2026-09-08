"""App signature matching against fixtures and load-time guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from ceiling.detect.apps import compile_app_signatures, load_app_signatures, match_apps
from ceiling.detect.extract import extract
from ceiling.detect.signatures import CompiledSignature, SignatureEntry
from tests.conftest import REPO_ROOT, fixture_text

CONFIG_DIR = REPO_ROOT / "config"


def test_sparklayer_fixture_matches_b2b_wholesale() -> None:
    html = fixture_text("shop_home.html")
    entries = load_app_signatures(CONFIG_DIR)
    compiled = compile_app_signatures(entries)
    matches = match_apps(compiled, [extract(html)], [html])
    by_key = {m.key: m for m in matches}
    assert "sparklayer" in by_key
    assert by_key["sparklayer"].capability == "b2b_wholesale"
    assert "sparklayer" in by_key["sparklayer"].matched_on
    # one row per app_key even though both a script host and a dom marker match
    assert sum(1 for m in matches if m.key == "sparklayer") == 1


def test_extensions_path_alone_matches_nothing() -> None:
    html = fixture_text("extensions_only.html")
    entries = load_app_signatures(CONFIG_DIR)
    compiled = compile_app_signatures(entries)
    matches = match_apps(compiled, [extract(html)], [html])
    assert matches == []


def test_unverified_signatures_are_flagged() -> None:
    html = fixture_text("shop_home.html")
    entries = load_app_signatures(CONFIG_DIR)
    compiled = compile_app_signatures(entries)
    matches = match_apps(compiled, [extract(html)], [html])
    assert matches, "fixture should match at least one signature"
    assert all(m.verified is False for m in matches)


def test_pattern_matching_cdn_shopify_host_is_rejected() -> None:
    entry = SignatureEntry.model_validate(
        {
            "app_key": "bad_app",
            "capability": "discount_logic",
            "patterns": {"script_host": ["cdn\\.shopify\\.com"]},
        }
    )
    with pytest.raises(ValueError, match="forbidden"):
        CompiledSignature(entry)


def test_unknown_capability_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "app_signatures.yaml"
    bad.write_text(
        "- app_key: x\n  capability: teleportation\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="taxonomy"):
        load_app_signatures(tmp_path)
