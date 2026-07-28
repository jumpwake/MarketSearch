from __future__ import annotations

from marketsearch.config import ModelConfig, WatchlistConfig
from marketsearch.models import RawListing
from marketsearch.prefilter import (
    NO_MODEL,
    Assignment,
    Rejection,
    assign,
    identify_model,
    offer,
)


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


def test_keyword_matching_is_case_insensitive():
    assert identify_model("bobcat t770 loader", MACHINES).name == "bobcat-t770"
    result = assign(wl_listing("BOBCAT T770 LOADER", 4_000_000), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "bobcat-t770"


def test_any_keyword_spelling_is_enough():
    """The keywords are spelling variants of one model, not conditions to meet
    together. No title ever contains 't770' and 't-770' at once."""
    result = assign(wl_listing("Bobcat T-770 compact track loader", 4_000_000), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "bobcat-t770"


def test_keywords_still_match_inside_a_longer_model_number():
    """Guard: word boundaries must NOT reach model keywords. '299d' has to keep
    matching '299d3xe', which is how Marketplace sellers write it."""
    cats = WatchlistConfig(
        name="cats", queries=["Caterpillar 299D"],
        models=[model("cat-299d", ["299d"], 12000, 53000)],
        criteria="Under 3000 hours.",
    )
    result = offer(wl_listing("2021 Cat 299d3xe", 4_690_000), cats)
    assert isinstance(result, Assignment)


def test_price_at_the_band_boundaries_is_kept():
    for price in (1_500_000, 5_300_000):
        assert isinstance(assign(wl_listing("2019 Bobcat T770", price), ALL), Assignment)


def test_price_below_the_band_names_the_model():
    result = assign(wl_listing("2019 Bobcat T750", 500_000), ALL)
    assert isinstance(result, Rejection)
    assert "bobcat-t750" in result.reason
    assert "below" in result.reason


def test_exclusion_terms_match_whole_words_only():
    """'toy' excludes toys, not Toyotas. Substring exclusions silently discard
    real machines and the drop reason looks legitimate."""
    junk = WatchlistConfig(
        name="junk-test", queries=["Bobcat T770"],
        models=[model("bobcat-t770", ["t770"], 15000, 53000)],
        exclude=["toy"], criteria="c",
    )
    assert isinstance(offer(wl_listing("2015 Toyota 8FGU25 Bobcat T770", 4_000_000), junk),
                      Assignment)


def test_exclusion_terms_still_match_across_a_space():
    junk = WatchlistConfig(
        name="junk-test", queries=["Bobcat T770"],
        models=[model("bobcat-t770", ["t770"], 15000, 53000)],
        exclude=["for rent"], criteria="c",
    )
    result = offer(wl_listing("BOBCAT T770 TRACK LOADER FOR RENT", 4_000_000), junk)
    assert isinstance(result, Rejection)
    assert "for rent" in result.reason


def test_exclusion_terms_match_at_a_punctuation_boundary():
    junk = WatchlistConfig(
        name="junk-test", queries=["Bobcat T770"],
        models=[model("bobcat-t770", ["t770"], 15000, 53000)],
        exclude=["parts only"], criteria="c",
    )
    result = offer(wl_listing("Bobcat T770 - parts only, no engine", 4_000_000), junk)
    assert isinstance(result, Rejection)


def test_bobcat_763_is_not_identified_as_the_t76_track_loader():
    """Regression: config.yaml used to carry a bare 't 76' keyword. It matched
    'boBCAT 763' across the word boundary (the 't' ending "bobcat", a space,
    then the '76' that starts '763') and pulled a wheeled Bobcat 763 skid
    steer into the T76 track-loader band. The fix anchors on 'bobcat t 76' —
    a real title still spelled "Bobcat T 76" keeps matching, but "Bobcat 763"
    no longer does."""
    t76 = WatchlistConfig(
        name="track-loaders", queries=["Bobcat T76 track loader"],
        models=[model("bobcat-t76", ["t76", "t-76", "bobcat t 76"], 25000, 60000)],
        exclude=["s770", "s750", "s870", "s320"],
        criteria="c",
    )
    listing = wl_listing("Bobcat 763 skid steer", 1_550_000)  # inside the t76 band
    assert identify_model(listing.title.lower(), t76) is None
    result = assign(listing, [t76])
    assert isinstance(result, Rejection)
    assert result.reason == NO_MODEL


def test_bobcat_863_is_not_identified_as_the_t86_track_loader():
    """Same bug, the T86 side: a bare 't 86' keyword matched 'boBCAT 863' and
    pulled a wheeled Bobcat 863 skid steer into the T86 band."""
    t86 = WatchlistConfig(
        name="track-loaders", queries=["Bobcat T86 track loader"],
        models=[model("bobcat-t86", ["t86", "t-86", "bobcat t 86"], 30000, 70000)],
        exclude=["s770", "s750", "s870", "s320"],
        criteria="c",
    )
    listing = wl_listing("Low hour Bobcat 863 skid steer", 1_290_000)  # inside the t86 band
    assert identify_model(listing.title.lower(), t86) is None
    result = assign(listing, [t86])
    assert isinstance(result, Rejection)
    assert result.reason == NO_MODEL


def test_bobcat_t_76_with_a_space_still_matches():
    """The anchored keyword must not lose the genuine spelling it exists to
    catch — a title actually written 'Bobcat T 76'."""
    t76 = WatchlistConfig(
        name="track-loaders", queries=["Bobcat T76 track loader"],
        models=[model("bobcat-t76", ["t76", "t-76", "bobcat t 76"], 25000, 60000)],
        criteria="c",
    )
    assert identify_model("2021 bobcat t 76 track loader", t76).name == "bobcat-t76"


def test_bobcat_t_86_with_a_space_still_matches():
    t86 = WatchlistConfig(
        name="track-loaders", queries=["Bobcat T86 track loader"],
        models=[model("bobcat-t86", ["t86", "t-86", "bobcat t 86"], 30000, 70000)],
        criteria="c",
    )
    assert identify_model("2021 bobcat t 86 track loader", t86).name == "bobcat-t86"


def test_earlier_models_win_ties():
    """Order is load-bearing: `identify_model` returns the first match, which is
    why the config keeps the longer model numbers above the shorter ones."""
    both = WatchlistConfig(
        name="ordered", queries=["Bobcat"],
        models=[model("bobcat-t76", ["t76"], 25000, 60000),
                model("bobcat-t760", ["t760"], 10000, 20000)],
        criteria="c",
    )
    assert identify_model("2022 bobcat t760", both).name == "bobcat-t76"
