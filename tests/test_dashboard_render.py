from __future__ import annotations

from marketsearch.dashboard import render_dashboard
from tests.test_dashboard_ranking import judged
from tests.test_pipeline_scan import watchlist_config

STAMP = "2026-07-28T09:00:00+00:00"


def render(rows, life_hours=6000):
    return render_dashboard(rows, watchlist_config(), STAMP, life_hours)


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
    assert "hours unknown" in html.lower()


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
