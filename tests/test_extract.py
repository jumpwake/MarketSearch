from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from marketsearch.extract import ExtractionError, Extractor, build_prompt
from marketsearch.models import ListingDetail, RawListing

VALID_PAYLOAD = {
    "core": {"year": 2019, "make_model": "Bobcat T770", "engine_hours": 2400,
             "asking_price": 38000, "location": "Olathe, KS"},
    "specs": {"cab_enclosed": True, "has_ac": True, "two_speed": True, "high_flow": False,
              "tracks_or_tires": "tracks", "undercarriage_condition": "good",
              "aux_hydraulics": True},
    "condition": {"runs": True, "stated_issues": [], "recent_service": [],
                  "damage_notes": None, "one_owner_claim": False},
    "deal": {"attachments": ["bucket"], "seller_type": "private",
             "financing_or_trade": False, "price_vs_market_note": "fair"},
    "verdict": "match", "confidence": 0.9,
    "reasoning": "2,400 hours, 2-speed confirmed.", "unknowns": [],
}


class StubClient:
    """Records the request and replays a canned response."""

    def __init__(self, payload=None, stop_reason="end_turn", input_tokens=1500,
                 output_tokens=400):
        self.captured: dict = {}
        text = json.dumps(payload if payload is not None else VALID_PAYLOAD)
        self._response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason=stop_reason,
            usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        )
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.captured = kwargs
        return self._response


def listing() -> RawListing:
    return RawListing(
        listing_id="1", title="2019 Bobcat T770", price_cents=3_800_000,
        location="Olathe, KS", url="https://example.com/1",
        thumbnail_url=None, seller_name="Dale S",
    )


def detail(description: str = "2,400 hours. 2-speed. Cab with AC.") -> ListingDetail:
    return ListingDetail(
        listing_id="1", description=description,
        structured_fields={"condition": "used"}, photo_urls=[], distance_miles=42.0,
    )


def test_prompt_contains_criteria_title_and_description():
    prompt = build_prompt(listing(), detail(), "Under 3000 engine hours.")
    assert "Under 3000 engine hours." in prompt
    assert "2019 Bobcat T770" in prompt
    assert "2,400 hours" in prompt


def test_prompt_includes_asking_price_in_dollars():
    prompt = build_prompt(listing(), detail(), "any")
    assert "$38,000" in prompt


def test_prompt_handles_missing_price():
    no_price = listing().model_copy(update={"price_cents": None})
    prompt = build_prompt(no_price, detail(), "any")
    assert "not stated" in prompt.lower()


def test_extract_returns_parsed_extraction():
    extractor = Extractor(StubClient(), model="claude-opus-5", effort="low")
    result = extractor.extract(listing(), detail(), "Under 3000 engine hours.")
    assert result.extraction.verdict == "match"
    assert result.extraction.core.engine_hours == 2400


def test_extract_sends_the_json_schema_and_effort():
    client = StubClient()
    Extractor(client, model="claude-opus-5", effort="low").extract(
        listing(), detail(), "any"
    )
    output_config = client.captured["output_config"]
    assert output_config["effort"] == "low"
    assert output_config["format"]["type"] == "json_schema"
    assert output_config["format"]["schema"]["additionalProperties"] is False
    assert client.captured["model"] == "claude-opus-5"


def test_extract_never_sends_deprecated_output_format():
    client = StubClient()
    Extractor(client, model="claude-opus-5", effort="low").extract(listing(), detail(), "any")
    assert "output_format" not in client.captured


def test_cost_is_computed_from_token_usage():
    client = StubClient(input_tokens=1_000_000, output_tokens=1_000_000)
    result = Extractor(client, model="claude-opus-5", effort="low").extract(
        listing(), detail(), "any"
    )
    # Opus 5: $5/MTok in, $25/MTok out -> $30.00 -> 3000 cents
    assert result.cost_cents == pytest.approx(3000.0)


def test_cost_uses_the_configured_model_price():
    client = StubClient(input_tokens=1_000_000, output_tokens=1_000_000)
    result = Extractor(client, model="claude-haiku-4-5", effort="low").extract(
        listing(), detail(), "any"
    )
    # Haiku 4.5: $1/MTok in, $5/MTok out -> $6.00 -> 600 cents
    assert result.cost_cents == pytest.approx(600.0)


def test_unknown_model_price_falls_back_to_zero_not_a_crash():
    client = StubClient()
    result = Extractor(client, model="some-future-model", effort="low").extract(
        listing(), detail(), "any"
    )
    assert result.cost_cents == 0.0


def test_refusal_raises_extraction_error():
    client = StubClient(stop_reason="refusal")
    with pytest.raises(ExtractionError, match="refused"):
        Extractor(client, model="claude-opus-5", effort="low").extract(
            listing(), detail(), "any"
        )


def test_truncated_response_raises_extraction_error():
    client = StubClient(stop_reason="max_tokens")
    with pytest.raises(ExtractionError, match="truncated"):
        Extractor(client, model="claude-opus-5", effort="low").extract(
            listing(), detail(), "any"
        )


def test_malformed_json_raises_extraction_error():
    client = StubClient()
    client._response.content = [SimpleNamespace(type="text", text="not json at all")]
    with pytest.raises(ExtractionError, match="did not return valid JSON"):
        Extractor(client, model="claude-opus-5", effort="low").extract(
            listing(), detail(), "any"
        )


def test_schema_violating_json_raises_extraction_error():
    client = StubClient(payload={"verdict": "match"})  # missing everything else
    with pytest.raises(ExtractionError, match="did not match the schema"):
        Extractor(client, model="claude-opus-5", effort="low").extract(
            listing(), detail(), "any"
        )


def test_thinking_blocks_before_text_are_skipped():
    """Opus 5 has thinking on by default, so content[0] may be a thinking block."""
    client = StubClient()
    client._response.content = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=json.dumps(VALID_PAYLOAD)),
    ]
    result = Extractor(client, model="claude-opus-5", effort="low").extract(
        listing(), detail(), "any"
    )
    assert result.extraction.verdict == "match"
