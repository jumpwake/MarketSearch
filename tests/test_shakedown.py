from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketsearch.models import ListingDetail
from marketsearch.pipeline import content_hash
from marketsearch.shakedown import (
    ReplayRow,
    _watchlist_by_name,
    collect_run_cards,
    format_replay,
    parse_since,
    replay,
)
from marketsearch.store import Store

from tests.test_pipeline_scan import (
    FakeExtractor,
    config,
    extraction,
    listing,
    watchlist_config,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "s.db")
    s.initialize()
    yield s
    s.close()


def seed_extracted(
    store: Store, listing_id: str, verdict: str = "match",
    watchlist_name: str | None = "track-loaders", model_name: str | None = "bobcat-t770",
) -> None:
    store.upsert_listing(listing(listing_id), "bobcat-t770", f"fp{listing_id}",
                         watchlist_name=watchlist_name, model_name=model_name)
    detail = ListingDetail(listing_id=listing_id, description="2400 hours",
                           structured_fields={}, photo_urls=[], distance_miles=None)
    store.save_detail(detail, content_hash(detail))
    store.save_extraction(
        listing_id=listing_id, attributes={"core": {"engine_hours": 2400}},
        verdict=verdict, confidence=0.9, reasoning="reasons", unknowns=[],
        model="claude-opus-5", input_tokens=1, output_tokens=1, cost_cents=0.1,
    )
    store.set_stage(listing_id, "matched" if verdict == "match" else "extracted")


def test_watchlist_resolves_from_a_model_name():
    """A row labelled with a model must find its watchlist's criteria."""
    watchlist = _watchlist_by_name(watchlist_config(), "bobcat-t770")
    assert watchlist is not None
    assert watchlist.name == "track-loaders"
    assert watchlist.criteria == "Under 3000 engine hours."


def test_unknown_model_resolves_to_no_watchlist():
    assert _watchlist_by_name(watchlist_config(), "newholland-c") is None


def test_grapple_model_resolves_to_the_attachments_watchlist():
    watchlist = _watchlist_by_name(watchlist_config(), "root-grapple")
    assert watchlist.name == "attachments"


def test_watchlist_lookup_of_a_none_model_name_returns_none_not_an_error():
    """A listing baselined by WatchSyncer._baseline with no matching model in
    any watchlist is stored with model_name=None — a reachable state, not
    just a theoretical one. The lookup must not raise for it."""
    assert _watchlist_by_name(watchlist_config(), None) is None


def test_parse_since_days():
    delta = datetime.now(timezone.utc) - parse_since("7d")
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)


def test_parse_since_hours_and_minutes():
    assert (datetime.now(timezone.utc) - parse_since("36h")) > timedelta(hours=35)
    assert (datetime.now(timezone.utc) - parse_since("30m")) > timedelta(minutes=29)


def test_parse_since_rejects_nonsense():
    with pytest.raises(ValueError, match="7 days"):
        parse_since("last tuesday")


def test_latest_run_id_is_none_on_an_empty_database(store: Store):
    assert store.latest_run_id() is None


def test_latest_run_id_returns_the_newest(store: Store):
    store.start_run()
    second = store.start_run()
    assert store.latest_run_id() == second


def test_collect_run_cards_splits_matches_and_unverified(store: Store):
    run_id = store.start_run()
    seed_extracted(store, "1", "match")
    seed_extracted(store, "2", "unverifiable")
    seed_extracted(store, "3", "no_match")
    store.finish_run(run_id, {})

    matches, unverified = collect_run_cards(store, config(), run_id)
    assert [c.listing.listing_id for c in matches] == ["1"]
    assert [c.listing.listing_id for c in unverified] == ["2"]


def test_collect_run_cards_respects_on_unknown_skip(store: Store):
    from tests.test_pipeline_scan import WATCHLIST_DICT

    cfg = watchlist_config(
        watchlists=[{**WATCHLIST_DICT["watchlists"][0], "on_unknown": "skip"}]
    )
    run_id = store.start_run()
    seed_extracted(store, "2", "unverifiable")
    store.finish_run(run_id, {})

    _matches, unverified = collect_run_cards(store, cfg, run_id)
    assert unverified == []


