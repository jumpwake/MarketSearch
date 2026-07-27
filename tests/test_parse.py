from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.sources.base import ParseError
from marketsearch.sources.parse import (
    detect_login_wall,
    extract_json_blobs,
    iter_dicts,
    parse_item_detail,
    parse_saved_listings,
    parse_search_results,
)


def page(fixtures_dir: Path, name: str) -> str:
    return (fixtures_dir / "pages" / name).read_text(encoding="utf-8")


def test_extract_json_blobs_finds_all_script_payloads(fixtures_dir: Path):
    blobs = extract_json_blobs(page(fixtures_dir, "search.html"))
    assert len(blobs) == 2


def test_extract_json_blobs_skips_unparseable_scripts():
    html = '<script type="application/json">{"good":1}</script>' \
           '<script type="application/json">not json</script>'
    assert extract_json_blobs(html) == [{"good": 1}]


def test_iter_dicts_walks_lists_and_nested_dicts():
    tree = {"a": [{"b": 1}, {"c": {"d": 2}}]}
    found = list(iter_dicts(tree))
    assert {"b": 1} in found
    assert {"d": 2} in found


def test_parse_search_results_returns_all_listings(fixtures_dir: Path):
    listings = parse_search_results(page(fixtures_dir, "search.html"))
    assert [l.listing_id for l in listings] == ["1001", "1002", "1003"]


def test_parse_search_results_maps_fields(fixtures_dir: Path):
    listings = parse_search_results(page(fixtures_dir, "search.html"))
    first = listings[0]
    assert first.title == "2019 Bobcat T770 Compact Track Loader"
    assert first.price_cents == 3_800_000
    assert first.location == "Olathe, KS"
    assert first.seller_name == "Dale S"
    assert first.thumbnail_url == "https://scontent.example.com/1001.jpg"
    assert first.url == "https://www.facebook.com/marketplace/item/1001/"


def test_parse_search_results_tolerates_missing_optional_fields(fixtures_dir: Path):
    listings = parse_search_results(page(fixtures_dir, "search.html"))
    third = listings[2]
    assert third.price_cents is None
    assert third.thumbnail_url is None
    assert third.seller_name is None


def test_parse_search_results_deduplicates_repeated_nodes():
    """Facebook's payload often contains the same listing in several places."""
    html = (
        '<script type="application/json">'
        '{"a":{"id":"1","marketplace_listing_title":"T770","listing_price":{"amount":"1"}},'
        ' "b":{"id":"1","marketplace_listing_title":"T770","listing_price":{"amount":"1"}}}'
        "</script>"
    )
    assert len(parse_search_results(html)) == 1


def test_empty_results_page_returns_empty_list_not_an_error(fixtures_dir: Path):
    """Distinguishing 'zero results' from 'could not parse' is the whole point."""
    assert parse_search_results(page(fixtures_dir, "empty_results.html")) == []


def test_page_with_no_json_at_all_raises_parse_error():
    with pytest.raises(ParseError, match="no JSON payload"):
        parse_search_results("<html><body>Something went wrong</body></html>")


def test_parse_item_detail(fixtures_dir: Path):
    detail = parse_item_detail(page(fixtures_dir, "item.html"), "1001")
    assert "2,400 hours" in detail.description
    assert detail.photo_urls == [
        "https://scontent.example.com/1001-a.jpg",
        "https://scontent.example.com/1001-b.jpg",
    ]
    assert detail.structured_fields["Condition"] == "Used - good"
    assert detail.structured_fields["Category"] == "Heavy Equipment"


def test_parse_item_detail_raises_when_the_listing_node_is_absent(fixtures_dir: Path):
    with pytest.raises(ParseError, match="1001"):
        parse_item_detail(page(fixtures_dir, "empty_results.html"), "1001")


def test_parse_saved_listings_returns_full_listings(fixtures_dir: Path):
    saved = parse_saved_listings(page(fixtures_dir, "saved.html"))
    assert [l.listing_id for l in saved] == ["1001", "2002"]
    assert saved[0].title == "2019 Bobcat T770 Compact Track Loader"
    assert saved[1].price_cents == 2_450_000


