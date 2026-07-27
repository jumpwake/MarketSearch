from __future__ import annotations

from marketsearch.extraction_models import EXTRACTION_JSON_SCHEMA, Extraction


def _walk_objects(schema: dict):
    """Yield every object-typed subschema, including those under $defs."""
    for definition in schema.get("$defs", {}).values():
        if definition.get("type") == "object":
            yield definition
    if schema.get("type") == "object":
        yield schema


def test_every_object_forbids_additional_properties():
    for obj in _walk_objects(EXTRACTION_JSON_SCHEMA):
        assert obj.get("additionalProperties") is False, obj.get("title")


def test_every_property_is_required():
    """The structured-output API rejects a schema where a declared property is
    missing from `required`. Pydantic omits any field that has a default, so
    this test is the guard against someone adding `= None`."""
    for obj in _walk_objects(EXTRACTION_JSON_SCHEMA):
        declared = set(obj.get("properties", {}))
        required = set(obj.get("required", []))
        assert declared == required, f"{obj.get('title')}: {declared - required} not required"


def test_parses_a_complete_response():
    payload = {
        "core": {"year": 2019, "make_model": "Bobcat T770", "engine_hours": 2400,
                 "asking_price": 38000, "location": "Olathe, KS"},
        "specs": {"cab_enclosed": True, "has_ac": True, "two_speed": True,
                  "high_flow": False, "tracks_or_tires": "tracks",
                  "undercarriage_condition": "good", "aux_hydraulics": True},
        "condition": {"runs": True, "stated_issues": [], "recent_service": ["new tracks"],
                      "damage_notes": None, "one_owner_claim": True},
        "deal": {"attachments": ["bucket", "forks"], "seller_type": "private",
                 "financing_or_trade": False, "price_vs_market_note": "at market"},
        "verdict": "match", "confidence": 0.92,
        "reasoning": "2,400 hours is under the 3,000 limit and 2-speed is confirmed.",
        "unknowns": [],
    }
    extraction = Extraction.model_validate(payload)
    assert extraction.core.engine_hours == 2400
    assert extraction.verdict == "match"
    assert extraction.specs.two_speed is True


def test_parses_a_response_full_of_nulls():
    """The common real-world case: a three-line listing that states almost
    nothing. Every field must accept null rather than failing validation."""
    payload = {
        "core": {"year": None, "make_model": "Bobcat T770", "engine_hours": None,
                 "asking_price": 38000, "location": None},
        "specs": {"cab_enclosed": None, "has_ac": None, "two_speed": None,
                  "high_flow": None, "tracks_or_tires": None,
                  "undercarriage_condition": None, "aux_hydraulics": None},
        "condition": {"runs": None, "stated_issues": [], "recent_service": [],
                      "damage_notes": None, "one_owner_claim": None},
        "deal": {"attachments": [], "seller_type": None,
                 "financing_or_trade": None, "price_vs_market_note": None},
        "verdict": "unverifiable", "confidence": 0.4,
        "reasoning": "Listing states no hours.",
        "unknowns": ["engine_hours", "two_speed"],
    }
    extraction = Extraction.model_validate(payload)
    assert extraction.verdict == "unverifiable"
    assert extraction.unknowns == ["engine_hours", "two_speed"]
