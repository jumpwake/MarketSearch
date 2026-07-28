from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.models import ListingDetail, RawListing
from marketsearch.pipeline import WatchSyncer, content_hash
from marketsearch.sources.base import ListingUnavailable, ParseError
from marketsearch.store import Store

from tests.test_pipeline_scan import (
    WATCHLIST_DICT,
    FakeExtractor,
    config,
    extraction,
    listing,
    watchlist_config,
)


class FakeWatchSource:
    def __init__(self, saved=None, details=None, unavailable=None):
        self.saved = saved or []
        self.details = details or {}
        self.unavailable = set(unavailable or [])
        self.detail_calls: list[str] = []

    def search(self, query, location, radius_miles):
        return []

    def fetch_saved(self):
        return list(self.saved)

    def fetch_detail(self, listing_id):
        self.detail_calls.append(listing_id)
        if listing_id in self.unavailable:
            raise ListingUnavailable(f"{listing_id} is gone")
        return self.details.get(
            listing_id,
            ListingDetail(listing_id=listing_id, description="2400 hours",
                          structured_fields={}, photo_urls=[], distance_miles=None),
        )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "w.db")
    s.initialize()
    yield s
    s.close()


def syncer(store, source, extractor=None, cfg=None, **kwargs) -> WatchSyncer:
    return WatchSyncer(
        config=cfg or config(), store=store, source=source,
        extractor=extractor or FakeExtractor(), **kwargs,
    )


def seed(store: Store, listing_obj: RawListing, description="2400 hours") -> None:
    store.upsert_listing(listing_obj, "bobcat-t770", "fp")
    detail = ListingDetail(listing_id=listing_obj.listing_id, description=description,
                           structured_fields={}, photo_urls=[], distance_miles=None)
    store.save_detail(detail, content_hash(detail))


def test_saved_ids_are_mirrored_into_the_database(store: Store):
    seed(store, listing("1"))
    syncer(store, FakeWatchSource(saved=[listing("1")])).sync()
    assert store.watched_listing_ids() == {"1"}


def test_unsaving_on_facebook_clears_the_watch_flag(store: Store):
    seed(store, listing("1"))
    syncer(store, FakeWatchSource(saved=[listing("1")])).sync()
    syncer(store, FakeWatchSource(saved=[])).sync()
    assert store.watched_listing_ids() == set()


def test_a_never_seen_saved_listing_is_extracted_for_a_baseline(store: Store):
    """Baseline assignment goes through `assign`, which needs a watchlist
    catalog to judge against — a search-only config has none."""
    extractor = FakeExtractor()
    outcome = syncer(store, FakeWatchSource(saved=[listing("9")]), extractor,
                     cfg=watchlist_config()).sync()
    assert extractor.calls == 1
    assert store.get_listing("9") is not None
    assert store.latest_extraction("9") is not None
    assert outcome.changes == []  # first sight is not a change


def test_a_never_seen_listing_skips_baseline_without_a_watchlist_catalog(store: Store):
    """The live config.yaml has `searches:` and no `watchlists:` until Task 7
    migrates it. One saved listing with nothing to judge it against must not
    crash the whole sync — it should be skipped, not guessed at."""
    extractor = FakeExtractor()
    outcome = syncer(store, FakeWatchSource(saved=[listing("9")]), extractor).sync()
    assert outcome.changes == []
    assert outcome.errors == 0
    assert extractor.calls == 0
    assert store.get_listing("9") is None


def test_no_change_produces_no_card(store: Store):
    seed(store, listing("1"))
    outcome = syncer(store, FakeWatchSource(saved=[listing("1")])).sync()
    assert outcome.changes == []


def test_price_drop_produces_a_change_card(store: Store):
    seed(store, listing("1", price_cents=4_100_000))
    source = FakeWatchSource(saved=[listing("1", price_cents=3_800_000)])
    outcome = syncer(store, source).sync()
    assert len(outcome.changes) == 1
    change = outcome.changes[0]
    assert change.kind == "price_change"
    assert change.old_price_cents == 4_100_000
    assert change.new_price_cents == 3_800_000