def test_collect_run_cards_handles_a_listing_with_no_model_name(store: Store):
    """WatchSyncer._baseline can store a matched/unverifiable listing with
    model_name=None when a saved listing matches no specific model. That must
    not crash card collection — it should behave like any other listing whose
    watchlist can't be found."""
    run_id = store.start_run()
    seed_extracted(store, "1", "unverifiable", watchlist_name=None, model_name=None)
    store.finish_run(run_id, {})

    matches, unverified = collect_run_cards(store, watchlist_config(), run_id)
    assert matches == []
    assert [c.listing.listing_id for c in unverified] == ["1"]


def test_collect_run_cards_ignores_other_runs(store: Store):
    first = store.start_run()
    seed_extracted(store, "1", "match")
    store.finish_run(first, {})

    second = store.start_run()
    store.finish_run(second, {})

    matches, _unverified = collect_run_cards(store, config(), second)
    assert matches == []


def test_replay_reextracts_stored_listings(store: Store):
    seed_extracted(store, "1", "unverifiable")
    extractor = FakeExtractor(extraction("match"))
    rows = replay(store, watchlist_config(), extractor, model_name="bobcat-t770", since="30d")
    assert len(rows) == 1
    assert rows[0].old_verdict == "unverifiable"
    assert rows[0].new_verdict == "match"


def test_replay_never_touches_the_network_or_facebook(store: Store):
    """The whole point: tuning carries zero detection risk."""
    seed_extracted(store, "1")
    extractor = FakeExtractor()
    replay(store, watchlist_config(), extractor, model_name="bobcat-t770", since="30d")
    assert extractor.calls == 1  # one Claude call, no source involved


def test_replay_does_not_write_by_default(store: Store):
    seed_extracted(store, "1", "unverifiable")
    replay(store, watchlist_config(), FakeExtractor(extraction("match")),
           model_name="bobcat-t770", since="30d")
    assert store.latest_extraction("1").verdict == "unverifiable"


def test_replay_writes_when_save_is_requested(store: Store):
    seed_extracted(store, "1", "unverifiable")
    replay(store, watchlist_config(), FakeExtractor(extraction("match")),
           model_name="bobcat-t770", since="30d", save=True)
    assert store.latest_extraction("1").verdict == "match"


def test_replay_filters_by_model_name(store: Store):
    seed_extracted(store, "1")
    store.upsert_listing(listing("2"), "some-other-search", "fp2")
    rows = replay(store, watchlist_config(), FakeExtractor(), model_name="bobcat-t770",
                  since="30d")
    assert [r.listing_id for r in rows] == ["1"]


def test_replay_skips_a_listing_with_no_model_name(store: Store, caplog):
    """Same reachable state as above: a stored listing with model_name=None
    must be skipped with the existing warning, not crash replay."""
    seed_extracted(store, "1", "unverifiable", watchlist_name=None, model_name=None)

    with caplog.at_level(logging.WARNING, logger="marketsearch.shakedown"):
        rows = replay(store, watchlist_config(), FakeExtractor(), model_name=None, since="30d")

    assert rows == []
    assert "skipping" in caplog.text


def test_replay_skips_listings_with_no_stored_detail(store: Store):
    store.upsert_listing(listing("3"), "bobcat-t770", "fp3")  # no detail saved
    rows = replay(store, watchlist_config(), FakeExtractor(), model_name="bobcat-t770",
                  since="30d")
    assert rows == []


def test_format_replay_shows_counts_and_changes():
    rows = [
        ReplayRow("1", "2019 T770", "unverifiable", "match", "2-speed now confirmed"),
        ReplayRow("2", "2016 T770", "match", "match", "unchanged"),
    ]
    text = format_replay(rows, "bobcat-t770")
    assert "bobcat-t770" in text
    assert "2 listings replayed" in text
    assert "CHANGED" in text
    assert "unverifiable → match" in text
    assert "2016 T770" not in text.split("CHANGED")[1]


def test_format_replay_says_so_when_nothing_moved():
    rows = [ReplayRow("1", "2019 T770", "match", "match", "unchanged")]
    assert "no verdicts changed" in format_replay(rows, "bobcat-t770").lower()
