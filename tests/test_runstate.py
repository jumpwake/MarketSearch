from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketsearch.runstate import (
    AlreadyRunning,
    OperationalAlerts,
    RunLock,
    clear_needs_login,
    needs_login,
    set_needs_login,
)
from marketsearch.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "r.db")
    s.initialize()
    yield s
    s.close()


def test_lock_is_acquired_and_released(tmp_path: Path):
    path = tmp_path / "run.lock"
    with RunLock(path):
        assert path.exists()
    assert not path.exists()


def test_second_lock_raises_already_running(tmp_path: Path):
    path = tmp_path / "run.lock"
    with RunLock(path):
        with pytest.raises(AlreadyRunning):
            with RunLock(path):
                pass


def test_lock_released_even_when_body_raises(tmp_path: Path):
    path = tmp_path / "run.lock"
    with pytest.raises(ValueError):
        with RunLock(path):
            raise ValueError("boom")
    assert not path.exists()


def test_stale_lock_is_reclaimed(tmp_path: Path):
    """A machine that lost power mid-run must not be wedged forever."""
    path = tmp_path / "run.lock"
    path.write_text("99999", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(hours=12)).timestamp()
    import os
    os.utime(path, (old, old))

    with RunLock(path, stale_after_hours=6):
        assert path.exists()


def test_fresh_lock_is_not_reclaimed(tmp_path: Path):
    path = tmp_path / "run.lock"
    path.write_text("99999", encoding="utf-8")
    with pytest.raises(AlreadyRunning):
        with RunLock(path, stale_after_hours=6):
            pass


def test_needs_login_roundtrip(store: Store):
    assert needs_login(store) is None
    set_needs_login(store, "checkpoint")
    assert needs_login(store) == "checkpoint"


def test_clear_needs_login_reports_whether_it_was_set(store: Store):
    assert clear_needs_login(store) is False
    set_needs_login(store, "login")
    assert clear_needs_login(store) is True
    assert needs_login(store) is None


def test_first_operational_alert_is_allowed(store: Store):
    alerts = OperationalAlerts(store)
    assert alerts.should_send("parse_failure") is True


def test_second_alert_within_24h_is_suppressed(store: Store):
    alerts = OperationalAlerts(store)
    alerts.mark_sent("parse_failure")
    assert alerts.should_send("parse_failure") is False


def test_alert_is_allowed_again_after_24h(store: Store):
    now = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
    alerts = OperationalAlerts(store, now=lambda: now)
    alerts.mark_sent("parse_failure")

    later = now + timedelta(hours=25)
    alerts_later = OperationalAlerts(store, now=lambda: later)
    assert alerts_later.should_send("parse_failure") is True


def test_different_problems_throttle_independently(store: Store):
    alerts = OperationalAlerts(store)
    alerts.mark_sent("parse_failure")
    assert alerts.should_send("needs_login") is True


def test_clear_reports_whether_an_all_clear_is_warranted(store: Store):
    alerts = OperationalAlerts(store)
    assert alerts.clear("parse_failure") is False
    alerts.mark_sent("parse_failure")
    assert alerts.clear("parse_failure") is True
    assert alerts.clear("parse_failure") is False
    assert alerts.should_send("parse_failure") is True
