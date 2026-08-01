from __future__ import annotations

import re

from marketsearch.dashboard import render_dashboard
from tests.test_dashboard_ranking import judged
from tests.test_pipeline_scan import watchlist_config

STAMP = "2026-07-28T09:00:00+00:00"


def render(rows, life_hours=6000):
    return render_dashboard(rows, watchlist_config(), STAMP, life_hours)


def _card_html(html, listing_id):
    """The single <article class="card" ...>...</article> block for one
    listing, so assertions can be scoped to what that card actually
    renders rather than matching boilerplate text baked into `_JS` that
    appears on every page regardless of test data."""
    match = re.search(
        rf'<article class="card" data-listing-id="{re.escape(listing_id)}".*?</article>',
        html, re.S,
    )
    assert match, f"no card found for listing {listing_id!r}"
    return match.group(0)


def _value_chip_text(card_html):
    match = re.search(r'<span class="chip value-chip">(.*?)</span>', card_html, re.S)
    assert match, "value-chip span not found in card"
    return match.group(1)


def _pick_html(html, listing_id):
    match = re.search(
        rf'<li data-pick="{re.escape(listing_id)}".*?</li>', html, re.S,
    )
    assert match, f"no pick found for listing {listing_id!r}"
    return match.group(0)


def _pick_value_text(pick_html):
    match = re.search(r'<span class="pick-value">(.*?)</span>', pick_html, re.S)
    assert match, "pick-value span not found in pick"
    return match.group(1)


def test_a_script_tag_in_a_title_is_escaped():
    """Titles come from Facebook. They are not trusted."""
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200,
                          title="<script>alert(1)</script>")])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_script_tag_in_reasoning_is_escaped():
    """Reasoning comes from an LLM. Also not trusted."""
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200,
                          reasoning="<img src=x onerror=alert(1)>")])
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_the_page_is_self_contained():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200)])
    for forbidden in ("<script src=", "<link rel=\"stylesheet\"", "cdn.", "https://unpkg"):
        assert forbidden not in html


def test_every_judged_listing_appears_in_the_browse_list():
    rows = [
        judged("1", "bobcat-t770", 3_000_000, 2200),
        judged("2", "root-grapple", 300_000, None, verdict="no_match"),
        judged("3", "kubota-svl95", 3_200_000, 2984, verdict="unverifiable"),
    ]
    html = render(rows)
    for listing_id in ("1", "2", "3"):
        assert f'data-listing-id="{listing_id}"' in html


def test_top_picks_states_the_real_count_rather_than_padding():
    rows = [judged("1", "bobcat-t770", 3_000_000, 2200)]
    assert "1 match" in render(rows)


def test_top_picks_omits_retired_models_but_browse_keeps_them():
    # "case-tv380" deliberately does not appear in watchlist_config()'s
    # models — unlike "root-grapple", which is live there — so this
    # exercises the retirement path rather than the unknown-hours path.
    rows = [judged("g", "case-tv380", 300_000, None)]
    html = render(rows)
    assert 'data-pick="g"' not in html
    assert 'data-listing-id="g"' in html


def test_cards_carry_the_data_the_client_script_sorts_on():
    html = render([judged("1", "bobcat-t770", 3_950_000, 2200)])
    assert 'data-price-cents="3950000"' in html
    assert 'data-hours="2200"' in html
    assert 'data-verdict="match"' in html
    assert 'data-model="bobcat-t770"' in html


def test_unknown_hours_are_labelled_not_zero():
    html = render([judged("1", "bobcat-t770", 3_000_000, None)])
    assert 'data-hours=""' in html
    # Scoped to this card's value chip rather than the whole page: the
    # literal "hours unknown" also appears in _JS's boilerplate on every
    # render, so a page-wide substring check would pass even if _card's
    # own unknown-hours label were broken.
    card = _card_html(html, "1")
    assert "hours unknown" in _value_chip_text(card).lower()


def test_unknown_price_renders_as_empty_not_zero():
    """`data-price-cents` must distinguish "unknown price" from "literally
    free" the same way `data-hours` already distinguishes unknown hours
    from zero hours — otherwise the client-side value formula cannot tell
    the two apart either."""
    html = render([judged("1", "bobcat-t770", None, 2200)])
    assert 'data-price-cents=""' in html
    assert 'data-price-cents="0"' not in html


def test_unknown_price_label_says_price_unknown_not_hours_unknown():
    """A card with known hours and no price must not read 'hours unknown' —
    that would contradict the hours the card plainly states elsewhere."""
    html = render([judged("1", "bobcat-t770", None, 2200)])
    card = _card_html(html, "1")
    text = _value_chip_text(card).lower()
    assert "price unknown" in text
    assert "hours unknown" not in text


def test_unknown_price_label_on_a_top_pick_says_price_unknown():
    html = render([judged("1", "bobcat-t770", None, 2200)])
    pick = _pick_html(html, "1")
    text = _pick_value_text(pick).lower()
    assert "price unknown" in text
    assert "hours unknown" not in text


def test_picks_carry_the_data_the_client_script_sorts_on():
    """Top Picks <li> elements need the same data-hours / data-price-cents
    attributes as browse cards, or the slider has nothing to re-sort."""
    html = render([judged("1", "bobcat-t770", 3_950_000, 2200)])
    pick = _pick_html(html, "1")
    assert 'data-hours="2200"' in pick
    assert 'data-price-cents="3950000"' in pick


