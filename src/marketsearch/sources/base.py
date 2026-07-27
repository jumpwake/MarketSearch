"""The boundary between MarketSearch and any particular marketplace.

Everything downstream of this file speaks only RawListing and ListingDetail.
Swapping Facebook for a paid scraping API means writing one new class here.
"""

from __future__ import annotations

from typing import Protocol

from marketsearch.models import ListingDetail, RawListing


class SourceError(Exception):
    """Any failure while talking to the listing source."""


class LoginRequired(SourceError):
    """The source demanded a login or presented a security checkpoint.

    Callers must stop immediately rather than retrying — retrying a checkpoint
    is how a soft flag becomes a hard one.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(f"facebook requires attention: {kind}")
        self.kind = kind


class ParseError(SourceError):
    """The page loaded but its structure was not recognised.

    Distinct from 'zero results', which is an ordinary empty list.
    """


class ListingSource(Protocol):
    def search(self, query: str, location: str, radius_miles: int) -> list[RawListing]: ...
    def fetch_detail(self, listing_id: str) -> ListingDetail: ...
    def fetch_saved(self) -> list[RawListing]: ...
