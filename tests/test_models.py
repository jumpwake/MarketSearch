from __future__ import annotations

import pytest
from pydantic import ValidationError

from marketsearch.models import ListingDetail, RawListing


def test_raw_listing_requires_id_title_url():
    listing = RawListing(
        listing_id="123",
        title="Bobcat T770",
        price_cents=3_800_000,
        location="Kansas City, MO",
        url="https://www.facebook.com/marketplace/item/123/",
        thumbnail_url=None,
        seller_name=None,
    )
    assert listing.listing_id == "123"
    assert listing.price_cents == 3_800_000


def test_raw_listing_is_frozen():
    listing = RawListing(
        listing_id="123",
        title="Bobcat T770",
        price_cents=None,
        location=None,
        url="https://example.com/1",
        thumbnail_url=None,
        seller_name=None,
    )
    with pytest.raises(ValidationError):
        listing.title = "changed"


def test_listing_detail_defaults_photo_list_empty():
    detail = ListingDetail(
        listing_id="123",
        description="Runs great, 2400 hours",
        structured_fields={},
        photo_urls=[],
        distance_miles=None,
    )
    assert detail.photo_urls == []
    assert detail.description.startswith("Runs")
