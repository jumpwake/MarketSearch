from __future__ import annotations

from marketsearch.dashboard import (
    JudgedListing,
    engine_hours,
    top_picks,
    value_per_remaining_hour,
)
from marketsearch.models import ListingDetail
from marketsearch.store import ExtractionRow, ListingRow

LIVE = {"bobcat-t770", "kubota-svl95"}


def judged(listing_id, model_name, price_cents, hours, verdict="match",
           title="a machine", reasoning="because", unknowns=None,
           thumbnail_url="https://example.com/a.jpg",
           location="Peoria, IL") -> JudgedListing:
    """One judged listing. `ListingRow` and `ExtractionRow` are frozen, so every
    varying field is a parameter here rather than assigned after construction."""
    return JudgedListing(
        listing=ListingRow(
            listing_id=listing_id, title=title, price_cents=price_cents,
            location=location, url=f"https://example.com/{listing_id}",
            thumbnail_url=thumbnail_url, seller_name="Dale",
            fingerprint="fp", stage="matched", reject_reason=None, watched=False,
            first_seen_at="2026-07-27T10:00:00+00:00",
            last_seen_at="2026-07-27T10:00:00+00:00",
            last_change_check_at=None, extraction_attempts=0,
            watchlist_name="track-loaders", model_name=model_name,
        ),
        detail=ListingDetail(
            listing_id=listing_id, description="runs strong",
            structured_fields={}, photo_urls=[], distance_miles=None,
        ),
        extraction=ExtractionRow(
            listing_id=listing_id,
            attributes={"core": {"engine_hours": hours}},
            verdict=verdict, confidence=0.8, reasoning=reasoning,
            unknowns=unknowns or [], model="claude-opus-5",
            created_at="2026-07-27T10:00:00+00:00",
        ),
    )


def test_engine_hours_reads_the_core_attribute():
    assert engine_hours(judged("1", "bobcat-t770", 100, 2200).extraction) == 2200


def test_engine_hours_is_none_when_absent():
    assert engine_hours(judged("1", "bobcat-t770", 100, None).extraction) is None


def test_value_is_price_over_remaining_life():
    # $30,000 with 1,000 hours used of an assumed 6,000 -> 5,000 remaining
    assert value_per_remaining_hour(3_000_000, 1000, 6000) == 6.0


def test_value_is_none_without_hours():
    assert value_per_remaining_hour(3_000_000, None, 6000) is None


def test_value_is_none_without_a_price():
    assert value_per_remaining_hour(None, 1000, 6000) is None


def test_hours_beyond_assumed_life_do_not_divide_by_zero():
    """Clamped to 1 remaining hour rather than raising or going negative."""
    assert value_per_remaining_hour(3_000_000, 6000, 6000) == 30_000.0
    assert value_per_remaining_hour(3_000_000, 9999, 6000) == 30_000.0


def test_top_picks_excludes_non_match_verdicts():
    rows = [
        judged("1", "bobcat-t770", 3_950_000, 2200, verdict="unverifiable"),
        judged("2", "kubota-svl95", 3_200_000, 2984, verdict="no_match"),
    ]
    assert top_picks(rows, LIVE, 6000) == []


def test_top_picks_excludes_models_not_in_the_live_catalog():
    """A retired search's matches must not be presented as things to go buy."""
    rows = [judged("1", "root-grapple", 300_000, None)]
    assert top_picks(rows, LIVE, 6000) == []


def test_ranking_at_six_thousand_hours_prefers_low_hours():
    rows = [
        judged("svl95", "kubota-svl95", 3_200_000, 2984),
        judged("svl90", "kubota-svl95", 4_500_000, 1005),
        judged("t770", "bobcat-t770", 3_950_000, 2200),
    ]
    assert [p.row.listing.listing_id for p in top_picks(rows, LIVE, 6000)] == [
        "svl90", "t770", "svl95",
    ]


def test_ranking_inverts_at_ten_thousand_hours():
    """The assumed life reorders the list rather than merely adjusting it.

    This is why the figure is a user-facing control and not a constant: the
    same three machines rank in exactly the opposite order.
    """
    rows = [
        judged("svl95", "kubota-svl95", 3_200_000, 2984),
        judged("svl90", "kubota-svl95", 4_500_000, 1005),
        judged("t770", "bobcat-t770", 3_950_000, 2200),
    ]
    assert [p.row.listing.listing_id for p in top_picks(rows, LIVE, 10000)] == [
        "svl95", "svl90", "t770",
    ]


def test_unknown_hours_sort_last_rather_than_first():
    """Treating unknown hours as zero would rank them best. It must not."""
    rows = [
        judged("unknown", "bobcat-t770", 100_000, None),
        judged("known", "bobcat-t770", 4_500_000, 2200),
    ]
    picks = top_picks(rows, LIVE, 6000)
    assert [p.row.listing.listing_id for p in picks] == ["known", "unknown"]
    assert picks[-1].value_per_hour is None


def test_top_picks_honours_the_limit():
    rows = [judged(str(i), "bobcat-t770", 3_000_000 + i, 2000) for i in range(15)]
    assert len(top_picks(rows, LIVE, 6000, limit=10)) == 10


def test_pick_carries_the_numbers_it_was_ranked_on():
    rows = [judged("t770", "bobcat-t770", 3_950_000, 2200)]
    pick = top_picks(rows, LIVE, 6000)[0]
    assert pick.hours == 2200
    assert round(pick.value_per_hour, 2) == 10.39
