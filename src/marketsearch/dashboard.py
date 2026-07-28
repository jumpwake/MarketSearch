"""The results dashboard: judged listings in, one HTML page out.

Everything here is pure. The renderer never reads a file, a clock, or the
database — the CLI does that and passes the results in — which is what lets
every test run without a browser or a database.
"""

from __future__ import annotations

from dataclasses import dataclass

from marketsearch.models import ListingDetail
from marketsearch.store import ExtractionRow, ListingRow

# Compact track loaders work harder than skid steers, and major undercarriage
# and final-drive costs typically land well before 10,000 hours. This figure is
# a user-facing control, not a constant: it inverts the ranking rather than
# merely adjusting it (see tests).
DEFAULT_LIFE_HOURS = 6000


@dataclass(frozen=True)
class JudgedListing:
    listing: ListingRow
    detail: ListingDetail
    extraction: ExtractionRow


@dataclass(frozen=True)
class Pick:
    row: JudgedListing
    hours: int | None
    value_per_hour: float | None


def engine_hours(extraction: ExtractionRow) -> int | None:
    hours = extraction.attributes.get("core", {}).get("engine_hours")
    return hours if isinstance(hours, int) else None


def value_per_remaining_hour(
    price_cents: int | None, hours: int | None, life_hours: int
) -> float | None:
    """Dollars per hour of life left in the machine.

    Interpretable in real units and stable as the corpus grows, unlike a
    normalised composite score. Returns None when it cannot be computed, so
    callers can sort those last instead of pretending they scored zero.
    """
    if price_cents is None or hours is None:
        return None
    remaining = max(life_hours - hours, 1)
    return (price_cents / 100) / remaining


def top_picks(
    rows: list[JudgedListing],
    live_models: set[str],
    life_hours: int,
    limit: int = 10,
) -> list[Pick]:
    """Best value among matches from models still in the config.

    Retired models are excluded: presenting a disabled search's matches as
    things to go buy would be wrong. They stay visible in the browse list.
    """
    picks: list[Pick] = []
    for row in rows:
        if row.extraction.verdict != "match":
            continue
        if row.listing.model_name not in live_models:
            continue
        hours = engine_hours(row.extraction)
        picks.append(
            Pick(
                row=row,
                hours=hours,
                value_per_hour=value_per_remaining_hour(
                    row.listing.price_cents, hours, life_hours
                ),
            )
        )

    # None sorts last: unknown hours must never rank as the best value.
    picks.sort(key=lambda p: (p.value_per_hour is None, p.value_per_hour or 0.0))
    return picks[:limit]
