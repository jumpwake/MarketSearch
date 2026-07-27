from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.config import Config
from marketsearch.extract import ExtractionError, ExtractionResult
from marketsearch.extraction_models import Extraction
from marketsearch.models import ListingDetail, RawListing
from marketsearch.pipeline import Scanner
from marketsearch.sources.base import LoginRequired, ParseError
from marketsearch.store import Store

CONFIG_DICT = {
    "account": {"profile_dir": "profile"},
    "location": {"anchor": "Olathe, KS", "radius_miles": 250},
    "extraction": {"model": "claude-opus-5", "effort": "low", "max_extractions_per_run": 25},
    "notifications": {
        "email": {"to": "a@b.c", "from": "d@e.f", "smtp_host": "h", "smtp_port": 587,
                  "username": "u", "password_env": "P"},
        "sms": {"to": "+1", "twilio_from": "+2", "account_sid_env": "S",
                "auth_token_env": "T"},
    },
    "searches": [{
        "name": "bobcat-t770", "query": "Bobcat T770",
        "price_min_cents": 1_500_000, "price_max_cents": 6_000_000,
        "title_must_match": ["t770"], "title_must_not_match": ["wanted"],
        "on_unknown": "alert", "criteria": "Under 3000 engine hours.",
    }],
}


def config(**overrides) -> Config:
    data = {**CONFIG_DICT, **overrides}
    return Config.model_validate(data)


def listing(listing_id: str, title="2019 Bobcat T770", price_cents=3_800_000) -> RawListing:
    return RawListing(
        listing_id=listing_id, title=title, price_cents=price_cents,
        location="Olathe, KS", url=f"https://example.com/{listing_id}",
        thumbnail_url=None, seller_name="Dale S",
    )


def extraction(verdict="match", unknowns=None) -> Extraction:
    return Extraction.model_validate({
        "core": {"year": 2019, "make_model": "Bobcat T770", "engine_hours": 2400,
                 "asking_price": 38000, "location": "Olathe, KS"},
        "specs": {"cab_enclosed": True, "has_ac": True, "two_speed": True,
                  "high_flow": False, "tracks_or_tires": "tracks",
                  "undercarriage_condition": "good", "aux_hydraulics": True},
        "condition": {"runs": True, "stated_issues": [], "recent_service": [],
                      "damage_notes": None, "one_owner_claim": False},
        "deal": {"attachments": [], "seller_type": "private",
                 "financing_or_trade": False, "price_vs_market_note": None},
        "verdict": verdict, "confidence": 0.9, "reasoning": "reasons",
        "unknowns": unknowns or [],
    })


class FakeSource:
    def __init__(self, results=None, detail_error=None):
        self.results = results or []
        self.detail_error = detail_error
        self.detail_calls: list[str] = []

    def search(self, query, location, radius_miles):
        return list(self.results)

    def fetch_detail(self, listing_id):
        self.detail_calls.append(listing_id)
        if self.detail_error is not None:
            raise self.detail_error
        return ListingDetail(
            listing_id=listing_id, description="2400 hours",
            structured_fields={}, photo_urls=["https://example.com/p.jpg"],
            distance_miles=None,
        )

    def fetch_saved(self):
        return []


class FakeExtractor:
    def __init__(self, result=None, error=None):
        self._result = result or extraction()
        self._error = error
        self.calls = 0

    def extract(self, listing, detail, criteria):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return ExtractionResult(
            extraction=self._result, input_tokens=1500,
            output_tokens=400, cost_cents=1.75,
        )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "p.db")
    s.initialize()
    yield s
    s.close()


def scanner(store, source, extractor=None, cfg=None, **kwargs) -> Scanner:
    return Scanner(
        config=cfg or config(), store=store, source=source,
        extractor=extractor or FakeExtractor(),
        photo_fetcher=lambda urls, limit=3: [b"img"],
        **kwargs,
    )


def test_a_matching_listing_produces_a_match_card(store: Store):
    outcome = scanner(store, FakeSource([listing("1")])).scan()
    assert len(outcome.matches) == 1
    assert outcome.matches[0].listing.listing_id == "1"
    assert outcome.counters.matched == 1


def test_match_card_carries_photos(store: Store):
    outcome = scanner(store, FakeSource([listing("1")])).scan()
    assert outcome.matches[0].photos == [b"img"]


def test_already_seen_listings_are_skipped_entirely(store: Store):
    source = FakeSource([listing("1")])
    extractor = FakeExtractor()
    scanner(store, source, extractor).scan()
    scanner(store, FakeSource([listing("1")]), extractor).scan()
    assert extractor.calls == 1


def test_prefiltered_listing_never_loads_a_detail_page(store: Store):
    source = FakeSource([listing("1", title="WANTED Bobcat T770")])
    outcome = scanner(store, source).scan()
    assert source.detail_calls == []
    assert outcome.counters.prefiltered == 1
    assert outcome.matches == []


def test_prefilter_reason_is_recorded(store: Store):
    scanner(store, FakeSource([listing("1", price_cents=9_000_000)])).scan()
    row = store.get_listing("1")
    assert row.stage == "prefiltered_out"
    assert "above" in row.reject_reason


