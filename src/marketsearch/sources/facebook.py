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


def build_search_url(query: str, radius_miles: int) -> str:
    params = {
        "query": query,
        "radiusKM": int(round(radius_miles * 1.60934)),
        "sortBy": "creation_time_descend",
    }
    return f"{SEARCH_BASE}?{urlencode(params)}"


def build_item_url(listing_id: str) -> str:
    return f"{ITEM_BASE}/{listing_id}/"


class FacebookSource:
    def __init__(
        self,
        profile_dir: Path,
        headless: bool = True,
        debug_dir: Path | None = None,
        fetch_html: Callable[[str], str] | None = None,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self._fetch_html_override = fetch_html
        self._playwright = None
        self._context = None
        self._page = None
        self._loaded_any_page = False

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
        url = build_search_url(query, radius_miles)
        log.info("searching %r (location %s, %d mi)", query, location, radius_miles)
        html = self._load(url, "search")
        try:
            return parse_search_results(html)
        except ParseError:
            self._save_debug("search", html)
            raise

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
