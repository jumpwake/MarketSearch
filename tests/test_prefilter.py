from __future__ import annotations

import pytest

from marketsearch.config import ModelConfig, SearchConfig, WatchlistConfig
from marketsearch.models import RawListing
from marketsearch.prefilter import Assignment, Rejection, assign, identify_model, offer, prefilter


def search(**overrides) -> SearchConfig:
    base = dict(
        name="bobcat-t770",
        query="Bobcat T770",
        price_min_cents=1_500_000,
        price_max_cents=6_000_000,
        title_must_match=["t770"],
        title_must_not_match=["wanted", "parts only"],
        on_unknown="alert",
        criteria="Under 3000 hours.",
    )
    base.update(overrides)
    return SearchConfig(**base)


def listing(title: str = "2019 Bobcat T770", price_cents: int | None = 3_800_000) -> RawListing:
    return RawListing(
        listing_id="1", title=title, price_cents=price_cents, location=None,
        url="https://example.com/1", thumbnail_url=None, seller_name=None,
    )


def test_keeps_a_normal_match():
    result = prefilter(listing(), search())
    assert result.keep is True
    assert result.reason is None


def test_drops_title_missing_required_token():
    result = prefilter(listing(title="2019 Bobcat T650"), search())
    assert result.keep is False
    assert "t770" in result.reason


def test_title_matching_is_case_insensitive():
    assert prefilter(listing(title="BOBCAT T770 LOADER"), search()).keep is True


def test_drops_wanted_ads():
    result = prefilter(listing(title="WANTED: Bobcat T770"), search())
    assert result.keep is False
    assert "wanted" in result.reason


def test_drops_parts_listings():
    result = prefilter(listing(title="Bobcat T770 parts only"), search())
    assert result.keep is False
    assert "parts only" in result.reason


def test_exclusions_checked_before_inclusions():
    """A title matching both lists is rejected — exclusion wins."""
    result = prefilter(listing(title="Wanted Bobcat T770"), search())
    assert result.keep is False
    assert "wanted" in result.reason


def test_drops_price_above_maximum():
    result = prefilter(listing(price_cents=9_500_000), search())
    assert result.keep is False
    assert "above" in result.reason


def test_drops_price_below_minimum():
    result = prefilter(listing(price_cents=500_000), search())
    assert result.keep is False
    assert "below" in result.reason


def test_price_at_boundaries_is_kept():
    assert prefilter(listing(price_cents=1_500_000), search()).keep is True
    assert prefilter(listing(price_cents=6_000_000), search()).keep is True


def test_missing_price_is_kept_for_extraction_to_judge():
    """Marketplace occasionally omits price. Dropping those silently would
    discard exactly the listings worth a phone call."""
    result = prefilter(listing(price_cents=None), search())
    assert result.keep is True


def test_empty_matcher_lists_keep_everything():
    result = prefilter(
        listing(title="Some random skid steer"),
        search(title_must_match=[], title_must_not_match=[]),
    )
    assert result.keep is True


def test_any_required_token_is_enough():
    """The tokens are spelling variants of one model, not conditions to meet
    together. No title ever contains 't770' and 't-770' and 't 770' at once."""
    result = prefilter(
        listing(title="Bobcat T-770 compact track loader"),
        search(title_must_match=["t770", "t-770", "t 770"]),
    )
    assert result.keep is True
    assert result.reason is None


def test_drops_a_title_matching_none_of_the_required_tokens():
    result = prefilter(
        listing(title="2019 Bobcat T650"),
        search(title_must_match=["t770", "t-770", "t 770"]),
    )
    assert result.keep is False
    assert "t-770" in result.reason


def test_exclusion_terms_match_whole_words_only():
    """'toy' excludes toys, not Toyotas. Substring exclusions silently discard
    real machines and the drop reason looks legitimate."""
    result = prefilter(
        listing(title="2015 Toyota 8FGU25 Bobcat T770"),
        search(title_must_not_match=["toy"]),
    )
    assert result.keep is True


def test_exclusion_terms_still_match_across_a_space():
    result = prefilter(
        listing(title="BOBCAT T770 TRACK LOADER FOR RENT"),
        search(title_must_not_match=["for rent"]),
    )
    assert result.keep is False
    assert "for rent" in result.reason


