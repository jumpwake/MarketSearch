"""Preview and replay — the tools that earn the tool its place in your inbox.

Nothing here touches Facebook. `preview` re-renders what a past run would have
sent; `replay` re-judges stored listings against edited criteria. Both work
entirely from the database, so tuning carries no detection risk.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from marketsearch.config import Config, WatchlistConfig
from marketsearch.extract import ExtractionError, Extractor
from marketsearch.models import RawListing
from marketsearch.notify.render import MatchCard
from marketsearch.store import ExtractionRow, ListingRow, Store

log = logging.getLogger(__name__)

_SINCE = re.compile(r"^(\d+)([dhm])$")
_UNITS = {"d": "days", "h": "hours", "m": "minutes"}


def parse_since(value: str) -> datetime:
    """Turn '7d' / '36h' / '30m' into a UTC cutoff."""
    match = _SINCE.match(value.strip().lower())
    if match is None:
        raise ValueError(
            f"could not understand {value!r} — use a form like 7d (7 days), "
            f"36h, or 30m"
        )
    amount, unit = int(match.group(1)), match.group(2)
    return datetime.now(timezone.utc) - timedelta(**{_UNITS[unit]: amount})


def _watchlist_by_name(config: Config, model_name: str | None) -> WatchlistConfig | None:
    """The watchlist that owns a model, which is where its criteria live."""
    if model_name is None:
        return None
    for watchlist in config.watchlists:
        for model in watchlist.models:
            if model.name == model_name:
                return watchlist
    return None


def collect_run_cards(
    store: Store, config: Config, run_id: int
) -> tuple[list[MatchCard], list[MatchCard]]:
    """Rebuild the cards a run produced, from what it wrote to the database."""
    row = store.get_run(run_id)
    if row is None:
        return [], []

    end = row["ended_at"] or datetime.now(timezone.utc).isoformat()
    matches: list[MatchCard] = []
    unverified: list[MatchCard] = []

    for listing, extraction in store.extractions_between(row["started_at"], end):
        card = MatchCard(listing=listing, extraction=extraction, photos=[])
        if extraction.verdict == "match":
            matches.append(card)
        elif extraction.verdict == "unverifiable":
            watchlist = _watchlist_by_name(config, listing.model_name)
            if watchlist is None or watchlist.on_unknown == "alert":
                unverified.append(card)

    return matches, unverified


@dataclass(frozen=True)
class ReplayRow:
    listing_id: str
    title: str
    old_verdict: str | None
    new_verdict: str
    reasoning: str


def _as_raw(listing: ListingRow) -> RawListing:
    return RawListing(
        listing_id=listing.listing_id, title=listing.title,
        price_cents=listing.price_cents, location=listing.location,
        url=listing.url, thumbnail_url=listing.thumbnail_url,
        seller_name=listing.seller_name,
    )


def replay(
    store: Store,
    config: Config,
    extractor: Extractor,
    model_name: str | None,
    since: str,
    save: bool = False,
) -> list[ReplayRow]:
    """Re-judge stored listings with the criteria currently in config.yaml."""
    cutoff = parse_since(since).isoformat()
    corpus = store.listings_with_details(model_name, cutoff)
    rows: list[ReplayRow] = []

    for listing, detail, previous in corpus:
        watchlist = _watchlist_by_name(config, listing.model_name)
        if watchlist is None:
            log.warning(
                "listing %s is labelled model %r which is no longer in config; skipping",
                listing.listing_id, listing.model_name,
            )
            continue

        try:
            result = extractor.extract(_as_raw(listing), detail, watchlist.criteria)
        except ExtractionError as exc:
            log.warning("replay failed for %s: %s", listing.listing_id, exc)
            continue

        extraction = result.extraction
        rows.append(
            ReplayRow(
                listing_id=listing.listing_id,
                title=listing.title,
                old_verdict=previous.verdict if previous else None,
                new_verdict=extraction.verdict,
                reasoning=extraction.reasoning,
            )
        )

        if save:
            store.save_extraction(
                listing_id=listing.listing_id,
                attributes=extraction.model_dump(
                    include={"core", "specs", "condition", "deal"}
                ),
                verdict=extraction.verdict, confidence=extraction.confidence,
                reasoning=extraction.reasoning, unknowns=extraction.unknowns,
                model=config.extraction.model,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                cost_cents=result.cost_cents,
            )

    return rows


def format_replay(rows: list[ReplayRow], search_name: str) -> str:
    if not rows:
        return f"{search_name} — nothing to replay in that window."

    def count(verdict: str, key) -> int:
        return sum(1 for r in rows if key(r) == verdict)

    lines = [f"{search_name} — {len(rows)} listings replayed", ""]
    for verdict in ("match", "unverifiable", "no_match"):
        new = count(verdict, lambda r: r.new_verdict)
        old = count(verdict, lambda r: r.old_verdict)
        delta = new - old
        arrow = f"{delta:+d}" if delta else "  "
        lines.append(f"  → {verdict:<14}{new:>3}  (was {old})  {arrow}")

    changed = [r for r in rows if r.old_verdict != r.new_verdict]
    if not changed:
        lines += ["", "  No verdicts changed."]
        return "\n".join(lines)

    lines += ["", "  CHANGED:"]
    for row in changed:
        old = row.old_verdict or "none"
        lines.append(
            f"  {row.listing_id:<12} {row.title[:38]:<40} "
            f"{old} → {row.new_verdict}"
        )
        lines.append(f"  {'':<12} {row.reasoning[:76]}")
    return "\n".join(lines)
