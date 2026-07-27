from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from marketsearch.notify.render import (
    ChangeCard,
    MatchCard,
    download_photos,
    render_email,
    render_sms,
)
from marketsearch.store import ExtractionRow, ListingRow


def listing(listing_id="1", title="2019 Bobcat T770", price_cents=3_800_000) -> ListingRow:
    return ListingRow(
        listing_id=listing_id, search_name="bobcat-t770", title=title,
        price_cents=price_cents, location="Olathe, KS",
        url=f"https://www.facebook.com/marketplace/item/{listing_id}/",
        thumbnail_url=None, seller_name="Dale S", fingerprint="fp",
        stage="matched", reject_reason=None, watched=False,
        first_seen_at="2026-07-26T10:00:00+00:00",
        last_seen_at="2026-07-26T10:00:00+00:00", last_change_check_at=None,
        extraction_attempts=0,
    )


def extraction(verdict="match", unknowns=None) -> ExtractionRow:
    return ExtractionRow(
        listing_id="1",
        attributes={
            "core": {"year": 2019, "make_model": "Bobcat T770", "engine_hours": 2400,
                     "asking_price": 38000, "location": "Olathe, KS"},
            "specs": {"cab_enclosed": True, "has_ac": True, "two_speed": True,
                      "high_flow": False, "tracks_or_tires": "tracks",
                      "undercarriage_condition": "70% remaining", "aux_hydraulics": True},
            "condition": {"runs": True, "stated_issues": [], "recent_service": ["new filters"],
                          "damage_notes": None, "one_owner_claim": True},
            "deal": {"attachments": ["bucket", "forks"], "seller_type": "private",
                     "financing_or_trade": False, "price_vs_market_note": "fair"},
        },
        verdict=verdict, confidence=0.9,
        reasoning="2,400 hours is under the limit and 2-speed is confirmed.",
        unknowns=unknowns or [], model="claude-opus-5",
        created_at="2026-07-26T10:00:00+00:00",
    )


def match(photos=None) -> MatchCard:
    return MatchCard(listing=listing(), extraction=extraction(), photos=photos or [])


def test_subject_counts_matches():
    email = render_email([match(), match()], [], [])
    assert "2 new matches" in email.subject


def test_subject_is_singular_for_one_match():
    email = render_email([match()], [], [])
    assert "1 new match" in email.subject
    assert "matches" not in email.subject


def test_subject_mentions_changes():
    change = ChangeCard(listing=listing(), kind="price_change",
                        old_price_cents=4_100_000, new_price_cents=3_800_000)
    email = render_email([], [], [change])
    assert "1 price change" in email.subject


def test_body_shows_price_hours_and_reasoning():
    html = render_email([match()], [], []).html
    assert "$38,000" in html
    assert "2,400" in html
    assert "2-speed is confirmed" in html


def test_body_links_to_the_listing():
    html = render_email([match()], [], []).html
    assert "https://www.facebook.com/marketplace/item/1/" in html


def test_body_shows_attributes_table():
    html = render_email([match()], [], []).html
    for label in ("Hours", "Year", "Cab", "2-speed", "Attachments", "Seller"):
        assert label in html


def test_unverified_section_names_the_gap():
    card = MatchCard(
        listing=listing(),
        extraction=extraction(verdict="unverifiable", unknowns=["engine_hours"]),
        photos=[],
    )
    html = render_email([], [card], []).html
    assert "Unverified" in html
    assert "engine_hours" in html


def test_price_drop_shows_both_prices():
    change = ChangeCard(listing=listing(), kind="price_change",
                        old_price_cents=4_100_000, new_price_cents=3_800_000)
    html = render_email([], [], [change]).html
    assert "$41,000" in html
    assert "$38,000" in html


def test_removed_listing_says_likely_sold():
    change = ChangeCard(listing=listing(), kind="removed",
                        old_price_cents=3_800_000, new_price_cents=None)
    html = render_email([], [], [change]).html
    assert "likely sold" in html.lower()


def test_photos_become_cid_references():
    email = render_email([match(photos=[b"\x89PNG-a", b"\x89PNG-b"])], [], [])
    assert len(email.images) == 2
    for cid, _data in email.images:
        assert f'cid:{cid}' in email.html


def test_photo_cids_are_unique_across_cards():
    a = MatchCard(listing=listing("1"), extraction=extraction(), photos=[b"a"])
    b = MatchCard(listing=listing("2"), extraction=extraction(), photos=[b"b"])
    email = render_email([a, b], [], [])
    cids = [cid for cid, _ in email.images]
    assert len(set(cids)) == 2


def test_card_without_photos_still_renders():
    html = render_email([match(photos=[])], [], []).html
    assert "2019 Bobcat T770" in html


def test_html_escapes_listing_titles():
    """A seller-controlled title must not be able to inject markup."""
    card = MatchCard(
        listing=listing(title='T770 <script>alert("x")</script>'),
        extraction=extraction(), photos=[],
    )
    html = render_email([card], [], []).html
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_sms_is_short_and_mentions_counts():
    text = render_sms([match(), match()], [match()], [])
    assert len(text) <= 160
    assert "2" in text
    assert "check email" in text.lower()


def test_sms_mentions_changes_when_there_are_no_matches():
    change = ChangeCard(listing=listing(), kind="price_change",
                        old_price_cents=4_100_000, new_price_cents=3_800_000)
    text = render_sms([], [], [change])
    assert "1 change" in text


def test_download_photos_respects_the_limit():
    calls: list[str] = []

    def get(url: str) -> bytes:
        calls.append(url)
        return b"img"

    urls = [f"https://example.com/{i}.jpg" for i in range(10)]
    assert len(download_photos(urls, limit=3, get=get)) == 3
    assert len(calls) == 3


def test_download_photos_skips_failures_without_raising():
    def get(url: str) -> bytes:
        if "bad" in url:
            raise RuntimeError("404")
        return b"img"

    photos = download_photos(
        ["https://example.com/bad.jpg", "https://example.com/good.jpg"], limit=3, get=get
    )
    assert photos == [b"img"]


SNAPSHOT = Path(__file__).parent / "fixtures" / "email_snapshot.html"
_CID = re.compile(r"photo-[0-9a-f]{32}")


def test_email_html_matches_the_snapshot():
    """Guards against layout drift. Regenerate deliberately with:
        MARKETSEARCH_UPDATE_SNAPSHOTS=1 pytest tests/test_render.py
    and read the diff before committing it."""
    change = ChangeCard(listing=listing(), kind="price_change",
                        old_price_cents=4_100_000, new_price_cents=3_800_000)
    unverified_card = MatchCard(
        listing=listing("2"),
        extraction=extraction(verdict="unverifiable", unknowns=["engine_hours"]),
        photos=[],
    )
    html = render_email([match(photos=[b"img"])], [unverified_card], [change]).html
    normalised = _CID.sub("photo-CID", html)

    if os.environ.get("MARKETSEARCH_UPDATE_SNAPSHOTS") or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(normalised, encoding="utf-8")
        pytest.skip("snapshot written")

    assert normalised == SNAPSHOT.read_text(encoding="utf-8")
