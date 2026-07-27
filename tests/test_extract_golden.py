from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from marketsearch.extract import Extractor
from marketsearch.models import ListingDetail, RawListing

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "listings"
CASES = sorted(GOLDEN_DIR.glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def as_inputs(case: dict) -> tuple[RawListing, ListingDetail]:
    listing = RawListing(
        listing_id="golden", title=case["title"], price_cents=case["price_cents"],
        location="Olathe, KS", url="https://example.com/golden",
        thumbnail_url=None, seller_name=None,
    )
    detail = ListingDetail(
        listing_id="golden", description=case["description"],
        structured_fields={}, photo_urls=[], distance_miles=None,
    )
    return listing, detail


def test_golden_directory_is_not_empty():
    assert CASES, "no golden fixtures found — the suite would silently pass"


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_fixture_is_well_formed(path: Path):
    """Runs in CI. Guards against a malformed fixture that would make the live
    suite fail for the wrong reason."""
    case = load(path)
    for key in ("name", "title", "price_cents", "description", "criteria", "expect"):
        assert key in case, f"{path.name} missing '{key}'"
    assert case["expect"]["verdict"] in {"match", "no_match", "unverifiable"}
    as_inputs(case)  # must construct without a validation error


@pytest.mark.live_api
@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_extraction_matches_golden_verdict(path: Path):
    """Deselected by default. Run with: pytest -m live_api"""
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    case = load(path)
    listing, detail = as_inputs(case)
    extractor = Extractor(anthropic.Anthropic(), model="claude-opus-5", effort="low")
    result = extractor.extract(listing, detail, case["criteria"])
    got = result.extraction
    expect = case["expect"]

    assert got.verdict == expect["verdict"], (
        f"{case['name']}\nexpected {expect['verdict']}, got {got.verdict}\n"
        f"reasoning: {got.reasoning}"
    )

    if "engine_hours" in expect:
        assert got.core.engine_hours == expect["engine_hours"], (
            f"{case['name']}\nhours mismatch — reasoning: {got.reasoning}"
        )
    if "two_speed" in expect:
        assert got.specs.two_speed == expect["two_speed"]
    if "cab_enclosed" in expect:
        assert got.specs.cab_enclosed == expect["cab_enclosed"]
    if "unknowns_include" in expect:
        assert expect["unknowns_include"] in got.unknowns
