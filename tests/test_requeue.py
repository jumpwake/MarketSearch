from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.models import RawListing
from marketsearch.requeue import format_requeue, requeue
from marketsearch.store import Store

from tests.test_pipeline_scan import watchlist_config


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "q.db")
    s.initialize()
    yield s
    s.close()


def _stranded(store: Store) -> str:
    """A T770 rejected by the T86 search, exactly as v1 recorded it."""
    listing = RawListing(
        listing_id="1917658885567966", title="2019 Bobcat T770",
        price_cents=4_200_000, location="Cameron, Missouri",
        url="https://example.com/x", thumbnail_url=None, seller_name=None,
    )
    store.upsert_listing(listing, "bobcat-t86", "fp",
                         watchlist_name="track-loaders", model_name="bobcat-t86")
    store.set_stage(listing.listing_id, "prefiltered_out",
                    "title matched none of: 't86', 't-86', 't 86'")
    return listing.listing_id


def test_requeue_reclaims_a_listing_the_catalog_now_accepts(store: Store):
    listing_id = _stranded(store)

    rows = requeue(store, watchlist_config())
    row = store.get_listing(listing_id)

    assert [r.listing_id for r in rows] == [listing_id]
    assert rows[0].model_name == "bobcat-t770"
    assert row.stage == "pending"
    assert row.model_name == "bobcat-t770"
    assert row.watchlist_name == "track-loaders"


def test_requeue_leaves_genuinely_rejected_listings_alone(store: Store):
    junk = RawListing(
        listing_id="junk", title="2018 Bobcat T595", price_cents=3_000_000,
        location="Peoria, Illinois", url="https://example.com/j",
        thumbnail_url=None, seller_name=None,
    )
    store.upsert_listing(junk, "bobcat-t770", "fp2")
    store.set_stage("junk", "prefiltered_out", "matched no watched model")

    assert requeue(store, watchlist_config()) == []
    assert store.get_listing("junk").stage == "prefiltered_out"


def test_requeue_dry_run_writes_nothing(store: Store):
    listing_id = _stranded(store)

    rows = requeue(store, watchlist_config(), dry_run=True)

    assert len(rows) == 1
    assert store.get_listing(listing_id).stage == "prefiltered_out"


def test_requeue_never_touches_the_network():
    """requeue takes no source argument at all — this is a signature guarantee."""
    import inspect

    assert "source" not in inspect.signature(requeue).parameters


def test_format_requeue_reports_nothing_found():
    assert "nothing" in format_requeue([], dry_run=False).lower()