def test_price_increase_also_produces_a_change_card(store: Store):
    seed(store, listing("1", price_cents=3_800_000))
    outcome = syncer(store, FakeWatchSource(saved=[listing("1", price_cents=4_000_000)])).sync()
    assert outcome.changes[0].new_price_cents == 4_000_000


def test_new_price_is_persisted(store: Store):
    seed(store, listing("1", price_cents=4_100_000))
    syncer(store, FakeWatchSource(saved=[listing("1", price_cents=3_800_000)])).sync()
    assert store.get_listing("1").price_cents == 3_800_000


def test_edited_description_produces_a_change_card(store: Store):
    seed(store, listing("1"), description="2400 hours")
    source = FakeWatchSource(
        saved=[listing("1")],
        details={"1": ListingDetail(listing_id="1", description="2400 hours. New tracks!",
                                    structured_fields={}, photo_urls=[], distance_miles=None)},
    )
    outcome = syncer(store, source).sync()
    assert len(outcome.changes) == 1
    assert outcome.changes[0].kind == "description_change"


def test_removed_listing_produces_a_removed_card(store: Store):
    seed(store, listing("1"))
    source = FakeWatchSource(saved=[listing("1")], unavailable={"1"})
    outcome = syncer(store, source).sync()
    assert len(outcome.changes) == 1
    assert outcome.changes[0].kind == "removed"
    assert outcome.changes[0].old_price_cents == 3_800_000


def test_a_parse_error_is_an_error_not_a_removal(store: Store):
    """Facebook changing its markup must never be reported as 'sold'."""
    class Broken(FakeWatchSource):
        def fetch_detail(self, listing_id):
            raise ParseError("markup changed")

    seed(store, listing("1"))
    outcome = syncer(store, Broken(saved=[listing("1")])).sync()
    assert outcome.changes == []
    assert outcome.errors == 1


def test_price_change_skips_the_detail_fetch_for_unchanged_text(store: Store):
    """Both a price change and a description edit are reported from one fetch."""
    seed(store, listing("1", price_cents=4_100_000))
    source = FakeWatchSource(saved=[listing("1", price_cents=3_800_000)])
    syncer(store, source).sync()
    assert source.detail_calls == ["1"]


def test_dry_run_writes_nothing(store: Store):
    seed(store, listing("1", price_cents=4_100_000))
    outcome = syncer(store, FakeWatchSource(saved=[listing("1", price_cents=3_800_000)]),
                     dry_run=True).sync()
    assert len(outcome.changes) == 1
    assert store.get_listing("1").price_cents == 4_100_000
    assert store.watched_listing_ids() == set()


def test_saved_listing_routes_to_the_watchlist_and_model_matching_its_title(store: Store):
    """A machine saved while browsing must be judged against the model whose
    keywords match it, not the first model in the catalog. Requiring every
    spelling variant on one model, rather than any of them, would leave a
    hyphenated listing unassigned."""
    t870 = {
        "name": "bobcat-t870", "keywords": ["t870", "t-870", "t 870"],
        "price_min_cents": 2_000_000, "price_max_cents": 5_500_000,
    }
    cfg = watchlist_config(watchlists=[{
        **WATCHLIST_DICT["watchlists"][0],
        "models": [WATCHLIST_DICT["watchlists"][0]["models"][0], t870],
    }])
    source = FakeWatchSource(saved=[listing("9", title="2020 Bobcat T-870 track loader")])

    WatchSyncer(config=cfg, store=store, source=source, extractor=FakeExtractor()).sync()

    row = store.get_listing("9")
    assert row.watchlist_name == "track-loaders"
    assert row.model_name == "bobcat-t870"
