"""Source-agnostic listing vocabulary.

Everything downstream of `sources/` speaks only these two types. They contain
no Facebook-specific concepts, which is what allows the scraping layer to be
repaired or replaced without touching the rest of the system.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RawListing(BaseModel):
    """A listing as it appears in search results: cheap fields only."""

    model_config = ConfigDict(frozen=True)

    listing_id: str
    title: str
    price_cents: int | None
    location: str | None
    url: str
    thumbnail_url: str | None
    seller_name: str | None


class ListingDetail(BaseModel):
    """The contents of a listing's own page."""

    model_config = ConfigDict(frozen=True)

    listing_id: str
    description: str
    structured_fields: dict[str, object]
    photo_urls: list[str]
    distance_miles: float | None