def test_parse_saved_listings_returns_empty_for_an_empty_collection(fixtures_dir: Path):
    assert parse_saved_listings(page(fixtures_dir, "empty_results.html")) == []


def test_detect_login_wall(fixtures_dir: Path):
    assert detect_login_wall(page(fixtures_dir, "login_wall.html")) == "login"


def test_detect_checkpoint(fixtures_dir: Path):
    assert detect_login_wall(page(fixtures_dir, "checkpoint.html")) == "checkpoint"


def test_detect_login_wall_returns_none_for_a_normal_page(fixtures_dir: Path):
    assert detect_login_wall(page(fixtures_dir, "search.html")) is None


def test_facebooks_routing_table_is_not_mistaken_for_a_checkpoint():
    """Every real Facebook page embeds a URL routing table naming the checkpoint
    and login endpoints. Matching those substrings made the tool declare the
    account checkpointed on ordinary search results and pause every run.

    Verbatim from a live Marketplace search page.
    """
    html = (
        '<!DOCTYPE html><html id="facebook"><head><title>Facebook</title></head>'
        "<body><div>Marketplace results</div>"
        '<script type="application/json">{"routes":'
        '{"\\/ajax\\/dtsg\\/":1,"\\/checkpoint\\/block\\/":1,"\\/exitdsite":1,'
        '"\\/login\\/device-based\\/regular\\/login\\/":1,"name=\\"pass\\"":1}}'
        "</script></body></html>"
    )
    assert detect_login_wall(html) is None


def test_a_checkpoint_is_still_detected_when_the_page_also_has_scripts():
    """Stripping scripts must not blind the detector to a genuine interstitial."""
    html = (
        "<html><body>"
        '<script type="application/json">{"routes":{"\\/checkpoint\\/block\\/":1}}</script>'
        "<div>We need to confirm it's you before you continue.</div>"
        "</body></html>"
    )
    assert detect_login_wall(html) == "checkpoint"


def test_detect_unavailable(fixtures_dir: Path):
    from marketsearch.sources.parse import detect_unavailable
    assert detect_unavailable(page(fixtures_dir, "unavailable.html")) is True
    assert detect_unavailable(page(fixtures_dir, "item.html")) is False


# ---- captured real pages ------------------------------------------------
# These skip when the captures are absent, so CI on a fresh clone still passes.
# Run scripts/capture_pages.py to produce them.


def real_page(fixtures_dir: Path, name: str) -> str:
    path = fixtures_dir / "pages" / name
    if not path.exists():
        pytest.skip(f"{name} not captured yet — run scripts/capture_pages.py")
    return path.read_text(encoding="utf-8")


def test_real_search_page_parses(fixtures_dir: Path):
    listings = parse_search_results(real_page(fixtures_dir, "real_search.html"))
    assert listings, "captured search page produced zero listings"
    for listing in listings:
        assert listing.listing_id.isdigit(), listing.listing_id
        assert listing.title.strip()
        assert listing.url.endswith(f"/{listing.listing_id}/")


def test_real_search_page_yields_some_prices(fixtures_dir: Path):
    """Not every listing has a price, but a whole page without one means the
    price key moved."""
    listings = parse_search_results(real_page(fixtures_dir, "real_search.html"))
    assert any(l.price_cents is not None for l in listings)


def test_real_item_page_parses(fixtures_dir: Path):
    html = real_page(fixtures_dir, "real_item.html")
    listing_id = parse_search_results(html)[0].listing_id
    detail = parse_item_detail(html, listing_id)
    assert detail.description.strip(), "captured item page produced no description"


def test_real_saved_page_parses(fixtures_dir: Path):
    saved = parse_saved_listings(real_page(fixtures_dir, "real_saved.html"))
    for listing in saved:
        assert listing.listing_id.isdigit()
