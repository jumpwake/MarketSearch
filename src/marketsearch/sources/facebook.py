"""Playwright driver for Facebook Marketplace.

The only module in the project that knows what a Facebook page looks like, and
therefore the only one that should need changing when Facebook does.

Facebook derives Marketplace location from the logged-in profile rather than a
URL parameter that can be reliably constructed. The user sets location and
radius by hand once during `marketsearch login`; the persistent Chrome profile
remembers it.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from marketsearch.models import ListingDetail, RawListing
from marketsearch.sources.base import (
    ListingUnavailable,
    LoginRequired,
    ParseError,
    SourceError,
)
from marketsearch.sources.parse import (
    detect_login_wall,
    detect_unavailable,
    parse_item_detail,
    parse_saved_listings,
    parse_graphql_payload,
    parse_search_results,
)

log = logging.getLogger(__name__)

SEARCH_BASE = "https://www.facebook.com/marketplace/search"
SAVED_URL = "https://www.facebook.com/marketplace/you/saved"
ITEM_BASE = "https://www.facebook.com/marketplace/item"

# Pause between page loads within a run, so a sweep does not read as a burst.
_MIN_PAGE_DELAY_S = 3.0
_MAX_PAGE_DELAY_S = 8.0

_PAGE_TIMEOUT_MS = 45_000

# Scrolling past the first screenful of search results.
_MAX_SCROLLS = 15
_SCROLL_PX = 4000
_SCROLL_POLL_MS = 400
# How long to keep polling for a scrolled batch before calling it exhausted.
_SCROLL_PATIENCE_MS = 5000
_DEFAULT_TARGET_LISTINGS = 100

# Scrolled results arrive here as text/html, so the URL is the only filter.
_GRAPHQL_PATH = "/api/graphql/"
_LISTING_MARKER = '"marketplace_listing_title"'


def build_search_url(
    query: str, radius_miles: int, newest_first: bool = False
) -> str:
    """Marketplace search URL. Relevance-ranked unless asked otherwise.

    Omitting `sortBy` leaves Facebook's own ranking in place, which is what a
    person gets searching by hand. Adding `sortBy=creation_time_descend` does
    not merely reorder those results — it takes Facebook's much looser match
    set and sorts the whole thing by date, so once the genuine matches run out
    the slots fill with whatever was posted nearby most recently.

    Measured on a live account, "Bobcat T770" at 100 miles:

        date-sorted   120 listings,   4 mentioning Bobcat,  0 catalog matches
        relevance     101 listings,  24 mentioning Bobcat,  6 catalog matches

    The date sort degrades as the radius tightens, because a smaller radius
    holds fewer real matches to occupy the early slots.
    """
    params = {"query": query, "radiusKM": int(round(radius_miles * 1.60934))}
    if newest_first:
        params["sortBy"] = "creation_time_descend"
    return f"{SEARCH_BASE}?{urlencode(params)}"


def build_item_url(listing_id: str) -> str:
    return f"{ITEM_BASE}/{listing_id}/"


def scroll_for_more(
    page,
    count: Callable[[], int],
    target: int,
    max_scrolls: int = _MAX_SCROLLS,
) -> int:
    """Scroll a loaded search page until no more listings arrive.

    Marketplace ships roughly 24 cards per screenful and fetches the rest over
    GraphQL as you scroll. Those results never reach the page's JSON script
    tags, so `count` must report what the response listener has collected, not
    what `page.content()` parses to.

    Returns the number of scrolls performed. Stops as soon as the count reaches
    `target` or a scroll adds nothing — an exhausted result set and a stalled
    load look the same from here, and both mean stop.
    """
    seen = count()
    for scrolls in range(max_scrolls):
        if seen >= target:
            return scrolls
        page.mouse.wheel(0, _SCROLL_PX)
        found = _wait_for_growth(page, count, seen)
        if found <= seen:
            return scrolls + 1
        seen = found
    return max_scrolls


def _wait_for_growth(page, count: Callable[[], int], seen: int) -> int:
    """Poll until the listing count grows, or patience runs out.

    A fixed settle time cannot tell a slow GraphQL response from an exhausted
    result set. Waiting for the count itself to move returns as soon as the
    batch lands and still gives up in bounded time when nothing is coming.
    """
    waited = 0
    while waited < _SCROLL_PATIENCE_MS:
        page.wait_for_timeout(_SCROLL_POLL_MS)
        waited += _SCROLL_POLL_MS
        found = count()
        if found > seen:
            return found
    return count()


class FacebookSource:
    def __init__(
        self,
        profile_dir: Path,
        headless: bool = True,
        debug_dir: Path | None = None,
        fetch_html: Callable[[str], str] | None = None,
        max_listings_per_search: int = _DEFAULT_TARGET_LISTINGS,
        newest_first: bool = False,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.max_listings_per_search = max_listings_per_search
        self.newest_first = newest_first
        self._fetch_html_override = fetch_html
        self._playwright = None
        self._context = None
        self._page = None
        self._loaded_any_page = False
        # Listings seen in GraphQL traffic since the current search began.
        self._scrolled: dict[str, RawListing] = {}

    # ---- lifecycle -------------------------------------------------------

    def __enter__(self) -> "FacebookSource":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel="chrome",
            headless=self.headless,
            viewport={"width": 1440, "height": 900},
        )
        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )
        self._attach_response_listener(self._page)

    def _attach_response_listener(self, page) -> None:
        page.on("response", self._collect_scrolled_listings)

    def _collect_scrolled_listings(self, response) -> None:
        """Harvest listings from Marketplace's GraphQL traffic.

        Results past the first screenful are fetched over GraphQL and rendered
        straight into the DOM — they never appear in the page's JSON script
        tags, so this listener is the only place they can be picked up. The
        responses come back as text/html, so the URL is the only reliable
        filter.
        """
        if _GRAPHQL_PATH not in response.url:
            return
        try:
            body = response.text()
        except Exception:  # body already consumed, redirect, aborted request
            return
        if _LISTING_MARKER not in body:
            return
        for listing in parse_graphql_payload(body):
            self._scrolled.setdefault(listing.listing_id, listing)

    # ---- page fetching ---------------------------------------------------

    def _fetch(self, url: str) -> str:
        if self._fetch_html_override is not None:
            return self._fetch_html_override(url)

        self._ensure_browser()
        if self._loaded_any_page:
            time.sleep(random.uniform(_MIN_PAGE_DELAY_S, _MAX_PAGE_DELAY_S))

        log.debug("loading %s", url)
        self._page.goto(url, timeout=_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        self._page.wait_for_timeout(2500)  # let the JSON payload land
        self._loaded_any_page = True
        return self._page.content()

    def _save_debug(self, label: str, html: str) -> None:
        if self.debug_dir is None:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = self.debug_dir / f"{stamp}-{label}.html"
        path.write_text(html, encoding="utf-8")
        log.warning("saved unparseable page to %s", path)

    def _load(self, url: str, label: str) -> str:
        """Fetch, then check for a login wall *before* attempting to parse.

        A login page contains no listing JSON, so parsing it first would raise
        a misleading ParseError and trigger the wrong recovery.
        """
        try:
            html = self._fetch(url)
        except LoginRequired:
            raise
        except Exception as exc:  # playwright timeouts, navigation failures
            raise SourceError(f"failed to load {url}: {exc}") from exc

        wall = detect_login_wall(html)
        if wall is not None:
            raise LoginRequired(wall)
        return html

    # ---- ListingSource ---------------------------------------------------

    def search(self, query: str, location: str, radius_miles: int) -> list[RawListing]:
        url = build_search_url(query, radius_miles, self.newest_first)
        log.info(
            "searching %r (location %s, %d mi, %s)",
            query, location, radius_miles,
            "newest first" if self.newest_first else "by relevance",
        )
        # Results are per-search; last query's traffic must not bleed into this one.
        self._scrolled.clear()
        html = self._load(url, "search")
        try:
            found = parse_search_results(html)
        except ParseError:
            self._save_debug("search", html)
            raise

        if self._page is None:
            return found

        merged = {l.listing_id: l for l in found}

        def absorb() -> int:
            for listing_id, listing in self._scrolled.items():
                merged.setdefault(listing_id, listing)
            return len(merged)

        scrolls = scroll_for_more(
            self._page, count=absorb, target=self.max_listings_per_search
        )
        absorb()

        log.info("%r: %d listing(s) after %d scroll(s)", query, len(merged), scrolls)
        return list(merged.values())

    def fetch_detail(self, listing_id: str) -> ListingDetail:
        html = self._load(build_item_url(listing_id), "item")
        if detect_unavailable(html):
            raise ListingUnavailable(f"listing {listing_id} is no longer available")
        try:
            return parse_item_detail(html, listing_id)
        except ParseError:
            self._save_debug(f"item-{listing_id}", html)
            raise

    def fetch_saved(self) -> list[RawListing]:
        html = self._load(SAVED_URL, "saved")
        try:
            return parse_saved_listings(html)
        except ParseError:
            self._save_debug("saved", html)
            raise


def open_login_browser(profile_dir: Path) -> None:
    """Open a visible Chrome on the persistent profile and block until closed.

    The user logs into Facebook, opens Marketplace, and sets their location and
    search radius by hand. All of it persists in the profile directory.
    """
    from playwright.sync_api import sync_playwright

    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.facebook.com/marketplace/", timeout=_PAGE_TIMEOUT_MS)
        print(
            "\nA browser window is open.\n"
            "  1. Log into Facebook if prompted.\n"
            "  2. Open Marketplace and set your location and search radius.\n"
            "  3. Close the browser window when done.\n"
        )
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        context.close()
