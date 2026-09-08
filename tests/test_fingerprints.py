"""Fingerprint matching, and the leak test: fingerprint keys never appear in
feature_json."""

from __future__ import annotations

import json

from ceiling.detect.apps import compile_app_signatures, load_app_signatures, match_apps
from ceiling.detect.extract import extract
from ceiling.detect.fingerprints import (
    compile_fingerprints,
    load_fingerprints,
    match_fingerprints,
)
from ceiling.features.build import build_features
from tests.conftest import REPO_ROOT, fixture_text

CONFIG_DIR = REPO_ROOT / "config"


def test_multipass_marker_matches() -> None:
    html = fixture_text("plus_store.html")
    entries = load_fingerprints(CONFIG_DIR)
    compiled = compile_fingerprints(entries)
    matches = match_fingerprints(
        entries, compiled, [extract(html)], [html], "plus.example"
    )
    keys = {m.key for m in matches}
    assert "multipass_login" in keys
    assert "shopify_plus_text" in keys


def test_hreflang_expansion_fingerprint() -> None:
    html = fixture_text("shop_home.html")
    entries = load_fingerprints(CONFIG_DIR)
    compiled = compile_fingerprints(entries)
    matches = match_fingerprints(
        entries, compiled, [extract(html)], [html], "shop.example"
    )
    keys = {m.key for m in matches}
    # two external hreflang hosts (shop-de.example, shop-fr.example)
    assert "expansion_store_hreflang" in keys


def test_fingerprint_keys_never_appear_in_feature_json() -> None:
    """The leak test. Build features from a fixture that triggers both app and
    fingerprint matches and assert no fingerprint key is a feature key."""
    html = fixture_text("shop_home.html")
    ex = extract(html)

    app_entries = load_app_signatures(CONFIG_DIR)
    app_matches = match_apps(compile_app_signatures(app_entries), [ex], [html])

    fp_entries = load_fingerprints(CONFIG_DIR)
    fp_matches = match_fingerprints(
        fp_entries, compile_fingerprints(fp_entries), [ex], [html], "shop.example"
    )
    assert fp_matches, "fixture should trigger at least one fingerprint"

    features = build_features(
        app_matches=app_matches,
        catalog=None,
        reviews=[],
        home_extract=ex,
        products_json_available=False,
    )
    feature_json = json.dumps(features)
    fingerprint_keys = {e.key for e in fp_entries}
    flat_keys = set(features.keys())
    for sub in features.values():
        if isinstance(sub, dict):
            flat_keys.update(sub.keys())
    assert not (fingerprint_keys & flat_keys)
    for key in fingerprint_keys:
        assert f'"{key}"' not in feature_json
