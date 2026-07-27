from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketsearch.models import RawListing
from marketsearch.store import Store


def make_listing(listing_id: str = "1", price_cents: int | None = 3_800_000) -> RawListing:
    return RawListing(
        listing_id=listing_id,
        title="2019 Bobcat T770",
        price_cents=price_cents,
        location="Olathe, KS",
        url=f"https://www.facebook.com/marketplace/item/{listing_id}/",
        thumbnail_url="https://example.com/a.jpg",
        seller_name="Dale S",
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.initialize()
    yield s
    s.close()


def test_initialize_is_idempotent(tmp_path: Path):
    s = Store(tmp_path / "x.db")
    s.initialize()
    s.initialize()
    s.close()


def test_known_listing_ids_empty_on_fresh_db(store: Store):
    assert store.known_listing_ids(["1", "2"]) == set()


def test_upsert_then_known(store: Store):
    store.upsert_listing(make_listing("1"), "bobcat-t770", "fp1")
    assert store.known_listing_ids(["1", "2"]) == {"1"}


def test_known_listing_ids_handles_large_batches(store: Store):
    """Must not blow SQLite's 999-variable limit."""
    for i in range(1200):
        store.upsert_listing(make_listing(str(i)), "s", f"fp{i}")
    ids = [str(i) for i in range(1500)]
    assert len(store.known_listing_ids(ids)) == 1200


def test_get_listing_roundtrip(store: Store):
    store.upsert_listing(make_listing("1"), "bobcat-t770", "fp1")
    row = store.get_listing("1")
    assert row is not None
    assert row.title == "2019 Bobcat T770"
    assert row.price_cents == 3_800_000
    assert row.search_name == "bobcat-t770"
    assert row.fingerprint == "fp1"
    assert row.stage == "pending"
    assert row.watched is False


def test_get_listing_returns_none_when_absent(store: Store):
    assert store.get_listing("nope") is None


def test_upsert_preserves_first_seen_and_updates_last_seen(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp1")
    first = store.get_listing("1")
    store.upsert_listing(make_listing("1"), "s", "fp1")
    second = store.get_listing("1")
    assert second.first_seen_at == first.first_seen_at
    assert second.last_seen_at >= first.last_seen_at


def test_set_stage_records_reason(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp1")
    store.set_stage("1", "prefiltered_out", "price above maximum")
    row = store.get_listing("1")
    assert row.stage == "prefiltered_out"
    assert row.reject_reason == "price above maximum"


def test_update_price(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp1")
    store.update_price("1", 3_500_000)
    assert store.get_listing("1").price_cents == 3_500_000


def test_fingerprint_seen_before_ignores_the_listing_itself(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp-shared")
    assert store.fingerprint_seen_before("fp-shared", exclude_listing_id="1", within_days=60) is False


def test_fingerprint_seen_before_detects_a_repost(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp-shared")
    store.upsert_listing(make_listing("2"), "s", "fp-shared")
    assert store.fingerprint_seen_before("fp-shared", exclude_listing_id="2", within_days=60) is True


def test_fingerprint_outside_window_is_not_suppressed(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp-shared")
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    store._conn.execute("UPDATE listings SET first_seen_at = ? WHERE listing_id = '1'", (old,))
    store._conn.commit()
    store.upsert_listing(make_listing("2"), "s", "fp-shared")
    assert store.fingerprint_seen_before("fp-shared", exclude_listing_id="2", within_days=60) is False


def test_store_works_as_context_manager(tmp_path: Path):
    with Store(tmp_path / "cm.db") as s:
        s.initialize()
        s.upsert_listing(make_listing("1"), "s", "fp1")
        assert s.get_listing("1") is not None
