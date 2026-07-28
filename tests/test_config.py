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


def test_searches_on_unknown_defaults_to_alert(tmp_path: Path):
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


def test_max_listings_per_search_defaults_to_more_than_one_page(tmp_path: Path):
    """Facebook ships ~24 cards per screenful; the default must reach past it."""
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.extraction.max_listings_per_search >= 100


def test_max_listings_per_search_is_configurable(tmp_path: Path):
    body = MINIMAL + """
extraction:
  max_listings_per_search: 60
"""
    cfg = load_config(write(tmp_path, body))
    assert cfg.extraction.max_listings_per_search == 60


WATCHLIST = """
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
watchlists:
  - name: track-loaders
    criteria: "Under 3000 engine hours."
    exclude: ["Wanted", "s770"]
    queries: ["Bobcat T770", "Bobcat T86 track loader"]
    models:
      - {name: bobcat-t770, keywords: ["T770", "t-770"], price: {min: 15000, max: 53000}}
      - {name: bobcat-t750, keywords: ["t750"], price: {min: 15000, max: 50000}}
  - name: attachments
    criteria: "Skid steer root grapple."
    queries: ["skid steer root grapple"]
    models:
      - {name: root-grapple, keywords: ["grapple"], price: {min: 800, max: 6000}}
"""


def test_loads_watchlists(tmp_path: Path):
    cfg = load_config(write(tmp_path, WATCHLIST))
    assert [w.name for w in cfg.watchlists] == ["track-loaders", "attachments"]
    assert cfg.watchlists[0].queries == ["Bobcat T770", "Bobcat T86 track loader"]
    assert [m.name for m in cfg.watchlists[0].models] == ["bobcat-t770", "bobcat-t750"]


def test_model_prices_convert_dollars_to_cents(tmp_path: Path):
    cfg = load_config(write(tmp_path, WATCHLIST))
    model = cfg.watchlists[0].models[0]
    assert model.price_min_cents == 1_500_000
    assert model.price_max_cents == 5_300_000


def test_keywords_and_exclude_are_lowercased(tmp_path: Path):
    cfg = load_config(write(tmp_path, WATCHLIST))
    assert cfg.watchlists[0].models[0].keywords == ["t770", "t-770"]
    assert cfg.watchlists[0].exclude == ["wanted", "s770"]


def test_watchlist_on_unknown_defaults_to_alert(tmp_path: Path):
    cfg = load_config(write(tmp_path, WATCHLIST))
    assert cfg.watchlists[0].on_unknown == "alert"


def test_duplicate_watchlist_names_rejected(tmp_path: Path):
    body = WATCHLIST.replace("name: attachments", "name: track-loaders")
    with pytest.raises(ConfigError, match="duplicate watchlist name"):
        load_config(write(tmp_path, body))


def test_duplicate_model_names_rejected_across_watchlists(tmp_path: Path):
    body = WATCHLIST.replace("name: root-grapple", "name: bobcat-t770")
    with pytest.raises(ConfigError, match="duplicate model name"):
        load_config(write(tmp_path, body))


def test_watchlist_needs_at_least_one_model(tmp_path: Path):
    body = WATCHLIST.replace(
        '      - {name: root-grapple, keywords: ["grapple"], price: {min: 800, max: 6000}}',
        "",
    )
    with pytest.raises(ConfigError, match="at least one model"):
        load_config(write(tmp_path, body))


def test_watchlist_needs_at_least_one_query(tmp_path: Path):
    body = WATCHLIST.replace('queries: ["skid steer root grapple"]', "queries: []")
    with pytest.raises(ConfigError, match="at least one query"):
        load_config(write(tmp_path, body))


def test_model_needs_a_price_band(tmp_path: Path):
    body = WATCHLIST.replace(', price: {min: 800, max: 6000}', "")
    with pytest.raises(ConfigError, match="price.min and price.max"):
        load_config(write(tmp_path, body))


def test_legacy_searches_config_still_loads(tmp_path: Path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert len(cfg.searches) == 1
    assert cfg.watchlists == []
