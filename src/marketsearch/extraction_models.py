"""The structured shape Claude must return for every listing.

Two rules govern this file, both enforced by tests:
  1. Every model sets extra="forbid" (emits additionalProperties: false).
  2. No field has a default. A defaulted field is dropped from `required`,
     which the structured-output API rejects. Optional values are `X | None`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class CoreFacts(BaseModel):
    model_config = _STRICT

    year: int | None = Field(description="Model year, if stated.")
    make_model: str | None = Field(description="Manufacturer and model, e.g. 'Bobcat T770'.")
    engine_hours: int | None = Field(
        description="Engine hours as a number. Null if the listing does not state them. "
                    "Do not estimate or infer hours from age or condition."
    )
    asking_price: int | None = Field(description="Asking price in whole dollars.")
    location: str | None = Field(description="City and state as stated in the listing.")


class MachineSpecs(BaseModel):
    model_config = _STRICT

    cab_enclosed: bool | None
    has_ac: bool | None
    two_speed: bool | None
    high_flow: bool | None
    tracks_or_tires: Literal["tracks", "tires"] | None
    undercarriage_condition: str | None = Field(
        description="Short quote or paraphrase of any statement about undercarriage "
                    "or tire wear. Null if not mentioned."
    )
    aux_hydraulics: bool | None


class ConditionHistory(BaseModel):
    model_config = _STRICT

    runs: bool | None = Field(description="Whether the listing says it runs and operates.")
    stated_issues: list[str] = Field(
        description="Mechanical problems the seller explicitly mentions. Empty if none."
    )
    recent_service: list[str] = Field(
        description="Repairs or maintenance the seller says was recently done."
    )
    damage_notes: str | None
    one_owner_claim: bool | None


class DealContext(BaseModel):
    model_config = _STRICT

    attachments: list[str] = Field(
        description="Attachments included in the sale, e.g. bucket, forks, auger."
    )
    seller_type: Literal["private", "dealer"] | None
    financing_or_trade: bool | None
    price_vs_market_note: str | None = Field(
        description="One short clause on whether the price looks high, low, or fair "
                    "for the stated hours and condition. Null if there is too little "
                    "information to say."
    )


class Extraction(BaseModel):
    model_config = _STRICT

    core: CoreFacts
    specs: MachineSpecs
    condition: ConditionHistory
    deal: DealContext

    verdict: Literal["match", "no_match", "unverifiable"] = Field(
        description="'match' if every criterion is satisfied. 'no_match' if any "
                    "criterion is clearly violated. 'unverifiable' if nothing is "
                    "violated but a criterion cannot be checked from the listing text."
    )
    confidence: float = Field(description="0.0 to 1.0.")
    reasoning: str = Field(
        description="Two sentences at most, citing the specific listing text that "
                    "drove the verdict."
    )
    unknowns: list[str] = Field(
        description="Field names that a criterion depends on but the listing does "
                    "not state. Empty when the verdict is not 'unverifiable'."
    )


EXTRACTION_JSON_SCHEMA: dict = Extraction.model_json_schema()
