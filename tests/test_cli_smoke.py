"""CLI smoke tests: init and doctor run end to end against a temp project dir."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ceiling.cli import app
from ceiling.settings import CONTACT_EMAIL_ENV

REPO_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


@pytest.fixture()
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_refuses_without_contact_email(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CONTACT_EMAIL_ENV, raising=False)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "contact email" in result.output.lower()
    assert not (project_dir / "data" / "ceiling.db").exists()


def test_init_creates_db_and_is_idempotent(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "research@example.com")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (project_dir / "data" / "ceiling.db").exists()
    assert "001_init.sql" in result.output

    again = runner.invoke(app, ["init"])
    assert again.exit_code == 0, again.output
    assert "0 new" in again.output


def test_init_rejects_rate_limit_below_floor(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "research@example.com")
    settings_path = project_dir / "config" / "settings.yaml"
    text = settings_path.read_text().replace(
        "per_host_min_interval_s: 1.0", "per_host_min_interval_s: 0.1"
    )
    settings_path.write_text(text)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "invalid settings" in result.output.lower()


def test_doctor_reports_status(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONTACT_EMAIL_ENV, "research@example.com")
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    out = result.output.lower()
    assert "config sha256" in out
    assert "unverified signature count" in out
    assert "open items for the human owner" in out
    assert "robots cache" in out


def test_doctor_runs_before_init(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CONTACT_EMAIL_ENV, raising=False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "not created yet" in result.output.lower()
    assert "not set" in result.output.lower()
