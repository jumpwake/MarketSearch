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


def _seeded_db(tmp_path: Path, *listing_ids: str) -> Path:
    from marketsearch.models import RawListing

    path = tmp_path / "m.db"
    with Store(path) as store:
        store.initialize()
        for listing_id in listing_ids:
            store.upsert_listing(
                RawListing(
                    listing_id=listing_id, title="T770", price_cents=3_800_000,
                    location=None, url=f"https://example.com/{listing_id}",
                    thumbnail_url=None, seller_name=None,
                ),
                "fp",
            )
    return path


def test_dismiss_discards_several_listings_at_once(tmp_path: Path):
    """The dashboard hands over one command covering everything discarded in
    that sitting, so the command has to take a list."""
    db = _seeded_db(tmp_path, "a", "b")
    result = runner.invoke(app, ["dismiss", "a", "b", "--db", str(db),
                                 "--reason", "scam"])
    assert result.exit_code == 0
    assert "2 listing(s) discarded" in result.stdout

    with Store(db) as store:
        store.initialize()
        assert store.dismissed_listing_ids() == {"a", "b"}
        assert store.get_listing("a").dismiss_reason == "scam"


def test_dismiss_undo_restores(tmp_path: Path):
    db = _seeded_db(tmp_path, "a")
    runner.invoke(app, ["dismiss", "a", "--db", str(db)])
    result = runner.invoke(app, ["dismiss", "a", "--undo", "--db", str(db)])
    assert result.exit_code == 0
    assert "1 listing(s) restored" in result.stdout

    with Store(db) as store:
        store.initialize()
        assert store.dismissed_listing_ids() == set()


def test_dismiss_names_unknown_ids_without_failing_the_command(tmp_path: Path):
    """A copied command can carry an id that has since been pruned from the
    dashboard's window. The listings that do exist must still be discarded."""
    db = _seeded_db(tmp_path, "a")
    result = runner.invoke(app, ["dismiss", "a", "ghost", "--db", str(db)])
    assert result.exit_code == 0
    assert "1 listing(s) discarded" in result.stdout
    assert "ghost" in result.stdout

    with Store(db) as store:
        store.initialize()
        assert store.dismissed_listing_ids() == {"a"}


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
