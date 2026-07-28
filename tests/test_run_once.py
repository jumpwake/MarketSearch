from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.pipeline import run_once
from marketsearch.runstate import OperationalAlerts, needs_login, set_needs_login
from marketsearch.sources.base import LoginRequired, ParseError
from marketsearch.store import Store

from tests.test_pipeline_scan import FakeExtractor, listing, watchlist_config
from tests.test_pipeline_watched import FakeWatchSource


class FakeSource(FakeWatchSource):
    """A source that also returns search results."""

    def __init__(self, results=None, search_error=None, **kwargs):
        super().__init__(**kwargs)
        self.results = results or []
        self.search_error = search_error

    def search(self, query, location, radius_miles):
        if self.search_error is not None:
            raise self.search_error
        return list(self.results)


class RecordingDispatcher:
    def __init__(self, result=True):
        self.calls: list[tuple] = []
        self._result = result

    def dispatch(self, matches, unverified, changes):
        self.calls.append((matches, unverified, changes))
        return self._result


class RecordingOperational:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def __call__(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "run.db")
    s.initialize()
    yield s
    s.close()


def execute(store, source, dispatcher=None, operational=None, **kwargs):
    return run_once(
        config=watchlist_config(), store=store, source=source, extractor=FakeExtractor(),
        dispatcher=dispatcher or RecordingDispatcher(),
        alerts=OperationalAlerts(store),
        notify_operational=operational or RecordingOperational(),
        **kwargs,
    )


def test_a_normal_run_scans_syncs_and_dispatches(store: Store):
    dispatcher = RecordingDispatcher()
    report = execute(store, FakeSource(results=[listing("1")]), dispatcher)
    assert report.blocked is None
    assert report.counters.matched == 1
    assert report.notified is True
    assert len(dispatcher.calls) == 1


def test_run_row_is_written_and_closed(store: Store):
    execute(store, FakeSource(results=[listing("1")]))
    row = store._conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    assert row["ended_at"] is not None
    assert row["matched"] == 1


def test_login_required_sets_the_flag_and_blocks(store: Store):
    source = FakeSource(search_error=LoginRequired("checkpoint"))
    report = execute(store, source)
    assert report.blocked == "checkpoint"
    assert needs_login(store) == "checkpoint"


def test_login_required_sends_one_operational_alert(store: Store):
    operational = RecordingOperational()
    execute(store, FakeSource(search_error=LoginRequired("checkpoint")), operational=operational)
    assert len(operational.sent) == 1
    assert "log in" in operational.sent[0][0].lower()


def test_a_blocked_account_stops_the_next_run_before_touching_facebook(store: Store):
    set_needs_login(store, "checkpoint")
    source = FakeSource(results=[listing("1")])
    report = execute(store, source)
    assert report.blocked == "checkpoint"
    assert source.detail_calls == []


def test_repeated_login_alerts_are_throttled(store: Store):
    operational = RecordingOperational()
    for _ in range(3):
        execute(store, FakeSource(search_error=LoginRequired("checkpoint")),
                operational=operational)
    assert len(operational.sent) == 1


def test_clearing_the_block_sends_one_all_clear(store: Store):
    operational = RecordingOperational()
    execute(store, FakeSource(search_error=LoginRequired("checkpoint")), operational=operational)
    store.set_state("needs_login", "")  # user ran `marketsearch login`
    execute(store, FakeSource(results=[listing("1")]), operational=operational)
    assert len(operational.sent) == 2
    assert "back to normal" in operational.sent[1][0].lower()


def test_parse_failure_alerts_once_and_does_not_block(store: Store):
    operational = RecordingOperational()
    source = FakeSource(search_error=ParseError("markup changed"))
    report = execute(store, source, operational=operational)
    assert report.blocked is None
    assert len(operational.sent) == 1
    assert "parse" in operational.sent[0][0].lower()


def test_repeated_parse_failures_are_throttled(store: Store):
    operational = RecordingOperational()
    for _ in range(3):
        execute(store, FakeSource(search_error=ParseError("markup changed")),
                operational=operational)
    assert len(operational.sent) == 1


def test_dry_run_does_not_dispatch(store: Store):
    dispatcher = RecordingDispatcher()
    report = execute(store, FakeSource(results=[listing("1")]), dispatcher, dry_run=True)
    assert dispatcher.calls == []
    assert report.notified is False
    assert report.counters.matched == 1


def test_watched_changes_reach_the_dispatcher(store: Store):
    from marketsearch.models import ListingDetail
    from marketsearch.pipeline import content_hash

    store.upsert_listing(listing("1", price_cents=4_100_000), "fp",
                         "track-loaders", "bobcat-t770")
    detail = ListingDetail(listing_id="1", description="2400 hours",
                           structured_fields={}, photo_urls=[], distance_miles=None)
    store.save_detail(detail, content_hash(detail))

    dispatcher = RecordingDispatcher()
    execute(store, FakeSource(saved=[listing("1", price_cents=3_800_000)]), dispatcher)
    _matches, _unverified, changes = dispatcher.calls[0]
    assert len(changes) == 1
    assert changes[0].kind == "price_change"
