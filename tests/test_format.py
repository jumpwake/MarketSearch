from __future__ import annotations

from marketsearch.format import attribute_rows, dollars, hours_text, yes_no

ATTRS = {
    "core": {"year": 2021, "engine_hours": 2200},
    "specs": {"cab_enclosed": True, "has_ac": False, "two_speed": True,
              "high_flow": True, "undercarriage_condition": "good"},
    "condition": {"runs": True, "stated_issues": ["ac intermittent"]},
    "deal": {"attachments": ["bucket"], "seller_type": "private"},
}


def test_dollars_formats_cents_with_separators():
    assert dollars(3_950_000) == "$39,500"


def test_dollars_renders_a_dash_for_missing_price():
    assert dollars(None) == "—"


def test_yes_no_maps_booleans_and_passes_through_strings():
    assert yes_no(True) == "yes"
    assert yes_no(False) == "no"
    assert yes_no(None) == "—"
    assert yes_no("good") == "good"


def test_hours_text_formats_with_a_separator():
    assert hours_text(ATTRS) == "2,200"


def test_hours_text_handles_a_missing_value():
    assert hours_text({"core": {}}) == "—"


def test_attribute_rows_flattens_every_section():
    rows = dict(attribute_rows(ATTRS))
    assert rows["Hours"] == "2,200"
    assert rows["Year"] == "2021"
    assert rows["Cab"] == "yes"
    assert rows["A/C"] == "no"
    assert rows["High flow"] == "yes"
    assert rows["Undercarriage"] == "good"
    assert rows["Issues"] == "ac intermittent"
    assert rows["Seller"] == "private"


def test_attribute_rows_says_none_stated_for_empty_lists():
    rows = dict(attribute_rows({"core": {}, "specs": {}, "condition": {},
                                "deal": {}}))
    assert rows["Issues"] == "none stated"
    assert rows["Attachments"] == "none stated"
