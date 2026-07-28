"""Re-test stored rejections against the current catalog.

Editing config.yaml used to have no way of rescuing a listing rejected under
the old filters: rejected rows are skipped by the scanner's dedupe, and a
relist under a new id is caught as a repost. This closes that door — using the
database only, with no scraping, so it is safe to run as often as you like.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from marketsearch.config import Config
from marketsearch.models import RawListing
from marketsearch.prefilter import Assignment, assign
from marketsearch.store import ListingRow, Store

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequeueRow:
    listing_id: str
    title: str
    old_reason: str | None
    model_name: str
    watchlist_name: str


def _as_raw(listing: ListingRow) -> RawListing:
    return RawListing(
        listing_id=listing.listing_id, title=listing.title,
        price_cents=listing.price_cents, location=listing.location,
        url=listing.url, thumbnail_url=listing.thumbnail_url,
        seller_name=listing.seller_name,
    )


def requeue(store: Store, config: Config, dry_run: bool = False) -> list[RequeueRow]:
    """Reset every rejected listing the catalog now accepts back to 'pending'.

    Takes no listing source. Reclaiming during a normal run would require the
    listing to reappear in live search results, and search results are
    truncated — so this reads the ledger instead.
    """
    reclaimed: list[RequeueRow] = []

    for listing in store.prefiltered_listings():
        decision = assign(_as_raw(listing), config.watchlists)
        if not isinstance(decision, Assignment):
            continue

        reclaimed.append(
            RequeueRow(
                listing_id=listing.listing_id,
                title=listing.title,
                old_reason=listing.reject_reason,
                model_name=decision.model.name,
                watchlist_name=decision.watchlist.name,
            )
        )
        if not dry_run:
            store.reassign(
                listing.listing_id, decision.watchlist.name, decision.model.name
            )
            store.set_stage(listing.listing_id, "pending")

    log.info("requeued %d listing(s)", len(reclaimed))
    return reclaimed


def format_requeue(rows: list[RequeueRow], dry_run: bool) -> str:
    if not rows:
        return "Nothing to requeue — no rejected listing matches the current catalog."

    lines = [f"{len(rows)} listing(s) reclaimed:", ""]
    for row in rows:
        lines.append(f"  {row.model_name:<16} {row.title[:56]}")
        lines.append(f"  {'':<16} was: {row.old_reason}")
    if dry_run:
        lines.append("")
        lines.append("(Dry run — nothing written. Re-run without --dry-run to apply.)")
    return "\n".join(lines)
