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


def test_build_search_url_sorts_newest_first():
    assert "sortBy=creation_time_descend" in build_search_url("x", radius_miles=100)


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


class FakePage:
    """Enough of a Playwright page to drive the scroll loop."""

    def __init__(self, snapshots: list[str]) -> None:
        self._snapshots = snapshots
        self.scrolls = 0
        self.waits = 0

    def content(self) -> str:
        return self._snapshots[min(self.scrolls, len(self._snapshots) - 1)]

    @property
    def mouse(self):
        return self

    def wheel(self, dx: int, dy: int) -> None:
        self.scrolls += 1

    def wait_for_timeout(self, ms: int) -> None:
        self.waits += 1


def cards(*ids: str) -> str:
    return " ".join(f"card-{i}" for i in ids)


def count_cards(html: str) -> int:
    return len(html.split())


def test_scroll_collects_pages_until_the_count_stops_growing():
    page = FakePage([cards("1", "2"), cards("1", "2", "3"), cards("1", "2", "3")])
    extra = scroll_for_more(page, initial_count=2, target=100, count=count_cards)
    assert page.scrolls == 2  # one that grew, one that did not
    assert extra == [cards("1", "2", "3"), cards("1", "2", "3")]


def test_scroll_stops_once_the_target_is_reached():
    page = FakePage([cards("1"), cards("1", "2", "3")])
    scroll_for_more(page, initial_count=1, target=3, count=count_cards)
    assert page.scrolls == 1


def test_scroll_does_not_touch_the_page_when_already_at_target():
    page = FakePage([cards("1", "2")])
    assert scroll_for_more(page, initial_count=2, target=2, count=count_cards) == []
    assert page.scrolls == 0


def test_scroll_is_bounded_even_when_the_page_keeps_growing():
    page = FakePage([cards(*(str(i) for i in range(n))) for n in range(1, 60)])
    scroll_for_more(page, initial_count=1, target=1000, count=count_cards, max_scrolls=4)
    assert page.scrolls == 4


def results_page(*ids: str) -> str:
    nodes = ",".join(
        f'{{"id":"{i}","marketplace_listing_title":"Bobcat T770 #{i}"}}' for i in ids
    )
    return f'<script type="application/json">{{"edges":[{nodes}]}}</script>'


def test_search_merges_listings_found_across_scrolls(tmp_path: Path):
    """The first screenful is 24 cards; the rest arrive only after scrolling."""
    src = source_returning(results_page("1", "2"), tmp_path)
    src._page = FakePage([results_page("1", "2", "3"), results_page("1", "2", "3")])

    found = src.search("Bobcat T770", "Springfield, IL", 250)

    assert [l.listing_id for l in found] == ["1", "2", "3"]


def test_search_without_a_browser_page_returns_the_first_page(tmp_path: Path):
    src = source_returning(results_page("1", "2"), tmp_path)
    assert [l.listing_id for l in src.search("q", "loc", 250)] == ["1", "2"]
