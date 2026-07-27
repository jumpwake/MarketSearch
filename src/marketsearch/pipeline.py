"""Orchestration: search, dedupe, prefilter, fetch, extract, record."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from marketsearch.config import Config, SearchConfig
from marketsearch.extract import ExtractionError, Extractor
from marketsearch.fingerprint import fingerprint
from marketsearch.models import ListingDetail, RawListing
from marketsearch.notify.render import ChangeCard, MatchCard, download_photos
from marketsearch.prefilter import prefilter
from marketsearch.runstate import (
    OperationalAlerts,
    clear_needs_login,
    needs_login,
    set_needs_login,
)
from marketsearch.sources.base import (
    ListingSource,
    ListingUnavailable,
    LoginRequired,
    ParseError,
    SourceError,
)
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


@dataclass(frozen=True)
class WatchOutcome:
    changes: list[ChangeCard] = field(default_factory=list)
    errors: int = 0


class WatchSyncer:
    """Mirror Facebook's saved list and report what changed.

    Facebook's saved list is the source of truth; the database mirrors it. That
    is what makes un-saving the unfollow, with no separate state to reconcile.
    """

    def __init__(
        self,
        config: Config,
        store: Store,
        source: ListingSource,
        extractor: Extractor,
        dry_run: bool = False,
    ) -> None:
        self._config = config
        self._store = store
        self._source = source
        self._extractor = extractor
        self._dry_run = dry_run

    def _search_for(self, title: str) -> SearchConfig:
        """Pick the search whose title filters this listing satisfies.

        A machine saved while browsing may not belong to any configured search;
        the first search's criteria are a reasonable default and the alert
        still carries the full attribute table.
        """
        lowered = title.lower()
        for search in self._config.searches:
            if search.title_must_match and all(t in lowered for t in search.title_must_match):
                return search
        return self._config.searches[0]

    def sync(self) -> WatchOutcome:
        saved = self._source.fetch_saved()
        log.info("%d saved listing(s) on facebook", len(saved))

        changes: list[ChangeCard] = []
        errors = 0

        for listing in saved:
            try:
                change = self._check(listing)
            except (ParseError, SourceError) as exc:
                log.warning("watched check failed for %s: %s", listing.listing_id, exc)
                errors += 1
                continue
            if change is not None:
                changes.append(change)

        if not self._dry_run:
            self._store.set_watched_ids({l.listing_id for l in saved})

        return WatchOutcome(changes=changes, errors=errors)

    def _check(self, listing: RawListing) -> ChangeCard | None:
        known = self._store.get_listing(listing.listing_id)

        if known is None:
            self._baseline(listing)
            return None

        try:
            detail = self._source.fetch_detail(listing.listing_id)
        except ListingUnavailable:
            return ChangeCard(
                listing=known, kind="removed",
                old_price_cents=known.price_cents, new_price_cents=None,
            )

        if known.price_cents != listing.price_cents:
            if not self._dry_run:
                self._store.update_price(listing.listing_id, listing.price_cents)
                self._store.save_detail(detail, content_hash(detail))
            return ChangeCard(
                listing=known, kind="price_change",
                old_price_cents=known.price_cents, new_price_cents=listing.price_cents,
            )

        new_hash = content_hash(detail)
        if new_hash != self._store.get_detail_content_hash(listing.listing_id):
            if not self._dry_run:
                self._store.save_detail(detail, new_hash)
            return ChangeCard(
                listing=known, kind="description_change",
                old_price_cents=known.price_cents, new_price_cents=listing.price_cents,
            )

        return None

    def _baseline(self, listing: RawListing) -> None:
        """First sight of a listing saved while browsing. Establish a record so
        later runs have something to diff against."""
        search = self._search_for(listing.title)
        fp = fingerprint(
            listing.title, listing.price_cents, listing.seller_name, listing.location
        )
        if not self._dry_run:
            self._store.upsert_listing(listing, search.name, fp)

        detail = self._source.fetch_detail(listing.listing_id)
        if not self._dry_run:
            self._store.save_detail(detail, content_hash(detail))

        try:
            result = self._extractor.extract(listing, detail, search.criteria)
        except ExtractionError as exc:
            log.warning("baseline extraction failed for %s: %s", listing.listing_id, exc)
            return

        if not self._dry_run:
            extraction = result.extraction
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
            self._store.set_stage(listing.listing_id, "extracted")


@dataclass(frozen=True)
class RunReport:
    counters: ScanCounters = field(default_factory=ScanCounters)
    changes: int = 0
    watch_errors: int = 0
    notified: bool = False
    blocked: str | None = None


class _Dispatcher(Protocol):
    def dispatch(self, matches, unverified, changes) -> bool: ...


def run_once(
    config: Config,
    store: Store,
    source: ListingSource,
    extractor: Extractor,
    dispatcher: _Dispatcher,
    alerts: OperationalAlerts,
    notify_operational: Callable[[str, str], None],
    dry_run: bool = False,
) -> RunReport:
    """One complete sweep: scan, sync watched listings, alert.

    Never touches Facebook while the account is flagged as needing attention —
    retrying into a checkpoint is how a soft flag becomes a hard one.
    """
    blocked = needs_login(store)
    if blocked is not None:
        log.warning("account needs attention (%s); skipping run", blocked)
        return RunReport(blocked=blocked)

    run_id = None if dry_run else store.start_run()

    try:
        scan = Scanner(config, store, source, extractor, dry_run=dry_run).scan()
        watch = WatchSyncer(config, store, source, extractor, dry_run=dry_run).sync()
    except LoginRequired as exc:
        if not dry_run:
            set_needs_login(store, exc.kind)
        if alerts.should_send("needs_login"):
            notify_operational(
                "MarketSearch needs you to log in again",
                f"Facebook presented a {exc.kind}. Runs are paused until you run "
                f"`marketsearch login` on the MarketSearch machine and complete it "
                f"by hand.\n\nNo further scraping will be attempted until then.",
            )
            alerts.mark_sent("needs_login")
        if run_id is not None:
            store.finish_run(run_id, {"errors": 1})
        return RunReport(blocked=exc.kind)
    except ParseError as exc:
        if alerts.should_send("parse_failure"):
            notify_operational(
                "MarketSearch could not parse a Facebook page",
                f"{exc}\n\nThe offending page was saved to the debug folder. "
                f"Runs continue, but results may be incomplete until the parser "
                f"is updated.",
            )
            alerts.mark_sent("parse_failure")
        if run_id is not None:
            store.finish_run(run_id, {"errors": 1})
        return RunReport(counters=ScanCounters(errors=1))

    if alerts.clear("parse_failure"):
        notify_operational(
            "MarketSearch is back to normal",
            "Page parsing succeeded again. No action needed.",
        )
    if clear_needs_login(store) or alerts.clear("needs_login"):
        notify_operational(
            "MarketSearch is back to normal",
            "The Facebook session is working again. No action needed.",
        )

    notified = False
    if not dry_run:
        notified = dispatcher.dispatch(scan.matches, scan.unverified, watch.changes)

    counters = scan.counters
    counters.errors += watch.errors
    if run_id is not None:
        store.finish_run(run_id, counters.as_dict())

    log.info(
        "run complete: %d found, %d new, %d matched, %d change(s), %d error(s)",
        counters.found, counters.new, counters.matched, len(watch.changes), counters.errors,
    )
    return RunReport(
        counters=counters, changes=len(watch.changes),
        watch_errors=watch.errors, notified=notified,
    )
