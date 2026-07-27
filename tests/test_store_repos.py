from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.models import ListingDetail, RawListing
from marketsearch.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.initialize()
    s.upsert_listing(
        RawListing(
            listing_id="1", title="2019 Bobcat T770", price_cents=3_800_000,
            location="Olathe, KS", url="https://example.com/1",
            thumbnail_url=None, seller_name="Dale S",
        ),
        "bobcat-t770", "fp1",
    )
    yield s
    s.close()


def detail(listing_id: str = "1", description: str = "2400 hours, runs great") -> ListingDetail:
    return ListingDetail(
        listing_id=listing_id, description=description,
        structured_fields={"condition": "used"},
        photo_urls=["https://example.com/a.jpg"], distance_miles=42.0,
    )


def test_detail_roundtrip(store: Store):
    store.save_detail(detail(), "hash-a")
    got = store.get_detail("1")
    assert got.description == "2400 hours, runs great"
    assert got.structured_fields == {"condition": "used"}
    assert got.photo_urls == ["https://example.com/a.jpg"]
    assert got.distance_miles == 42.0


def test_get_detail_returns_none_when_absent(store: Store):
    assert store.get_detail("nope") is None


def test_save_detail_overwrites_and_updates_hash(store: Store):
    store.save_detail(detail(), "hash-a")
    store.save_detail(detail(description="Now 2500 hours"), "hash-b")
    assert store.get_detail("1").description == "Now 2500 hours"
    assert store.get_detail_content_hash("1") == "hash-b"


def test_extraction_roundtrip(store: Store):
    store.save_extraction(
        listing_id="1", attributes={"core": {"engine_hours": 2400}},
        verdict="match", confidence=0.9, reasoning="Under 3000 hours, 2-speed confirmed",
        unknowns=[], model="claude-opus-5", input_tokens=1500,
        output_tokens=400, cost_cents=1.75,
    )
    row = store.latest_extraction("1")
    assert row.verdict == "match"
    assert row.attributes["core"]["engine_hours"] == 2400
    assert row.unknowns == []


def test_latest_extraction_returns_most_recent(store: Store):
    for verdict in ("unverifiable", "match"):
        store.save_extraction(
            listing_id="1", attributes={}, verdict=verdict, confidence=0.5,
            reasoning="r", unknowns=[], model="claude-opus-5",
            input_tokens=1, output_tokens=1, cost_cents=0.1,
        )
    assert store.latest_extraction("1").verdict == "match"


def test_set_watched_ids_mirrors_facebook_exactly(store: Store):
    store.upsert_listing(
        RawListing(listing_id="2", title="T300", price_cents=None, location=None,
                   url="https://example.com/2", thumbnail_url=None, seller_name=None),
        "bobcat-t300", "fp2",
    )
    store.set_watched_ids({"1", "2"})
    assert store.watched_listing_ids() == {"1", "2"}
    store.set_watched_ids({"2"})  # user un-saved listing 1 on Facebook
    assert store.watched_listing_ids() == {"2"}


def test_notification_idempotency(store: Store):
    assert store.already_notified("1", "email", "match") is False
    store.record_notification("1", "email", "match", "sent")
    assert store.already_notified("1", "email", "match") is True
    assert store.already_notified("1", "sms", "match") is False
    assert store.already_notified("1", "email", "price_change") is False


def test_failed_notification_is_not_treated_as_sent(store: Store):
    store.record_notification("1", "email", "match", "failed")
    assert store.already_notified("1", "email", "match") is False


def test_run_lifecycle(store: Store):
    run_id = store.start_run()
    assert isinstance(run_id, int)
    store.finish_run(run_id, {"found": 10, "new": 3, "matched": 1, "errors": 0})
    cur = store._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    row = cur.fetchone()
    assert row["ended_at"] is not None
    assert row["matched"] == 1


def test_state_kv(store: Store):
    assert store.get_state("needs_login") is None
    store.set_state("needs_login", "true")
    assert store.get_state("needs_login") == "true"
    store.set_state("needs_login", "false")
    assert store.get_state("needs_login") == "false"
