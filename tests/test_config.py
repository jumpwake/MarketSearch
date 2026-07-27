from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from marketsearch.config import ConfigError, load_config

MINIMAL = """
account:
  profile_dir: C:\\MarketSearch\\chrome-profile
location:
  anchor: "Kansas City, MO"
  radius_miles: 250
notifications:
  email:
    to: me@example.com
    from: bot@example.com
    smtp_host: smtp.gmail.com
    smtp_port: 587
    username: bot@example.com
    password_env: MARKETSEARCH_SMTP_PASSWORD
  sms:
    to: "+15555550100"
    twilio_from: "+15555550101"
    account_sid_env: TWILIO_ACCOUNT_SID
    auth_token_env: TWILIO_AUTH_TOKEN
searches:
  - name: bobcat-t770
    query: "Bobcat T770"
    price: {min: 15000, max: 60000}
    title_must_match: ["t770"]
    title_must_not_match: ["wanted"]
    criteria: |
      Under 3000 engine hours.
"""


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_loads_minimal_config(tmp_path: Path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.location.radius_miles == 250
    assert len(cfg.searches) == 1
    assert cfg.searches[0].name == "bobcat-t770"


def test_prices_convert_dollars_to_cents(tmp_path: Path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.searches[0].price_min_cents == 1_500_000
    assert cfg.searches[0].price_max_cents == 6_000_000


def test_notifications_disabled_by_default(tmp_path: Path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.notifications.enabled is False


def test_extraction_defaults_to_opus_5_low_effort(tmp_path: Path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.extraction.model == "claude-opus-5"
    assert cfg.extraction.effort == "low"
    assert cfg.extraction.max_extractions_per_run == 25


def test_title_matchers_are_lowercased(tmp_path: Path):
    cfg = load_config(write(tmp_path, MINIMAL.replace('["t770"]', '["T770"]')))
    assert cfg.searches[0].title_must_match == ["t770"]


def test_on_unknown_defaults_to_alert(tmp_path: Path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.searches[0].on_unknown == "alert"


def test_duplicate_search_names_rejected(tmp_path: Path):
    body = MINIMAL + """
  - name: bobcat-t770
    query: "Bobcat T770 cab"
    price: {min: 1, max: 2}
    criteria: "anything"
"""
    with pytest.raises(ConfigError, match="duplicate search name"):
        load_config(write(tmp_path, body))


def test_missing_file_raises_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_shape_raises_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="radius_miles"):
        load_config(write(tmp_path, MINIMAL.replace("radius_miles: 250", "radius_miles: many")))
