"""Typed configuration loaded from YAML.

Secrets are never stored here. The config names environment variables; the
values live in a gitignored .env file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or inconsistent."""


class AccountConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile_dir: Path


class LocationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    anchor: str
    radius_miles: int = Field(ge=1, le=500)


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    active_hours: str = "07:00-22:00"


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: str = "claude-opus-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    max_extractions_per_run: int = Field(default=25, ge=1)


class EmailConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    to: str
    from_: str = Field(alias="from")
    smtp_host: str
    smtp_port: int
    username: str
    password_env: str


class SmsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    to: str
    twilio_from: str
    account_sid_env: str
    auth_token_env: str


class NotificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    email: EmailConfig
    sms: SmsConfig


class SearchConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    query: str
    price_min_cents: int
    price_max_cents: int
    title_must_match: list[str] = []
    title_must_not_match: list[str] = []
    on_unknown: Literal["alert", "skip"] = "alert"
    criteria: str

    @field_validator("title_must_match", "title_must_not_match")
    @classmethod
    def _lowercase(cls, values: list[str]) -> list[str]:
        return [v.strip().lower() for v in values]


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    account: AccountConfig
    location: LocationConfig
    schedule: ScheduleConfig = ScheduleConfig()
    extraction: ExtractionConfig = ExtractionConfig()
    notifications: NotificationConfig
    searches: list[SearchConfig]


def _normalise_search(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert the human-friendly YAML shape into SearchConfig's field names."""
    out = dict(raw)
    price = out.pop("price", None) or {}
    if "min" not in price or "max" not in price:
        raise ConfigError(f"search '{out.get('name')}' needs price.min and price.max")
    out["price_min_cents"] = int(round(float(price["min"]) * 100))
    out["price_max_cents"] = int(round(float(price["max"]) * 100))
    return out


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config file must contain a YAML mapping at the top level")

    raw = dict(raw)
    raw["searches"] = [_normalise_search(s) for s in raw.get("searches") or []]
    if not raw["searches"]:
        raise ConfigError("config must define at least one search")

    names = [s["name"] for s in raw["searches"]]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ConfigError(f"duplicate search name(s): {', '.join(sorted(duplicates))}")

    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid config: {exc}") from exc
