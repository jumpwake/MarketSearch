from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marketsearch.cli import app

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copy("config.example.yaml", tmp_path / "config.yaml")
    return tmp_path


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "login", "test-search", "history"):
        assert command in result.stdout


def test_run_reports_a_missing_config_clearly(tmp_path: Path):
    result = runner.invoke(app, ["run", "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code != 0
    assert "not found" in result.stdout


def test_run_refuses_a_second_concurrent_run(project: Path, monkeypatch):
    lock = project / "marketsearch.lock"
    lock.write_text("1234", encoding="utf-8")
    result = runner.invoke(app, [
        "run", "--config", str(project / "config.yaml"),
        "--db", str(project / "m.db"), "--lock", str(lock),
    ])
    assert result.exit_code != 0
    assert "already running" in result.stdout.lower()


def test_history_on_an_empty_database_says_so(project: Path):
    result = runner.invoke(app, [
        "history", "--db", str(project / "m.db"),
    ])
    assert result.exit_code == 0
    assert "no runs" in result.stdout.lower()


def test_history_lists_completed_runs(project: Path):
    from marketsearch.store import Store

    db = project / "m.db"
    with Store(db) as store:
        store.initialize()
        run_id = store.start_run()
        store.finish_run(run_id, {"found": 12, "new": 3, "matched": 1, "errors": 0})

    result = runner.invoke(app, ["history", "--db", str(db)])
    assert result.exit_code == 0
    assert "12" in result.stdout
    assert "1" in result.stdout
