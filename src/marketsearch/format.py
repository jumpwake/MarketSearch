"""Presentation formatters shared by the email and dashboard renderers.

These live outside `notify/` because the dashboard is not a notification.
Keeping one copy is what stops the two renderers describing the same listing
differently.
"""

from __future__ import annotations


def dollars(cents: int | None) -> str:
    return "—" if cents is None else f"${cents / 100:,.0f}"


def yes_no(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def hours_text(attributes: dict) -> str:
    hours = attributes.get("core", {}).get("engine_hours")
    return "—" if hours is None else f"{hours:,}"


def attribute_rows(attributes: dict) -> list[tuple[str, str]]:
    core = attributes.get("core", {})
    specs = attributes.get("specs", {})
    condition = attributes.get("condition", {})
    deal = attributes.get("deal", {})
    return [
        ("Hours", hours_text(attributes)),
        ("Year", yes_no(core.get("year"))),
        ("Cab", yes_no(specs.get("cab_enclosed"))),
        ("A/C", yes_no(specs.get("has_ac"))),
        ("2-speed", yes_no(specs.get("two_speed"))),
        ("High flow", yes_no(specs.get("high_flow"))),
        ("Undercarriage", yes_no(specs.get("undercarriage_condition"))),
        ("Runs", yes_no(condition.get("runs"))),
        ("Issues", ", ".join(condition.get("stated_issues") or []) or "none stated"),
        ("Attachments", ", ".join(deal.get("attachments") or []) or "none stated"),
        ("Seller", yes_no(deal.get("seller_type"))),
    ]


# Marketplace writes locations as "Cameron, Missouri". Space on a ranked row is
# tight, and "Cameron, MO" carries the same information in half the width.
_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


def short_location(location: str | None) -> str:
    """'Cameron, Missouri' -> 'Cameron, MO'.

    Anything that does not end in a recognised state name is returned
    unchanged: a location the tool cannot parse is still worth showing, and
    guessing at it would be worse than leaving it alone.
    """
    if not location:
        return ""
    city, _, state = location.rpartition(",")
    if not city:
        return location
    abbreviation = _STATES.get(state.strip().lower())
    return f"{city.strip()}, {abbreviation}" if abbreviation else location
