"""Free, deterministic gates applied before any detail page is fetched.

Ordering matters: exclusions are checked first because they are the most
decisive signal (a "wanted" ad is never interesting regardless of price), then
required tokens, then price.
"""

from __future__ import annotations

from dataclasses import dataclass

from marketsearch.config import SearchConfig
from marketsearch.models import RawListing


@dataclass(frozen=True)
class PrefilterResult:
    keep: bool
    reason: str | None


_KEEP = PrefilterResult(keep=True, reason=None)


def _drop(reason: str) -> PrefilterResult:
    return PrefilterResult(keep=False, reason=reason)


def prefilter(listing: RawListing, search: SearchConfig) -> PrefilterResult:
    title = listing.title.lower()

    for token in search.title_must_not_match:
        if token in title:
            return _drop(f"title contains excluded term '{token}'")

    for token in search.title_must_match:
        if token not in title:
            return _drop(f"title missing required term '{token}'")

    # A missing price is not a rejection. Marketplace sometimes omits it, and
    # those listings are disproportionately worth a look.
    if listing.price_cents is not None:
        if listing.price_cents < search.price_min_cents:
            return _drop(
                f"price ${listing.price_cents / 100:,.0f} below minimum "
                f"${search.price_min_cents / 100:,.0f}"
            )
        if listing.price_cents > search.price_max_cents:
            return _drop(
                f"price ${listing.price_cents / 100:,.0f} above maximum "
                f"${search.price_max_cents / 100:,.0f}"
            )

    return _KEEP
