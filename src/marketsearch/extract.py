"""Turn a listing's free text into structured attributes and a verdict."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from marketsearch.extraction_models import EXTRACTION_JSON_SCHEMA, Extraction
from marketsearch.models import ListingDetail, RawListing

MAX_TOKENS = 8000

# Dollars per million tokens: (input, output).
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

SYSTEM_PROMPT = """\
You evaluate used heavy-equipment listings from Facebook Marketplace against a \
buyer's criteria.

Rules:
- Report only what the listing states. Never estimate, infer, or fill in a \
plausible value. If the listing does not say, the field is null.
- Engine hours in particular: sellers write them many ways ("2400 hrs", "2.4k \
hours", "twenty four hundred hours", "2,400 on the meter"). Read all of them, \
but if hours appear nowhere, engine_hours is null and "engine_hours" belongs \
in unknowns.
- Verdict 'no_match' means a criterion is clearly violated by something the \
listing says.
- Verdict 'unverifiable' means nothing is violated, but a criterion depends on \
information the listing does not provide. This is common and useful — do not \
force it to 'no_match'.
- Verdict 'match' requires that every criterion is satisfied by stated \
information.
- Keep reasoning to two sentences, quoting the listing text that decided it.
"""


class _MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _Client(Protocol):
    messages: _MessagesAPI


class ExtractionError(Exception):
    """The model could not produce a usable extraction for this listing."""


@dataclass(frozen=True)
class ExtractionResult:
    extraction: Extraction
    input_tokens: int
    output_tokens: int
    cost_cents: float


def build_prompt(listing: RawListing, detail: ListingDetail, criteria: str) -> str:
    price = (
        f"${listing.price_cents / 100:,.0f}"
        if listing.price_cents is not None
        else "not stated"
    )
    distance = (
        f"{detail.distance_miles:.0f} miles away"
        if detail.distance_miles is not None
        else "distance not stated"
    )
    fields = json.dumps(detail.structured_fields, indent=2, sort_keys=True)

    return f"""\
The buyer is looking for equipment meeting these criteria:

<criteria>
{criteria.strip()}
</criteria>

Here is the listing.

<listing>
Title: {listing.title}
Asking price: {price}
Location: {listing.location or "not stated"} ({distance})

Description:
{detail.description.strip() or "(the seller wrote no description)"}

Structured fields Facebook exposed:
{fields}
</listing>

Extract the attributes and decide the verdict."""


def _cost_cents(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _PRICES.get(model)
    if prices is None:
        return 0.0
    in_price, out_price = prices
    dollars = (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price
    return dollars * 100


def _first_text_block(response: Any) -> str:
    """Opus 5 runs thinking by default, so the text block is not always first."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ExtractionError("model response contained no text block")


class Extractor:
    def __init__(self, client: _Client, model: str, effort: str) -> None:
        self._client = client
        self._model = model
        self._effort = effort

    def extract(
        self, listing: RawListing, detail: ListingDetail, criteria: str
    ) -> ExtractionResult:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            output_config={
                "format": {"type": "json_schema", "schema": EXTRACTION_JSON_SCHEMA},
                "effort": self._effort,
            },
            messages=[{"role": "user", "content": build_prompt(listing, detail, criteria)}],
        )

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            raise ExtractionError(f"model refused listing {listing.listing_id}")
        if stop_reason == "max_tokens":
            raise ExtractionError(
                f"response truncated for listing {listing.listing_id}; raise MAX_TOKENS"
            )

        text = _first_text_block(response)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError(
                f"model did not return valid JSON for listing {listing.listing_id}"
            ) from exc

        try:
            extraction = Extraction.model_validate(payload)
        except ValidationError as exc:
            raise ExtractionError(
                f"model output did not match the schema for listing "
                f"{listing.listing_id}: {exc}"
            ) from exc

        return ExtractionResult(
            extraction=extraction,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_cents=_cost_cents(
                self._model, response.usage.input_tokens, response.usage.output_tokens
            ),
        )
