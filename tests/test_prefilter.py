from __future__ import annotations

import pytest

from marketsearch.config import SearchConfig
from marketsearch.models import RawListing
from marketsearch.prefilter import prefilter


def search(**overrides) -> SearchConfig:
    base = dict(
        name="bobcat-t770",
        query="Bobcat T770",
        price_min_cents=1_500_000,
        price_max_cents=6_000_000,
        title_must_match=["t770"],
        title_must_not_match=["wanted", "parts only"],
        on_unknown="alert",
        criteria="Under 3000 hours.",
    )
    base.update(overrides)
    return SearchConfig(**base)


def listing(title: str = "2019 Bobcat T770", price_cents: int | None = 3_800_000) -> RawListing:
    return RawListing(
        listing_id="1", title=title, price_cents=price_cents, location=None,
        url="https://example.com/1", thumbnail_url=None, seller_name=None,
    )


def test_keeps_a_normal_match():
    result = prefilter(listing(), search())
    assert result.keep is True
    assert result.reason is None


def test_drops_title_missing_required_token():
    result = prefilter(listing(title="2019 Bobcat T650"), search())
    assert result.keep is False
    assert "t770" in result.reason


def test_title_matching_is_case_insensitive():
    assert prefilter(listing(title="BOBCAT T770 LOADER"), search()).keep is True


def test_drops_wanted_ads():
    result = prefilter(listing(title="WANTED: Bobcat T770"), search())
    assert result.keep is False
    assert "wanted" in result.reason


def test_drops_parts_listings():
    result = prefilter(listing(title="Bobcat T770 parts only"), search())
    assert result.keep is False
    assert "parts only" in result.reason


def test_exclusions_checked_before_inclusions():
    """A title matching both lists is rejected — exclusion wins."""
    result = prefilter(listing(title="Wanted Bobcat T770"), search())
    assert result.keep is False
    assert "wanted" in result.reason


def test_drops_price_above_maximum():
    result = prefilter(listing(price_cents=9_500_000), search())
    assert result.keep is False
    assert "above" in result.reason


def test_drops_price_below_minimum():
    result = prefilter(listing(price_cents=500_000), search())
    assert result.keep is False
    assert "below" in result.reason


def test_price_at_boundaries_is_kept():
    assert prefilter(listing(price_cents=1_500_000), search()).keep is True
    assert prefilter(listing(price_cents=6_000_000), search()).keep is True


def test_missing_price_is_kept_for_extraction_to_judge():
    """Marketplace occasionally omits price. Dropping those silently would
    discard exactly the listings worth a phone call."""
    result = prefilter(listing(price_cents=None), search())
    assert result.keep is True


def test_empty_matcher_lists_keep_everything():
    result = prefilter(
        listing(title="Some random skid steer"),
        search(title_must_match=[], title_must_not_match=[]),
    )
    assert result.keep is True


def test_all_required_tokens_must_be_present():
    result = prefilter(
        listing(title="Bobcat T770"),
        search(title_must_match=["t770", "cab"]),
    )
    assert result.keep is False
    assert "cab" in result.reason
