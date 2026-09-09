"""Settings validation: rate limit floors, contact email, config sha."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from ceiling.settings import (
    CONTACT_EMAIL_ENV,
    Settings,
    SettingsError,
    config_sha,
    load_settings,
    signature_counts,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


def test_default_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTACT_EMAIL_ENV, raising=False)
    settings = load_settings(CONFIG_DIR)
    assert settings.rate_limits.per_host_min_interval_s >= 1.0
    assert settings.contact_email is None


def test_env_email_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "research@example.com")
    settings = load_settings(CONFIG_DIR)
    assert settings.contact_email == "research@example.com"
    assert "research@example.com" in settings.user_agent()
    assert settings.user_agent().startswith("CeilingIndexResearchBot/0.1")


def test_missing_email_refused_at_use_time() -> None:
    settings = Settings()
    with pytest.raises(SettingsError):
        settings.require_contact_email()
    with pytest.raises(SettingsError):
        settings.user_agent()


def test_bad_email_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"contact_email": "not-an-email"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("per_host_min_interval_s", 0.5),
        ("inter_host_min_interval_s", 1.0),
        ("backoff_base_s", 2.0),
        ("max_attempts", 10),
        ("max_consecutive_429_per_host", 5),
    ],
)
def test_rate_limit_floors_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"rate_limits": {field: value}})


def test_rate_limits_configurable_upward() -> None:
    settings = Settings.model_validate(
        {
            "rate_limits": {
                "per_host_min_interval_s": 3.0,
                "inter_host_min_interval_s": 5.0,
                "backoff_base_s": 10.0,
                "max_consecutive_429_per_host": 1,
            }
        }
    )
    assert settings.rate_limits.per_host_min_interval_s == 3.0


def test_wayback_and_hiring_floors_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"wayback": {"min_interval_s": 0.1}})
    with pytest.raises(ValidationError):
        Settings.model_validate({"hiring": {"min_interval_s": 0.1}})


def test_config_sha_changes_when_signatures_change(tmp_path: Path) -> None:
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    before = config_sha(tmp_path / "config")
    assert before == config_sha(CONFIG_DIR)
    with (tmp_path / "config" / "app_signatures.yaml").open("a") as fh:
        fh.write("\n# changed\n")
    after = config_sha(tmp_path / "config")
    assert before != after


def test_signature_counts_on_seed_config() -> None:
    counts = signature_counts(CONFIG_DIR)
    assert counts.apps_verified <= counts.apps_total
    assert counts.fingerprints_verified <= counts.fingerprints_total
    assert counts.unverified >= 0


def test_doctor_verified_count_matches_top_level_yaml_entries() -> None:
    """The doctor count must equal the number of top-level entries with
    verified: true. Guards against comments or nested fields containing the
    literal string 'verified: true' skewing naive counting, and against
    duplicate or malformed entries skewing the parser."""
    import yaml

    for filename, total_attr, verified_attr in [
        ("app_signatures.yaml", "apps_total", "apps_verified"),
        ("plus_fingerprints.yaml", "fingerprints_total", "fingerprints_verified"),
    ]:
        raw = yaml.safe_load((CONFIG_DIR / filename).read_text(encoding="utf-8"))
        assert isinstance(raw, list)
        top_level_verified = sum(1 for e in raw if e.get("verified") is True)
        counts = signature_counts(CONFIG_DIR)
        assert getattr(counts, total_attr) == len(raw), filename
        assert getattr(counts, verified_attr) == top_level_verified, filename