def test_the_slider_bounds_contain_an_out_of_range_life_hours():
    """--life-hours outside the default 3000-12000 slider range must widen
    the slider rather than let the browser silently clamp it out of sync
    with the server-rendered Top Picks numbers."""
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200)], life_hours=20000)
    assert 'min="3000"' in html
    assert 'max="20000"' in html
    assert 'value="20000"' in html


def test_the_slider_bounds_stay_at_the_defaults_within_range():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200)], life_hours=8000)
    assert 'min="3000"' in html
    assert 'max="12000"' in html


def test_a_missing_thumbnail_renders_a_placeholder():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200,
                          thumbnail_url=None)])
    assert "no photo" in html.lower()


def test_the_criteria_panel_shows_the_watchlist_criteria():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200)])
    assert "Under 3000 engine hours." in html


def test_the_assumed_life_control_reflects_the_value_passed_in():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200)], life_hours=8000)
    assert 'value="8000"' in html


def test_an_empty_corpus_renders_a_valid_page():
    html = render([])
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    assert "nothing judged yet" in html.lower()


def test_unknowns_are_surfaced_on_the_card():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200,
                          unknowns=["engine_hours", "quick_attach_plate"])])
    assert "quick_attach_plate" in html


# ---- model spec chips and short location on Top Picks --------------------

def test_pick_shows_hp_and_weight_from_the_model_config():
    """HP is a property of the model, not the listing, so it comes from config."""
    html = render([judged("1", "bobcat-t770", 3_950_000, 2200)])
    assert "92 hp" in html
    assert "10,900 lb" in html


def test_pick_omits_spec_chips_when_the_model_has_no_figures():
    """A model without hp/weight must not render an empty or 'None' chip."""
    html = render([judged("1", "root-grapple", 300_000, None)])
    picks = html[html.find('class="top"'):html.find('class="browse"')]
    assert "None hp" not in picks
    assert "None lb" not in picks


def test_pick_shows_an_abbreviated_location():
    html = render([judged("1", "bobcat-t770", 3_950_000, 2200,
                          location="Cameron, Missouri")])
    picks = html[html.find('class="top"'):html.find('class="browse"')]
    assert "Cameron, MO" in picks
    assert "Cameron, Missouri" not in picks


# ---- discarding and the NEW badge ----------------------------------------

DISCARDED = "2026-07-28T08:00:00+00:00"


def test_a_discarded_listing_stays_in_browse_but_leaves_top_picks():
    """Discarding hides a listing; it does not erase it. The ledger keeps
    every listing ever seen, and a discard has to be reversible."""
    html = render([
        judged("keep", "bobcat-t770", 3_950_000, 2200),
        judged("scam", "bobcat-t770", 1_500_000, 900, dismissed_at=DISCARDED),
    ])
    assert 'data-pick="scam"' not in html
    assert 'data-pick="keep"' in html
    assert 'data-listing-id="scam"' in html


def test_a_discarded_card_is_marked_so_the_page_can_tell_it_apart():
    """The client script layers unsaved clicks on top of this. It has to be
    able to distinguish 'the database knows' from 'this browser knows'."""
    html = render([judged("scam", "bobcat-t770", 1_500_000, 900,
                          dismissed_at=DISCARDED, dismiss_reason="fake seller")])
    card = _card_html(html, "scam")
    assert 'data-dismissed="1"' in card
    assert "fake seller" in card


def test_a_live_card_is_not_marked_discarded():
    card = _card_html(render([judged("1", "bobcat-t770", 3_950_000, 2200)]), "1")
    assert 'data-dismissed=""' in card
    assert 'data-dismissed="1"' not in card


def test_a_discard_reason_from_the_database_is_escaped():
    """Reasons are typed at a shell prompt and stored verbatim."""
    html = render([judged("1", "bobcat-t770", 3_950_000, 2200,
                          dismissed_at=DISCARDED,
                          dismiss_reason="<script>alert(1)</script>")])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_every_card_offers_a_discard_control():
    card = _card_html(render([judged("1", "bobcat-t770", 3_950_000, 2200)]), "1")
    assert 'class="discard"' in card


def test_cards_and_picks_carry_what_the_new_badge_needs():
    """The badge is decided in the browser against a watermark, so the page
    has to ship both the per-listing date and the page's own build time."""
    html = render([judged("1", "bobcat-t770", 3_950_000, 2200)])
    assert f'data-generated="{STAMP}"' in html
    assert 'data-seen="2026-07-27T10:00:00+00:00"' in _card_html(html, "1")
    assert 'data-seen="2026-07-27T10:00:00+00:00"' in _pick_html(html, "1")
    assert 'class="badge badge--new" hidden' in html


def test_the_browse_heading_counts_what_has_been_discarded():
    html = render([
        judged("keep", "bobcat-t770", 3_950_000, 2200),
        judged("scam", "bobcat-t770", 1_500_000, 900, dismissed_at=DISCARDED),
    ])
    assert "1 discarded and hidden" in html


def test_no_discard_count_is_shown_when_nothing_is_discarded():
    html = render([judged("1", "bobcat-t770", 3_950_000, 2200)])
    assert "discarded and hidden" not in html


def test_abbreviating_leaves_an_unrecognised_location_alone():
    from marketsearch.format import short_location
    assert short_location("Cameron, Missouri") == "Cameron, MO"
    assert short_location("Sharon Grove, Kentucky") == "Sharon Grove, KY"
    assert short_location("Peoria, IL") == "Peoria, IL"
    assert short_location("Somewhere Odd") == "Somewhere Odd"
    assert short_location(None) == ""
