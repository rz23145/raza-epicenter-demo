"""Settings loading and validation.

All rate limit values are floors mandated by the crawling policy. Validation
rejects any value faster than the floor. Values may only be made slower:
larger delays, fewer retry attempts, an earlier per-host hard stop.

The contact email is required before any HTTP is performed. It comes from
settings.yaml or the CEILING_CONTACT_EMAIL environment variable, and the
environment variable wins.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

CONTACT_EMAIL_ENV = "CEILING_CONTACT_EMAIL"

CONFIG_SHA_FILES = ("settings.yaml", "app_signatures.yaml", "plus_fingerprints.yaml")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SettingsError(Exception):
    """Raised when a required setting is missing or unusable at use time."""


class HttpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_agent_template: str = "CeilingIndexResearchBot/0.1 (+mailto:{contact_email})"
    connect_timeout_s: float = 20.0
    read_timeout_s: float = 30.0


class RateLimitSettings(BaseModel):
    """Crawling politeness. Floors are hard policy, not defaults."""

    model_config = ConfigDict(extra="forbid")

    per_host_min_interval_s: float = 1.0
    inter_host_min_interval_s: float = 2.0
    backoff_base_s: float = 5.0
    backoff_max_s: float = 60.0
    max_attempts: int = 4
    max_consecutive_429_per_host: int = 3

    @field_validator("per_host_min_interval_s")
    @classmethod
    def _floor_per_host(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError(
                "per_host_min_interval_s below the policy floor of 1.0s: "
                "rate limits are configurable upward only"
            )
        return v

    @field_validator("inter_host_min_interval_s")
    @classmethod
    def _floor_inter_host(cls, v: float) -> float:
        if v < 2.0:
            raise ValueError(
                "inter_host_min_interval_s below the policy floor of 2.0s: "
                "rate limits are configurable upward only"
            )
        return v

    @field_validator("backoff_base_s")
    @classmethod
    def _floor_backoff(cls, v: float) -> float:
        if v < 5.0:
            raise ValueError(
                "backoff_base_s below the policy floor of 5.0s: "
                "rate limits are configurable upward only"
            )
        return v

    @field_validator("max_attempts")
    @classmethod
    def _cap_attempts(cls, v: int) -> int:
        if not 1 <= v <= 4:
            raise ValueError("max_attempts must be between 1 and 4")
        return v

    @field_validator("max_consecutive_429_per_host")
    @classmethod
    def _cap_429(cls, v: int) -> int:
        if not 1 <= v <= 3:
            raise ValueError(
                "max_consecutive_429_per_host must be between 1 and 3: "
                "the hard stop may only be made stricter"
            )
        return v


class PathSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: Path = Path("data")
    db_path: Path = Path("data/ceiling.db")
    raw_dir: Path = Path("data/raw")
    exports_dir: Path = Path("data/exports")
    logs_dir: Path = Path("data/logs")
    labels_dir: Path = Path("data/labels")


class ScanSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products_json_max_pages: int = 40
    products_json_page_limit: int = 250


class ScoreSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weights: dict[str, float] = {}
    thresholds: dict[str, float] = {}
    unverified_weight_multiplier: float = 0.5


class PanelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_size: int = 750


class WaybackSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_interval_s: float = 2.0
    tolerance_days: int = 45
    offsets_months: list[int] = [-18, -12, -6, -3, 3]

    @field_validator("min_interval_s")
    @classmethod
    def _floor_interval(cls, v: float) -> float:
        if v < 2.0:
            raise ValueError("wayback min_interval_s below the policy floor of 2.0s")
        return v


class HiringSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_interval_s: float = 2.0
    terms: list[str] = []

    @field_validator("min_interval_s")
    @classmethod
    def _floor_interval(cls, v: float) -> float:
        if v < 2.0:
            raise ValueError("hiring min_interval_s below the policy floor of 2.0s")
        return v


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_email: str | None = None
    http: HttpSettings = HttpSettings()
    rate_limits: RateLimitSettings = RateLimitSettings()
    paths: PathSettings = PathSettings()
    scan: ScanSettings = ScanSettings()
    score: ScoreSettings = ScoreSettings()
    panel: PanelSettings = PanelSettings()
    wayback: WaybackSettings = WaybackSettings()
    hiring: HiringSettings = HiringSettings()

    @field_validator("contact_email")
    @classmethod
    def _email_format(cls, v: str | None) -> str | None:
        if v is not None and not _EMAIL_RE.match(v):
            raise ValueError(f"contact_email does not look like an email: {v!r}")
        return v

    def require_contact_email(self) -> str:
        if not self.contact_email:
            raise SettingsError(
                "Contact email is not set. Set the CEILING_CONTACT_EMAIL "
                "environment variable or contact_email in config/settings.yaml. "
                "The pipeline refuses to fetch anything without it."
            )
        return self.contact_email

    def user_agent(self) -> str:
        return self.http.user_agent_template.format(
            contact_email=self.require_contact_email()
        )


def load_settings(config_dir: Path) -> Settings:
    """Load settings.yaml from config_dir, with env override for contact email."""
    settings_path = config_dir / "settings.yaml"
    raw: Any = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SettingsError(f"{settings_path} must contain a YAML mapping")
    env_email = os.environ.get(CONTACT_EMAIL_ENV)
    if env_email:
        raw["contact_email"] = env_email
    return Settings.model_validate(raw)


def config_sha(config_dir: Path) -> str:
    """SHA-256 over settings.yaml, app_signatures.yaml, plus_fingerprints.yaml.

    Stored on every scans row so any result can be tied to the exact config
    that produced it.
    """
    digest = hashlib.sha256()
    for name in CONFIG_SHA_FILES:
        digest.update((config_dir / name).read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class SignatureCounts:
    apps_total: int
    apps_verified: int
    fingerprints_total: int
    fingerprints_verified: int

    @property
    def unverified(self) -> int:
        return (
            self.apps_total
            - self.apps_verified
            + self.fingerprints_total
            - self.fingerprints_verified
        )


def _count_entries(path: Path) -> tuple[int, int]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise SettingsError(f"{path} must contain a YAML list")
    total = 0
    verified = 0
    for entry in raw:
        if not isinstance(entry, dict):
            raise SettingsError(f"{path}: every entry must be a mapping")
        total += 1
        if entry.get("verified") is True:
            verified += 1
    return total, verified


def signature_counts(config_dir: Path) -> SignatureCounts:
    """Count total and verified entries in both signature dictionaries."""
    apps_total, apps_verified = _count_entries(config_dir / "app_signatures.yaml")
    fp_total, fp_verified = _count_entries(config_dir / "plus_fingerprints.yaml")
    return SignatureCounts(apps_total, apps_verified, fp_total, fp_verified)
