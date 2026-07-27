"""Operational state: the run lock, the needs-login flag, and alert throttling."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from marketsearch.store import Store

log = logging.getLogger(__name__)

NEEDS_LOGIN_KEY = "needs_login"
_ALERT_KEY_PREFIX = "alert_last_sent:"
_ALERT_INTERVAL_HOURS = 24


class AlreadyRunning(Exception):
    """Another sweep is in progress."""


class RunLock:
    """Prevents a slow sweep from colliding with the next scheduled one.

    A lock older than `stale_after_hours` is reclaimed, so a machine that lost
    power mid-run is not wedged until someone notices.
    """

    def __init__(self, path: Path, stale_after_hours: int = 6) -> None:
        self.path = Path(path)
        self.stale_after_hours = stale_after_hours
        self._held = False

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not self._is_stale():
                raise AlreadyRunning(f"another run holds {self.path}") from None
            log.warning("reclaiming stale lock at %s", self.path)
            self.path.unlink(missing_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        self._held = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def _is_stale(self) -> bool:
        try:
            age = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
        except FileNotFoundError:
            return True
        return age > self.stale_after_hours * 3600


def set_needs_login(store: Store, kind: str) -> None:
    store.set_state(NEEDS_LOGIN_KEY, kind)


def needs_login(store: Store) -> str | None:
    value = store.get_state(NEEDS_LOGIN_KEY)
    return value or None


def clear_needs_login(store: Store) -> bool:
    """Clear the flag. Returns True if it had been set."""
    was_set = needs_login(store) is not None
    store.set_state(NEEDS_LOGIN_KEY, "")
    return was_set


class OperationalAlerts:
    """One alert per problem per 24 hours, plus one all-clear when it resolves."""

    def __init__(self, store: Store, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _key(self, problem: str) -> str:
        return f"{_ALERT_KEY_PREFIX}{problem}"

    def should_send(self, problem: str) -> bool:
        raw = self._store.get_state(self._key(problem))
        if not raw:
            return True
        last = datetime.fromisoformat(raw)
        return self._now() - last >= timedelta(hours=_ALERT_INTERVAL_HOURS)

    def mark_sent(self, problem: str) -> None:
        self._store.set_state(self._key(problem), self._now().isoformat())

    def clear(self, problem: str) -> bool:
        """Reset the throttle. Returns True if an alert was outstanding, which
        is the caller's cue to send a single 'back to normal' message."""
        was_active = bool(self._store.get_state(self._key(problem)))
        self._store.set_state(self._key(problem), "")
        return was_active