def test_exclusion_terms_match_at_a_punctuation_boundary():
    result = prefilter(
        listing(title="Bobcat T770 - parts only, no engine"),
        search(title_must_not_match=["parts only"]),
    )
    assert result.keep is False


def test_required_terms_still_match_inside_a_longer_model_number():
    """Guard: word boundaries must NOT reach title_must_match. '299d' has to
    keep matching '299d3xe', which is how Marketplace sellers write it."""
    result = prefilter(
        listing(title="2021 Cat 299d3xe", price_cents=4_690_000),
        search(title_must_match=["299d"], price_max_cents=5_000_000),
    )
    assert result.keep is True


def model(name, keywords, lo, hi):
    return ModelConfig(
        name=name, keywords=keywords,
        price_min_cents=lo * 100, price_max_cents=hi * 100,
    )


MACHINES = WatchlistConfig(
    name="track-loaders",
    queries=["Bobcat T770"],
    models=[
        model("bobcat-t770", ["t770", "t-770"], 15000, 53000),
        model("bobcat-t750", ["t750"], 15000, 50000),
    ],
    exclude=["wanted", "s770"],
    criteria="Under 3000 hours.",
)

ATTACHMENTS = WatchlistConfig(
    name="attachments",
    queries=["skid steer root grapple"],
    models=[model("root-grapple", ["grapple"], 800, 6000)],
    exclude=["mini"],
    criteria="Root grapple.",
)

ALL = [MACHINES, ATTACHMENTS]


def wl_listing(title, price_cents):
    return RawListing(
        listing_id="1", title=title, price_cents=price_cents, location="Peoria, IL",
        url="https://example.com/1", thumbnail_url=None, seller_name=None,
    )


def test_identify_model_matches_substring():
    assert identify_model("2019 bobcat t770 loader", MACHINES).name == "bobcat-t770"


def test_identify_model_returns_none_when_nothing_matches():
    assert identify_model("2018 bobcat t595", MACHINES) is None


def test_a_query_keeps_a_machine_from_another_model():
    """The whole point: the T86 query surfaced a T770 and we keep it."""
    result = assign(wl_listing("2019 Bobcat T770", 4_200_000), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "bobcat-t770"
    assert result.watchlist.name == "track-loaders"


def test_machine_sold_with_a_grapple_is_a_machine():
    result = assign(wl_listing("2019 Bobcat T770 with root grapple", 4_200_000), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "bobcat-t770"


def test_grapple_for_a_machine_falls_through_on_price():
    """t770 matches, but $3,000 is below the machine band, so attachments take it."""
    result = assign(wl_listing("Root grapple for Bobcat T770", 300_000), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "root-grapple"
    assert result.watchlist.name == "attachments"


def test_plain_grapple_falls_through_on_no_model():
    result = assign(wl_listing("Eterra skidsteer Grapple", 549_500), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "root-grapple"


def test_rejected_when_no_watchlist_accepts():
    result = assign(wl_listing("2018 Bobcat T595", 3_000_000), ALL)
    assert isinstance(result, Rejection)
    assert result.reason == "matched no watched model"


def test_exclusion_beats_a_model_match():
    result = assign(wl_listing("Wanted: Bobcat T770", 4_000_000), ALL)
    assert isinstance(result, Rejection)
    assert "excluded term 'wanted'" in result.reason


def test_exclusions_are_whole_word():
    """'s770' must not fire on 'Bobcat T770'."""
    assert isinstance(assign(wl_listing("2019 Bobcat T770", 4_000_000), ALL), Assignment)


def test_price_reason_names_the_model():
    result = assign(wl_listing("2019 Bobcat T770", 9_900_000), ALL)
    assert isinstance(result, Rejection)
    assert "bobcat-t770" in result.reason
    assert "above" in result.reason


def test_missing_price_is_not_a_rejection():
    result = assign(wl_listing("2019 Bobcat T770", None), ALL)
    assert isinstance(result, Assignment)


def test_offer_declines_without_consulting_other_watchlists():
    assert isinstance(offer(wl_listing("Eterra Grapple", 549_500), MACHINES), Rejection)
