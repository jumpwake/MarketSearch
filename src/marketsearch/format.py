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