def test_repost_within_the_window_is_suppressed(store: Store):
    scanner(store, FakeSource([listing("1")])).scan()
    reposted = listing("2")  # same title, price, seller, location -> same fingerprint
    outcome = scanner(store, FakeSource([reposted])).scan()
    assert outcome.matches == []
    assert "repost" in store.get_listing("2").reject_reason


def test_repost_at_a_lower_price_still_alerts(store: Store):
    scanner(store, FakeSource([listing("1", price_cents=4_100_000)])).scan()
    outcome = scanner(store, FakeSource([listing("2", price_cents=3_800_000)])).scan()
    assert len(outcome.matches) == 1


def test_unverifiable_verdict_goes_to_the_unverified_bucket(store: Store):
    extractor = FakeExtractor(extraction("unverifiable", ["engine_hours"]))
    outcome = scanner(store, FakeSource([listing("1")]), extractor).scan()
    assert outcome.matches == []
    assert len(outcome.unverified) == 1


def test_on_unknown_skip_drops_unverifiable_listings(store: Store):
    cfg = config(searches=[{**CONFIG_DICT["searches"][0], "on_unknown": "skip"}])
    extractor = FakeExtractor(extraction("unverifiable", ["engine_hours"]))
    outcome = scanner(store, FakeSource([listing("1")]), extractor, cfg=cfg).scan()
    assert outcome.unverified == []


def test_no_match_produces_no_card_but_is_recorded(store: Store):
    extractor = FakeExtractor(extraction("no_match"))
    outcome = scanner(store, FakeSource([listing("1")]), extractor).scan()
    assert outcome.matches == []
    assert outcome.unverified == []
    assert store.latest_extraction("1").verdict == "no_match"
    assert store.get_listing("1").stage == "extracted"


def test_extraction_failure_leaves_the_listing_pending_for_retry(store: Store):
    extractor = FakeExtractor(error=ExtractionError("api down"))
    outcome = scanner(store, FakeSource([listing("1")]), extractor).scan()
    assert outcome.counters.errors == 1
    assert store.get_listing("1").stage == "pending"
    assert store.attempts("1") == 1


def test_failed_listings_are_retried_by_a_later_run(store: Store):
    failing = FakeExtractor(error=ExtractionError("api down"))
    scanner(store, FakeSource([listing("1")]), failing).scan()
    assert store.get_listing("1").stage == "pending"

    working = FakeExtractor()
    outcome = scanner(store, FakeSource([listing("1")]), working).scan()
    assert len(outcome.matches) == 1
    assert store.get_listing("1").stage == "matched"


def test_a_listing_that_exhausts_its_attempts_is_marked_failed(store: Store):
    failing = FakeExtractor(error=ExtractionError("api down"))
    for _ in range(3):
        scanner(store, FakeSource([listing("1")]), failing).scan()
    assert store.attempts("1") == 3
    assert store.get_listing("1").stage == "failed"


def test_a_failed_listing_is_not_retried_again(store: Store):
    failing = FakeExtractor(error=ExtractionError("api down"))
    for _ in range(3):
        scanner(store, FakeSource([listing("1")]), failing).scan()

    working = FakeExtractor()
    scanner(store, FakeSource([listing("1")]), working).scan()
    assert working.calls == 0


def test_detail_parse_failure_leaves_the_listing_pending(store: Store):
    source = FakeSource([listing("1")], detail_error=ParseError("markup changed"))
    outcome = scanner(store, source).scan()
    assert outcome.counters.errors == 1
    assert store.get_listing("1").stage == "pending"


def test_login_required_propagates_rather_than_being_swallowed(store: Store):
    class Blocked(FakeSource):
        def search(self, query, location, radius_miles):
            raise LoginRequired("checkpoint")

    with pytest.raises(LoginRequired):
        scanner(store, Blocked()).scan()


def test_extraction_budget_is_respected(store: Store):
    cfg = config(extraction={"model": "claude-opus-5", "effort": "low",
                             "max_extractions_per_run": 2})
    source = FakeSource([listing(str(i)) for i in range(5)])
    extractor = FakeExtractor()
    # Distinct prices so each listing has a distinct fingerprint.
    source.results = [listing(str(i), price_cents=3_000_000 + i * 10_000) for i in range(5)]
    scanner(store, source, extractor, cfg=cfg).scan()
    assert extractor.calls == 2


def test_listings_beyond_the_budget_stay_pending_for_the_next_run(store: Store):
    cfg = config(extraction={"model": "claude-opus-5", "effort": "low",
                             "max_extractions_per_run": 1})
    source = FakeSource([listing(str(i), price_cents=3_000_000 + i * 10_000)
                         for i in range(3)])
    scanner(store, source, cfg=cfg).scan()
    pending = [store.get_listing(str(i)).stage for i in range(3)]
    assert pending.count("pending") == 2


def test_dry_run_writes_nothing(store: Store):
    outcome = scanner(store, FakeSource([listing("1")]), dry_run=True).scan()
    assert len(outcome.matches) == 1
    assert store.get_listing("1") is None


def test_counters_reflect_the_sweep(store: Store):
    source = FakeSource([
        listing("1", price_cents=3_800_000),
        listing("2", title="WANTED Bobcat T770", price_cents=3_900_000),
    ])
    outcome = scanner(store, source).scan()
    assert outcome.counters.found == 2
    assert outcome.counters.new == 2
    assert outcome.counters.prefiltered == 1
    assert outcome.counters.extracted == 1
    assert outcome.counters.matched == 1
