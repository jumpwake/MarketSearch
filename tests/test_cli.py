from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marketsearch.cli import _load, app, build_dispatcher
from marketsearch.notify.delivery import DeliveryError
from marketsearch.store import Store

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copy(Path(__file__).parent / "fixtures" / "config.yaml", tmp_path / "config.yaml")
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


def test_disabled_notifications_do_not_require_delivery_secrets(project: Path, monkeypatch):
    """A shakedown run with notifications off must not demand an SMTP password or
    Twilio credentials — nothing is ever sent, so nothing should be resolved."""
    for name in ("MARKETSEARCH_SMTP_PASSWORD", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    cfg = _load(project / "config.yaml")
    assert cfg.notifications.enabled is False

    with Store(project / "m.db") as store:
        store.initialize()
        dispatcher = build_dispatcher(cfg, store)

    assert dispatcher.dispatch([], [], []) is False


def test_enabled_notifications_still_require_delivery_secrets(project: Path, monkeypatch):
    """The check is deferred, not dropped — a missing secret must still be caught."""
    monkeypatch.delenv("MARKETSEARCH_SMTP_PASSWORD", raising=False)
    text = (project / "config.yaml").read_text(encoding="utf-8")
    (project / "config.yaml").write_text(
        text.replace("enabled: false", "enabled: true"), encoding="utf-8"
    )
    cfg = _load(project / "config.yaml")

    with Store(project / "m.db") as store:
        store.initialize()
        with pytest.raises(DeliveryError, match="MARKETSEARCH_SMTP_PASSWORD"):
            build_dispatcher(cfg, store)


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
