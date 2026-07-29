from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.sources.base import LoginRequired, ParseError
from marketsearch.sources.facebook import (
    FacebookSource,
    build_item_url,
    build_search_url,
    scroll_for_more,
)


def page(fixtures_dir: Path, name: str) -> str:
    return (fixtures_dir / "pages" / name).read_text(encoding="utf-8")


def source_returning(html: str, tmp_path: Path, **kwargs) -> FacebookSource:
    return FacebookSource(
        profile_dir=tmp_path / "profile",
        fetch_html=lambda url: html,
        debug_dir=tmp_path / "debug",
        **kwargs,
    )


def test_build_search_url_encodes_the_query():
    url = build_search_url("Bobcat T770", radius_miles=250)
    assert "query=Bobcat+T770" in url or "query=Bobcat%20T770" in url
    assert url.startswith("https://www.facebook.com/marketplace/search")


def test_build_search_url_defaults_to_relevance():
    """No sortBy means Facebook's own ranking.

    Measured on a live account at a 100-mile radius: the date sort returned
    120 listings of which 0 matched any watched model, while the same query
    without it returned 101 of which 6 did. Forcing a date order takes
    Facebook's loose match set and floats recent junk to the top.
    """
    url = build_search_url("x", radius_miles=100)
    assert "sortBy" not in url


def test_build_search_url_can_still_sort_newest_first():
    url = build_search_url("x", radius_miles=100, newest_first=True)
    assert "sortBy=creation_time_descend" in url


def test_build_search_url_converts_miles_to_kilometres():
    url = build_search_url("x", radius_miles=250)
    assert "radiusKM=402" in url  # 250 mi -> 402 km


def test_build_item_url():
    assert build_item_url("1001") == "https://www.facebook.com/marketplace/item/1001/"


def test_search_returns_parsed_listings(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "search.html"), tmp_path)
    listings = src.search("Bobcat T770", location="Olathe, KS", radius_miles=250)
    assert [l.listing_id for l in listings] == ["1001", "1002", "1003"]


def test_search_returns_empty_list_for_no_results(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "empty_results.html"), tmp_path)
    assert src.search("Bobcat T999", location="x", radius_miles=250) == []


def test_login_wall_raises_login_required(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "login_wall.html"), tmp_path)
    with pytest.raises(LoginRequired) as exc:
        src.search("x", location="y", radius_miles=1)
    assert exc.value.kind == "login"


def test_checkpoint_raises_login_required(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "checkpoint.html"), tmp_path)
    with pytest.raises(LoginRequired) as exc:
        src.search("x", location="y", radius_miles=1)
    assert exc.value.kind == "checkpoint"


def test_login_wall_is_detected_before_parsing(fixtures_dir: Path, tmp_path: Path):
    """A login page has no listing JSON. Without the check it would surface as
    a misleading ParseError and trigger the wrong recovery."""
    src = source_returning(page(fixtures_dir, "login_wall.html"), tmp_path)
    with pytest.raises(LoginRequired):
        src.fetch_detail("1001")


def test_parse_failure_writes_the_page_to_the_debug_dir(tmp_path: Path):
    src = source_returning("<html><body>totally unexpected</body></html>", tmp_path)
    with pytest.raises(ParseError):
        src.search("x", location="y", radius_miles=1)
    written = list((tmp_path / "debug").glob("*.html"))
    assert len(written) == 1
    assert "totally unexpected" in written[0].read_text(encoding="utf-8")


def test_fetch_detail_returns_parsed_detail(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "item.html"), tmp_path)
    detail = src.fetch_detail("1001")
    assert "2,400 hours" in detail.description
    assert len(detail.photo_urls) == 2


def test_fetch_saved_returns_full_listings(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "saved.html"), tmp_path)
    saved = src.fetch_saved()
    assert [l.listing_id for l in saved] == ["1001", "2002"]
    assert saved[0].title == "2019 Bobcat T770 Compact Track Loader"


def test_source_is_a_context_manager(fixtures_dir: Path, tmp_path: Path):
    with source_returning(page(fixtures_dir, "saved.html"), tmp_path) as src:
        assert [l.listing_id for l in src.fetch_saved()] == ["1001", "2002"]




GRAPHQL_URL = "https://www.facebook.com/api/graphql/"


class FakeResponse:
    def __init__(self, url: str, body: str) -> None:
        self.url = url
        self._body = body

    def text(self) -> str:
        return self._body


class FakeMouse:
    def __init__(self, page: "FakePage") -> None:
        self._page = page

    def wheel(self, dx: int, dy: int) -> None:
        self._page.scrolls += 1
        self._page.schedule_next_batch()


