"""Free, deterministic gates applied before any detail page is fetched.

Ordering matters: exclusions are checked first because they are the most
decisive signal (a "wanted" ad is never interesting regardless of price), then
required tokens, then price.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from marketsearch.config import SearchConfig
from marketsearch.models import RawListing


@dataclass(frozen=True)
class PrefilterResult:
    keep: bool
    reason: str | None


_KEEP = PrefilterResult(keep=True, reason=None)


def _drop(reason: str) -> PrefilterResult:
    return PrefilterResult(keep=False, reason=reason)


@lru_cache(maxsize=512)
def _word(term: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(term)}\b")


def _contains_word(text: str, term: str) -> bool:
    """Whole-word containment, for exclusions only.

    'toy' must not exclude a Toyota, nor 'tire' an entire machine. Required
    terms deliberately stay substring matches: sellers write '299d3xe', and
    '299d' has to keep matching it.
    """
    return _word(term).search(text) is not None


def prefilter(listing: RawListing, search: SearchConfig) -> PrefilterResult:
    title = listing.title.lower()

    for token in search.title_must_not_match:
        if _contains_word(title, token):
            return _drop(f"title contains excluded term '{token}'")

    # The tokens are spelling variants of one model ("t770", "t-770", "t 770"),
    # so any one of them is enough. Requiring all of them can never be satisfied.
    if search.title_must_match and not any(t in title for t in search.title_must_match):
        return _drop(
            "title matched none of: "
            + ", ".join(repr(t) for t in search.title_must_match)
        )

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
