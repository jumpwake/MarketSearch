"""Free, deterministic gates applied before any detail page is fetched.

A listing is offered to each watchlist in turn. Within a watchlist, ordering
matters: exclusions are checked first because they are the most decisive signal
(a "wanted" ad is never interesting regardless of price), then model
identification, then that model's price band.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from marketsearch.config import ModelConfig, WatchlistConfig
from marketsearch.models import RawListing


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


NO_MODEL = "matched no watched model"


@dataclass(frozen=True)
class Assignment:
    """A watchlist accepted this listing, as this model."""

    watchlist: WatchlistConfig
    model: ModelConfig


@dataclass(frozen=True)
class Rejection:
    reason: str


def identify_model(title: str, watchlist: WatchlistConfig) -> ModelConfig | None:
    """First model whose keywords appear in the title.

    Substring, not whole-word: sellers write '299d3xe' and '299d' has to keep
    matching it.
    """
    for model in watchlist.models:
        if any(keyword in title for keyword in model.keywords):
            return model
    return None


def offer(listing: RawListing, watchlist: WatchlistConfig) -> Assignment | Rejection:
    """Ask one watchlist whether it will take this listing.

    Identification precedes the price check because the band belongs to the
    model, not the watchlist — which is also why the catalog cannot be
    flattened into one keyword list.
    """
    title = listing.title.lower()

    for token in watchlist.exclude:
        if _contains_word(title, token):
            return Rejection(f"title contains excluded term '{token}'")

    model = identify_model(title, watchlist)
    if model is None:
        return Rejection(NO_MODEL)

    # A missing price is not a rejection. Marketplace sometimes omits it, and
    # those listings are disproportionately worth a look.
    if listing.price_cents is not None:
        if listing.price_cents < model.price_min_cents:
            return Rejection(
                f"price ${listing.price_cents / 100:,.0f} below {model.name} "
                f"minimum ${model.price_min_cents / 100:,.0f}"
            )
        if listing.price_cents > model.price_max_cents:
            return Rejection(
                f"price ${listing.price_cents / 100:,.0f} above {model.name} "
                f"maximum ${model.price_max_cents / 100:,.0f}"
            )

    return Assignment(watchlist=watchlist, model=model)


def assign(
    listing: RawListing, watchlists: list[WatchlistConfig]
) -> Assignment | Rejection:
    """Offer the listing to each watchlist in order; first acceptance wins.

    A listing is rejected only when every watchlist declines it. That is what
    stops one catalog's filter from ending the life of another catalog's
    listing — the failure that lost a $42,000 T770 to the T86 search.

    The reported reason prefers a specific decline (exclusion, price) over the
    generic one, so 'no watched model' never masks a near miss.
    """
    specific: Rejection | None = None
    for watchlist in watchlists:
        result = offer(listing, watchlist)
        if isinstance(result, Assignment):
            return result
        if specific is None and result.reason != NO_MODEL:
            specific = result
    return specific or Rejection(NO_MODEL)
