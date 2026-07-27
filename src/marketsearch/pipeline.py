"""Orchestration: search, dedupe, prefilter, fetch, extract, record."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Callable

from marketsearch.config import Config, SearchConfig
from marketsearch.extract import ExtractionError, Extractor
from marketsearch.fingerprint import fingerprint
from marketsearch.models import ListingDetail, RawListing
from marketsearch.notify.render import MatchCard, download_photos
from marketsearch.prefilter import prefilter
from marketsearch.sources.base import ListingSource, ParseError, SourceError
from marketsearch.store import ExtractionRow, ListingRow, Store, utcnow

log = logging.getLogger(__name__)

RELIST_WINDOW_DAYS = 60
MAX_EXTRACTION_ATTEMPTS = 3


@dataclass
class ScanCounters:
    found: int = 0
    new: int = 0
    prefiltered: int = 0
    extracted: int = 0
    matched: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ScanOutcome:
    matches: list[MatchCard] = field(default_factory=list)
    unverified: list[MatchCard] = field(default_factory=list)
    counters: ScanCounters = field(default_factory=ScanCounters)


def content_hash(detail: ListingDetail) -> str:
    payload = json.dumps(
        {"d": detail.description, "p": detail.photo_urls}, sort_keys=True
    ).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def listing_row_from(
    listing: RawListing, search_name: str, fp: str, stage: str
) -> ListingRow:
    """Build a ListingRow without a database read.

    Used for card construction so that --dry-run, which writes nothing, still
    produces exactly the same output as a real run.
    """
    now = utcnow()
    return ListingRow(
        listing_id=listing.listing_id, search_name=search_name, title=listing.title,
        price_cents=listing.price_cents, location=listing.location, url=listing.url,
        thumbnail_url=listing.thumbnail_url, seller_name=listing.seller_name,
        fingerprint=fp, stage=stage, reject_reason=None, watched=False,
        first_seen_at=now, last_seen_at=now, last_change_check_at=None,
        extraction_attempts=0,
    )


class Scanner:
    def __init__(
        self,
        config: Config,
        store: Store,
        source: ListingSource,
        extractor: Extractor,
        photo_fetcher: Callable[..., list[bytes]] = download_photos,
        dry_run: bool = False,
    ) -> None:
        self._config = config
        self._store = store
        self._source = source
        self._extractor = extractor
        self._photo_fetcher = photo_fetcher
        self._dry_run = dry_run

    def scan(self) -> ScanOutcome:
        matches: list[MatchCard] = []
        unverified: list[MatchCard] = []
        counters = ScanCounters()
        budget = self._config.extraction.max_extractions_per_run

        for search in self._config.searches:
            listings = self._source.search(
                search.query, self._config.location.anchor,
                self._config.location.radius_miles,
            )
            counters.found += len(listings)

            known = self._store.known_listing_ids([l.listing_id for l in listings])
            fresh = [l for l in listings if l.listing_id not in known]
            counters.new += len(fresh)

            # Listings that failed extraction earlier are already in `listings`,
            # so dedupe would exclude them forever. Pull them back in explicitly.
            retries = [] if self._dry_run else self._store.pending_listings(search.name)
            log.info(
                "%s: %d listings, %d new, %d awaiting retry",
                search.name, len(listings), len(fresh), len(retries),
            )

            for listing in fresh + retries:
                budget -= self._process(
                    listing, search, matches, unverified, counters,
                    budget_remaining=budget,
                )

        return ScanOutcome(matches=matches, unverified=unverified, counters=counters)

    def _set_stage(self, listing_id: str, stage: str, reason: str | None = None) -> None:
        if not self._dry_run:
            self._store.set_stage(listing_id, stage, reason)

    def _record_failure(self, listing_id: str, counters: ScanCounters, why: str) -> None:
        """Leave a listing retryable, unless it has used up its attempts."""
        counters.errors += 1
        if self._dry_run:
            return
        attempts = self._store.bump_attempts(listing_id)
        if attempts >= MAX_EXTRACTION_ATTEMPTS:
            log.warning(
                "giving up on %s after %d attempts (%s)", listing_id, attempts, why
            )
            self._store.set_stage(
                listing_id, "failed", f"{why} after {attempts} attempts"
            )
        else:
            self._store.set_stage(listing_id, "pending")

    def _process(
        self,
        listing: RawListing,
        search: SearchConfig,
        matches: list[MatchCard],
        unverified: list[MatchCard],
        counters: ScanCounters,
        budget_remaining: int,
    ) -> int:
        """Handle one new listing. Returns the number of extractions consumed."""
        fp = fingerprint(
            listing.title, listing.price_cents, listing.seller_name, listing.location
        )

        if not self._dry_run:
            self._store.upsert_listing(listing, search.name, fp)

        decision = prefilter(listing, search)
        if not decision.keep:
            counters.prefiltered += 1
            self._set_stage(listing.listing_id, "prefiltered_out", decision.reason)
            return 0

        if not self._dry_run and self._store.fingerprint_seen_before(
            fp, listing.listing_id, RELIST_WINDOW_DAYS
        ):
            counters.prefiltered += 1
            self._set_stage(
                listing.listing_id, "prefiltered_out",
                f"repost of a listing seen within {RELIST_WINDOW_DAYS} days",
            )
            return 0

        if budget_remaining <= 0:
            log.info("extraction budget spent; %s stays pending", listing.listing_id)
            self._set_stage(listing.listing_id, "pending")
            return 0

        try:
            detail = self._source.fetch_detail(listing.listing_id)
        except (ParseError, SourceError) as exc:
            log.warning("detail fetch failed for %s: %s", listing.listing_id, exc)
            self._record_failure(listing.listing_id, counters, "detail fetch failed")
            return 0

        if not self._dry_run:
            self._store.save_detail(detail, content_hash(detail))

        try:
            result = self._extractor.extract(listing, detail, search.criteria)
        except ExtractionError as exc:
            log.warning("extraction failed for %s: %s", listing.listing_id, exc)
            self._record_failure(listing.listing_id, counters, "extraction failed")
            return 0

        counters.extracted += 1
        extraction = result.extraction

        if not self._dry_run:
            self._store.save_extraction(
                listing_id=listing.listing_id,
                attributes=extraction.model_dump(
                    include={"core", "specs", "condition", "deal"}
                ),
                verdict=extraction.verdict, confidence=extraction.confidence,
                reasoning=extraction.reasoning, unknowns=extraction.unknowns,
                model=self._config.extraction.model,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                cost_cents=result.cost_cents,
            )

        stage = "matched" if extraction.verdict == "match" else "extracted"
        self._set_stage(listing.listing_id, stage)

        row = ExtractionRow(
            listing_id=listing.listing_id,
            attributes=extraction.model_dump(include={"core", "specs", "condition", "deal"}),
            verdict=extraction.verdict, confidence=extraction.confidence,
            reasoning=extraction.reasoning, unknowns=extraction.unknowns,
            model=self._config.extraction.model, created_at=utcnow(),
        )

        if extraction.verdict == "match":
            counters.matched += 1
            matches.append(
                MatchCard(
                    listing=listing_row_from(listing, search.name, fp, stage),
                    extraction=row,
                    photos=self._photo_fetcher(detail.photo_urls),
                )
            )
        elif extraction.verdict == "unverifiable" and search.on_unknown == "alert":
            unverified.append(
                MatchCard(
                    listing=listing_row_from(listing, search.name, fp, stage),
                    extraction=row,
                    photos=self._photo_fetcher(detail.photo_urls),
                )
            )

        return 1