class FakePage:
    """Enough of a Playwright page to drive the scroll loop: each scroll
    delivers the next batch of GraphQL responses to the registered handler."""

    def __init__(self, html: str = "", batches: list[list[FakeResponse]] | None = None,
                 latency_waits: int = 1):
        self._html = html
        self._batches = list(batches or [])
        self._handlers: dict[str, list] = {}
        self._latency_waits = latency_waits
        self._deliver_at: int | None = None
        self.scrolls = 0
        self.waits = 0
        self.mouse = FakeMouse(self)

    def on(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def schedule_next_batch(self) -> None:
        """A scrolled response lands some number of polls after the scroll."""
        if self._batches:
            self._deliver_at = self.waits + self._latency_waits

    def _deliver_next_batch(self) -> None:
        self._deliver_at = None
        if not self._batches:
            return
        for response in self._batches.pop(0):
            for handler in self._handlers.get("response", []):
                handler(response)

    def content(self) -> str:
        return self._html

    def wait_for_timeout(self, ms: int) -> None:
        self.waits += 1
        if self._deliver_at is not None and self.waits >= self._deliver_at:
            self._deliver_next_batch()


def counter(*values: int):
    """A count() that yields each value in turn, then repeats the last."""
    seen = list(values)

    def count() -> int:
        return seen.pop(0) if len(seen) > 1 else seen[0]

    return count


def test_scroll_stops_once_a_scroll_adds_nothing():
    page = FakePage()
    scrolls = scroll_for_more(page, count=counter(24, 48, 48), target=100)
    assert scrolls == 2  # one that grew, one that did not
    assert page.scrolls == 2


def test_scroll_stops_once_the_target_is_reached():
    page = FakePage()
    scrolls = scroll_for_more(page, count=counter(24, 72, 120), target=100)
    assert scrolls == 2
    assert page.scrolls == 2


def test_scroll_does_not_touch_the_page_when_already_at_target():
    page = FakePage()
    assert scroll_for_more(page, count=counter(100), target=100) == 0
    assert page.scrolls == 0


def test_scroll_is_bounded_even_when_the_page_keeps_growing():
    page = FakePage()
    count = iter(range(1, 500))
    scroll_for_more(page, count=lambda: next(count), target=10_000, max_scrolls=4)
    assert page.scrolls == 4


def results_page(*ids: str) -> str:
    nodes = ",".join(
        f'{{"id":"{i}","marketplace_listing_title":"Bobcat T770 #{i}"}}' for i in ids
    )
    return f'<script type="application/json">{{"edges":[{nodes}]}}</script>'


def graphql_response(*ids: str) -> FakeResponse:
    nodes = ",".join(
        f'{{"node":{{"id":"{i}","marketplace_listing_title":"Bobcat T770 #{i}"}}}}'
        for i in ids
    )
    body = '{"data":{"marketplace_search":{"feed_units":{"edges":[' + nodes + "]}}}}"
    return FakeResponse(GRAPHQL_URL, body)


def test_search_merges_listings_that_arrive_over_graphql(tmp_path: Path):
    """The first screenful comes from the page's script tags; everything past
    it arrives only as GraphQL traffic while scrolling."""
    src = source_returning(results_page("1", "2"), tmp_path)
    src._page = FakePage(batches=[[graphql_response("3", "4")], [graphql_response("5")]])
    src._attach_response_listener(src._page)

    found = src.search("Bobcat T770", "Springfield, IL", 250)

    assert [l.listing_id for l in found] == ["1", "2", "3", "4", "5"]


def test_search_ignores_graphql_traffic_that_carries_no_listings(tmp_path: Path):
    src = source_returning(results_page("1"), tmp_path)
    src._page = FakePage(batches=[[FakeResponse(GRAPHQL_URL, '{"data":{"viewer":1}}')]])
    src._attach_response_listener(src._page)

    assert [l.listing_id for l in src.search("q", "loc", 250)] == ["1"]


def test_search_does_not_leak_listings_between_searches(tmp_path: Path):
    """Each search must report its own results, not the previous query's."""
    src = source_returning(results_page("1"), tmp_path)
    src._page = FakePage(batches=[[graphql_response("2")]])
    src._attach_response_listener(src._page)
    src.search("first query", "loc", 250)

    src._page = FakePage(batches=[[graphql_response("9")]])
    src._attach_response_listener(src._page)
    second = src.search("second query", "loc", 250)

    assert [l.listing_id for l in second] == ["1", "9"]


def test_scroll_waits_for_a_slow_graphql_response_before_giving_up(tmp_path: Path):
    """A response that lands three polls after the scroll is still a result.
    Treating one quiet poll as 'exhausted' is what capped every search at 48."""
    src = source_returning(results_page("1", "2"), tmp_path)
    src._page = FakePage(
        batches=[[graphql_response("3", "4")], [graphql_response("5", "6")]],
        latency_waits=3,
    )
    src._attach_response_listener(src._page)

    found = src.search("Bobcat T770", "Springfield, IL", 250)

    assert [l.listing_id for l in found] == ["1", "2", "3", "4", "5", "6"]


def test_scroll_gives_up_when_nothing_ever_arrives():
    """Patience must be bounded, or an exhausted result set hangs the sweep."""
    page = FakePage()
    scrolls = scroll_for_more(page, count=counter(24), target=100)
    assert scrolls == 1
    assert page.waits <= 20
