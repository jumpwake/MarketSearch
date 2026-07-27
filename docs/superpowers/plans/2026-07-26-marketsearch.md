# MarketSearch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a scheduled tool that watches Facebook Marketplace for specific heavy equipment, judges each new listing against plain-English criteria using Claude, and alerts by email and SMS only on genuine matches.

**Architecture:** A short-lived Python process invoked by Windows Task Scheduler. Each run drives a persistent logged-in Chrome profile via Playwright to search Marketplace, dedupes against a SQLite ledger, applies free deterministic prefilters, fetches detail pages only for survivors, extracts structured attributes with the Anthropic SDK, and emits one batched email plus one SMS. All Facebook-specific knowledge is quarantined behind a `ListingSource` interface so the fragile scraping layer can be replaced or repaired without touching anything downstream.

**Tech Stack:** Python 3.12, Playwright (Chromium/Chrome channel), SQLite (stdlib `sqlite3`), Pydantic v2, Anthropic SDK, Typer, Jinja2, Twilio, pytest.

**Spec:** `docs/superpowers/specs/2026-07-26-marketsearch-design.md`

## Global Constraints

- **Python 3.12+.** Use `from __future__ import annotations` in every module.
- **Model:** `claude-opus-5` with `output_config={"effort": "low"}`. Never hardcode a different model — it comes from config, and `claude-opus-5` is the default value.
- **Structured output:** use `client.messages.create()` with `output_config={"format": {"type": "json_schema", "schema": ...}, "effort": ...}`. Do not use the deprecated top-level `output_format` parameter on `create()`.
- **Every Pydantic model used in an extraction schema** must set `model_config = ConfigDict(extra="forbid")` and declare **all fields without defaults** (use `X | None` for optional values). The API requires `additionalProperties: false` and every property listed in `required`.
- **Secrets never appear in `config.yaml`.** Config holds env-var *names*; values come from a gitignored `.env` loaded via `python-dotenv`.
- **`notifications.enabled` defaults to `False`.** Nothing may send unless it is explicitly `True`.
- **Target OS is Windows 11.** Use `pathlib.Path` everywhere; never assume POSIX paths or a POSIX shell.
- **All timestamps stored in SQLite are UTC ISO-8601 strings** produced by `datetime.now(timezone.utc).isoformat()`.
- **No network calls in unit tests.** Facebook access, the Anthropic API, SMTP, and Twilio are all stubbed. The only exceptions are the two explicitly-marked test groups (`@pytest.mark.live_api`, `@pytest.mark.live_fb`), which are deselected by default.
- **Commit after every task**, using Conventional Commit prefixes (`feat:`, `test:`, `chore:`, `docs:`).

---

## File Structure

```
pyproject.toml                       # deps, pytest config, entry point
.gitignore                           # .env, *.db, chrome-profile/, debug/
.env.example                         # names of every required secret
config.example.yaml                  # fully-populated example config
README.md                            # setup + rollout instructions

src/marketsearch/
  __init__.py
  models.py            # RawListing, ListingDetail — the source-agnostic vocabulary
  config.py            # Pydantic config schema + YAML loader + env resolution
  fingerprint.py       # relist-suppression hashing (pure)
  store.py             # SQLite schema and repository
  prefilter.py         # deterministic keep/drop rules (pure)
  extraction_models.py # Pydantic models describing the extraction schema
  extract.py           # Anthropic call: ListingDetail + criteria -> Extraction
  sources/
    __init__.py
    base.py            # ListingSource protocol + exceptions
    parse.py           # pure functions over Facebook page HTML (fixture-tested)
    facebook.py        # Playwright driver implementing ListingSource
  notify/
    __init__.py
    render.py          # Jinja2 -> HTML email body + inline photo attachments
    delivery.py        # SMTP + Twilio senders, notification idempotency
  runstate.py          # lockfile, needs_login flag, operational alert throttling
  pipeline.py          # orchestration: scan, watched sync, dispatch
  cli.py               # typer app: run, login, test-search, history, preview, replay
  logging_setup.py     # rotating file logger

tests/
  conftest.py
  fixtures/
    pages/             # saved Marketplace HTML (search, item, saved)
    listings/          # golden-file extraction cases (JSON)
  test_config.py
  test_fingerprint.py
  test_store.py
  test_prefilter.py
  test_parse.py
  test_extract.py
  test_render.py
  test_delivery.py
  test_runstate.py
  test_pipeline_scan.py
  test_pipeline_watched.py
  test_cli.py
```

**Why these boundaries:** `sources/parse.py` is split from `sources/facebook.py` so that every parsing rule is testable against saved HTML with no browser involved — this is the module that will break when Facebook changes, and it needs the fastest possible test cycle. `notify/render.py` is split from `notify/delivery.py` so email content can be snapshot-tested without an SMTP server. `runstate.py` holds the three pieces of operational state (lock, login flag, alert throttle) that would otherwise be scattered across the pipeline.

---

## Task 1: Project scaffolding and domain models

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/marketsearch/__init__.py`, `src/marketsearch/models.py`, `tests/conftest.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces: `RawListing`, `ListingDetail` (both `pydantic.BaseModel`, frozen). Every later task uses these as the source-agnostic listing vocabulary.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "marketsearch"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "playwright>=1.44",
    "anthropic>=0.40",
    "jinja2>=3.1",
    "twilio>=9.0",
    "typer>=0.12",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14"]

[project.scripts]
marketsearch = "marketsearch.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/marketsearch"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "live_api: hits the real Anthropic API (costs money)",
    "live_fb: hits real Facebook (detection risk)",
]
addopts = "-m 'not live_api and not live_fb'"
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
.env
*.db
*.db-journal
chrome-profile/
debug/
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
```

- [ ] **Step 3: Create `.env.example`**

```bash
# Copy to .env and fill in. .env is gitignored and must never be committed.
ANTHROPIC_API_KEY=
MARKETSEARCH_SMTP_PASSWORD=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_models.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from marketsearch.models import ListingDetail, RawListing


def test_raw_listing_requires_id_title_url():
    listing = RawListing(
        listing_id="123",
        title="Bobcat T770",
        price_cents=3_800_000,
        location="Kansas City, MO",
        url="https://www.facebook.com/marketplace/item/123/",
        thumbnail_url=None,
        seller_name=None,
    )
    assert listing.listing_id == "123"
    assert listing.price_cents == 3_800_000


def test_raw_listing_is_frozen():
    listing = RawListing(
        listing_id="123",
        title="Bobcat T770",
        price_cents=None,
        location=None,
        url="https://example.com/1",
        thumbnail_url=None,
        seller_name=None,
    )
    with pytest.raises(ValidationError):
        listing.title = "changed"


def test_listing_detail_defaults_photo_list_empty():
    detail = ListingDetail(
        listing_id="123",
        description="Runs great, 2400 hours",
        structured_fields={},
        photo_urls=[],
        distance_miles=None,
    )
    assert detail.photo_urls == []
    assert detail.description.startswith("Runs")
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch'`

- [ ] **Step 6: Create the package and models**

Create `src/marketsearch/__init__.py` (empty file).

Create `src/marketsearch/models.py`:

```python
"""Source-agnostic listing vocabulary.

Everything downstream of `sources/` speaks only these two types. They contain
no Facebook-specific concepts, which is what allows the scraping layer to be
repaired or replaced without touching the rest of the system.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RawListing(BaseModel):
    """A listing as it appears in search results: cheap fields only."""

    model_config = ConfigDict(frozen=True)

    listing_id: str
    title: str
    price_cents: int | None
    location: str | None
    url: str
    thumbnail_url: str | None
    seller_name: str | None


class ListingDetail(BaseModel):
    """The contents of a listing's own page."""

    model_config = ConfigDict(frozen=True)

    listing_id: str
    description: str
    structured_fields: dict[str, object]
    photo_urls: list[str]
    distance_miles: float | None
```

- [ ] **Step 7: Create `tests/conftest.py`**

```python
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
```

- [ ] **Step 8: Install and run the tests**

Run:
```bash
pip install -e ".[dev]"
pytest tests/test_models.py -v
```
Expected: 3 passed

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .gitignore .env.example src/marketsearch tests
git commit -m "feat: scaffold marketsearch package with domain models"
```

---

## Task 2: Configuration schema and loading

**Files:**
- Create: `src/marketsearch/config.py`, `config.example.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `load_config(path: Path) -> Config`
  - `Config` with attributes `.account: AccountConfig`, `.location: LocationConfig`, `.schedule: ScheduleConfig`, `.extraction: ExtractionConfig`, `.notifications: NotificationConfig`, `.searches: list[SearchConfig]`
  - `SearchConfig` with `.name`, `.query`, `.price_min_cents`, `.price_max_cents`, `.title_must_match: list[str]`, `.title_must_not_match: list[str]`, `.on_unknown: Literal["alert","skip"]`, `.criteria: str`
  - `ConfigError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.config'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/config.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: 9 passed

- [ ] **Step 5: Create `config.example.yaml`**

```yaml
account:
  # Persistent Chrome profile. Created on first `marketsearch login`.
  profile_dir: C:\MarketSearch\chrome-profile

location:
  # EXAMPLE — replace with your actual anchor city.
  anchor: "Kansas City, MO"
  radius_miles: 250

schedule:
  # Informational only; the real cadence is set in Windows Task Scheduler.
  active_hours: "07:00-22:00"

extraction:
  model: claude-opus-5
  effort: low
  max_extractions_per_run: 25

notifications:
  # Ships false. Flip to true only after the shakedown described in the README.
  enabled: false
  email:
    to: you@example.com
    from: marketsearch@example.com
    smtp_host: smtp.gmail.com
    smtp_port: 587
    username: marketsearch@example.com
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
    title_must_not_match: ["wanted", "parts only", "looking for", "rental"]
    on_unknown: alert
    criteria: |
      Under 3000 engine hours.
      2-speed required. Enclosed cab with A/C strongly preferred.
      Reject anything describing major engine or hydraulic problems,
      or an undercarriage described as worn out or needing replacement.

  - name: bobcat-t300
    query: "Bobcat T300"
    price: {min: 10000, max: 40000}
    title_must_match: ["t300"]
    title_must_not_match: ["wanted", "parts only", "looking for"]
    on_unknown: alert
    criteria: |
      Under 3000 engine hours. Must run and drive.
      Prefer enclosed cab. Reject listings mentioning blown engines,
      seized hydraulics, or missing major components.
```

- [ ] **Step 6: Commit**

```bash
git add src/marketsearch/config.py config.example.yaml tests/test_config.py
git commit -m "feat: typed YAML configuration with env-var secret references"
```

---

## Task 3: Relist fingerprinting

**Files:**
- Create: `src/marketsearch/fingerprint.py`
- Test: `tests/test_fingerprint.py`

**Interfaces:**
- Consumes: nothing
- Produces: `fingerprint(title: str, price_cents: int | None, seller_name: str | None, location: str | None) -> str` — returns a 32-char hex digest. Task 4 stores it; Task 12 queries it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fingerprint.py`:

```python
from __future__ import annotations

from marketsearch.fingerprint import fingerprint


def test_identical_inputs_produce_identical_digest():
    a = fingerprint("2019 Bobcat T770", 3_800_000, "Dale S", "Olathe, KS")
    b = fingerprint("2019 Bobcat T770", 3_800_000, "Dale S", "Olathe, KS")
    assert a == b


def test_digest_is_32_hex_chars():
    digest = fingerprint("x", None, None, None)
    assert len(digest) == 32
    assert all(c in "0123456789abcdef" for c in digest)


def test_case_and_whitespace_are_normalised():
    a = fingerprint("2019 BOBCAT   T770", 3_800_000, "Dale S", "Olathe, KS")
    b = fingerprint("  2019 bobcat t770 ", 3_800_000, "dale s", "olathe, ks")
    assert a == b


def test_punctuation_is_ignored():
    a = fingerprint("2019 Bobcat T-770!!", 3_800_000, None, None)
    b = fingerprint("2019 Bobcat T 770", 3_800_000, None, None)
    assert a == b


def test_price_change_produces_different_digest():
    """A repost at a lower price is news and must not be suppressed."""
    a = fingerprint("2019 Bobcat T770", 4_100_000, "Dale S", "Olathe, KS")
    b = fingerprint("2019 Bobcat T770", 3_800_000, "Dale S", "Olathe, KS")
    assert a != b


def test_different_seller_produces_different_digest():
    a = fingerprint("2019 Bobcat T770", 3_800_000, "Dale S", "Olathe, KS")
    b = fingerprint("2019 Bobcat T770", 3_800_000, "Rita M", "Olathe, KS")
    assert a != b


def test_missing_fields_are_stable_not_random():
    a = fingerprint("2019 Bobcat T770", None, None, None)
    b = fingerprint("2019 Bobcat T770", None, None, None)
    assert a == b
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_fingerprint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.fingerprint'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/fingerprint.py`:

```python
"""Content fingerprinting for relist suppression.

Sellers routinely delete and repost an unsold machine, and Facebook assigns a
brand-new listing id each time. Pure id-based dedupe would therefore re-alert
on the same Bobcat every few weeks.

Price is deliberately part of the digest: a repost at a lower price produces a
different fingerprint and *does* alert, because a price drop is news.
"""

from __future__ import annotations

import hashlib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise(value: str | None) -> str:
    if not value:
        return ""
    return _NON_ALNUM.sub(" ", value.lower()).strip()


def fingerprint(
    title: str,
    price_cents: int | None,
    seller_name: str | None,
    location: str | None,
) -> str:
    """Return a stable 32-char digest identifying this machine-at-this-price."""
    parts = [
        _normalise(title),
        "" if price_cents is None else str(price_cents),
        _normalise(seller_name),
        _normalise(location),
    ]
    joined = "\x1f".join(parts).encode("utf-8")
    return hashlib.blake2b(joined, digest_size=16).hexdigest()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_fingerprint.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/fingerprint.py tests/test_fingerprint.py
git commit -m "feat: content fingerprinting for relist suppression"
```

---

## Task 4: SQLite schema and the listing ledger

**Files:**
- Create: `src/marketsearch/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `RawListing` (Task 1)
- Produces:
  - `Store(path: Path)` with `.initialize()`, `.close()`, and context-manager support
  - `ListingRow` frozen dataclass: `listing_id`, `search_name`, `title`, `price_cents`, `location`, `url`, `thumbnail_url`, `seller_name`, `fingerprint`, `stage`, `reject_reason`, `watched`, `first_seen_at`, `last_seen_at`
  - `Store.known_listing_ids(ids: Iterable[str]) -> set[str]`
  - `Store.upsert_listing(listing: RawListing, search_name: str, fp: str) -> None`
  - `Store.set_stage(listing_id: str, stage: str, reject_reason: str | None = None) -> None`
  - `Store.get_listing(listing_id: str) -> ListingRow | None`
  - `Store.fingerprint_seen_before(fp: str, exclude_listing_id: str, within_days: int) -> bool`
  - `Store.update_price(listing_id: str, price_cents: int | None) -> None`

Valid `stage` values, used consistently by every later task: `"prefiltered_out"`, `"pending"`, `"extracted"`, `"matched"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_store.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketsearch.models import RawListing
from marketsearch.store import Store


def make_listing(listing_id: str = "1", price_cents: int | None = 3_800_000) -> RawListing:
    return RawListing(
        listing_id=listing_id,
        title="2019 Bobcat T770",
        price_cents=price_cents,
        location="Olathe, KS",
        url=f"https://www.facebook.com/marketplace/item/{listing_id}/",
        thumbnail_url="https://example.com/a.jpg",
        seller_name="Dale S",
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.initialize()
    yield s
    s.close()


def test_initialize_is_idempotent(tmp_path: Path):
    s = Store(tmp_path / "x.db")
    s.initialize()
    s.initialize()
    s.close()


def test_known_listing_ids_empty_on_fresh_db(store: Store):
    assert store.known_listing_ids(["1", "2"]) == set()


def test_upsert_then_known(store: Store):
    store.upsert_listing(make_listing("1"), "bobcat-t770", "fp1")
    assert store.known_listing_ids(["1", "2"]) == {"1"}


def test_known_listing_ids_handles_large_batches(store: Store):
    """Must not blow SQLite's 999-variable limit."""
    for i in range(1200):
        store.upsert_listing(make_listing(str(i)), "s", f"fp{i}")
    ids = [str(i) for i in range(1500)]
    assert len(store.known_listing_ids(ids)) == 1200


def test_get_listing_roundtrip(store: Store):
    store.upsert_listing(make_listing("1"), "bobcat-t770", "fp1")
    row = store.get_listing("1")
    assert row is not None
    assert row.title == "2019 Bobcat T770"
    assert row.price_cents == 3_800_000
    assert row.search_name == "bobcat-t770"
    assert row.fingerprint == "fp1"
    assert row.stage == "pending"
    assert row.watched is False


def test_get_listing_returns_none_when_absent(store: Store):
    assert store.get_listing("nope") is None


def test_upsert_preserves_first_seen_and_updates_last_seen(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp1")
    first = store.get_listing("1")
    store.upsert_listing(make_listing("1"), "s", "fp1")
    second = store.get_listing("1")
    assert second.first_seen_at == first.first_seen_at
    assert second.last_seen_at >= first.last_seen_at


def test_set_stage_records_reason(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp1")
    store.set_stage("1", "prefiltered_out", "price above maximum")
    row = store.get_listing("1")
    assert row.stage == "prefiltered_out"
    assert row.reject_reason == "price above maximum"


def test_update_price(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp1")
    store.update_price("1", 3_500_000)
    assert store.get_listing("1").price_cents == 3_500_000


def test_fingerprint_seen_before_ignores_the_listing_itself(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp-shared")
    assert store.fingerprint_seen_before("fp-shared", exclude_listing_id="1", within_days=60) is False


def test_fingerprint_seen_before_detects_a_repost(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp-shared")
    store.upsert_listing(make_listing("2"), "s", "fp-shared")
    assert store.fingerprint_seen_before("fp-shared", exclude_listing_id="2", within_days=60) is True


def test_fingerprint_outside_window_is_not_suppressed(store: Store):
    store.upsert_listing(make_listing("1"), "s", "fp-shared")
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    store._conn.execute("UPDATE listings SET first_seen_at = ? WHERE listing_id = '1'", (old,))
    store._conn.commit()
    store.upsert_listing(make_listing("2"), "s", "fp-shared")
    assert store.fingerprint_seen_before("fp-shared", exclude_listing_id="2", within_days=60) is False


def test_store_works_as_context_manager(tmp_path: Path):
    with Store(tmp_path / "cm.db") as s:
        s.initialize()
        s.upsert_listing(make_listing("1"), "s", "fp1")
        assert s.get_listing("1") is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.store'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/store.py`:

```python
"""SQLite persistence.

Every listing id ever seen is recorded, matched or not. That ledger is what
guarantees a listing is examined once and alerted on at most once. Nothing is
ever pruned — full history costs a few megabytes after years and is worth more
than the disk.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from marketsearch.models import RawListing

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS listings (
    listing_id     TEXT PRIMARY KEY,
    search_name    TEXT NOT NULL,
    title          TEXT NOT NULL,
    price_cents    INTEGER,
    location       TEXT,
    url            TEXT NOT NULL,
    thumbnail_url  TEXT,
    seller_name    TEXT,
    fingerprint    TEXT NOT NULL,
    stage          TEXT NOT NULL DEFAULT 'pending',
    reject_reason  TEXT,
    watched        INTEGER NOT NULL DEFAULT 0,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    last_change_check_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_fingerprint ON listings(fingerprint);
CREATE INDEX IF NOT EXISTS idx_listings_watched ON listings(watched);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ListingRow:
    listing_id: str
    search_name: str
    title: str
    price_cents: int | None
    location: str | None
    url: str
    thumbnail_url: str | None
    seller_name: str | None
    fingerprint: str
    stage: str
    reject_reason: str | None
    watched: bool
    first_seen_at: str
    last_seen_at: str
    last_change_check_at: str | None


def _row_to_listing(row: sqlite3.Row) -> ListingRow:
    return ListingRow(
        listing_id=row["listing_id"],
        search_name=row["search_name"],
        title=row["title"],
        price_cents=row["price_cents"],
        location=row["location"],
        url=row["url"],
        thumbnail_url=row["thumbnail_url"],
        seller_name=row["seller_name"],
        fingerprint=row["fingerprint"],
        stage=row["stage"],
        reject_reason=row["reject_reason"],
        watched=bool(row["watched"]),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        last_change_check_at=row["last_change_check_at"],
    )


class Store:
    """Owns the SQLite connection. One instance per process run."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def initialize(self) -> None:
        self._conn.executescript(_SCHEMA)
        cur = self._conn.execute("SELECT version FROM schema_version")
        if cur.fetchone() is None:
            self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        self._conn.commit()

    # ---- listing ledger -------------------------------------------------

    def known_listing_ids(self, ids: Iterable[str]) -> set[str]:
        """Return the subset of `ids` already present. Chunked to stay under
        SQLite's variable limit (default 999)."""
        ids = list(ids)
        found: set[str] = set()
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = self._conn.execute(
                f"SELECT listing_id FROM listings WHERE listing_id IN ({placeholders})",
                chunk,
            )
            found.update(r["listing_id"] for r in cur.fetchall())
        return found

    def upsert_listing(self, listing: RawListing, search_name: str, fp: str) -> None:
        now = utcnow()
        self._conn.execute(
            """
            INSERT INTO listings (listing_id, search_name, title, price_cents, location,
                                  url, thumbnail_url, seller_name, fingerprint,
                                  first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                title = excluded.title,
                price_cents = excluded.price_cents,
                location = excluded.location,
                thumbnail_url = excluded.thumbnail_url,
                seller_name = excluded.seller_name,
                last_seen_at = excluded.last_seen_at
            """,
            (
                listing.listing_id, search_name, listing.title, listing.price_cents,
                listing.location, listing.url, listing.thumbnail_url,
                listing.seller_name, fp, now, now,
            ),
        )
        self._conn.commit()

    def set_stage(self, listing_id: str, stage: str, reject_reason: str | None = None) -> None:
        self._conn.execute(
            "UPDATE listings SET stage = ?, reject_reason = ? WHERE listing_id = ?",
            (stage, reject_reason, listing_id),
        )
        self._conn.commit()

    def update_price(self, listing_id: str, price_cents: int | None) -> None:
        self._conn.execute(
            "UPDATE listings SET price_cents = ?, last_seen_at = ? WHERE listing_id = ?",
            (price_cents, utcnow(), listing_id),
        )
        self._conn.commit()

    def get_listing(self, listing_id: str) -> ListingRow | None:
        cur = self._conn.execute("SELECT * FROM listings WHERE listing_id = ?", (listing_id,))
        row = cur.fetchone()
        return _row_to_listing(row) if row else None

    def fingerprint_seen_before(
        self, fp: str, exclude_listing_id: str, within_days: int
    ) -> bool:
        """True if some *other* listing with this fingerprint was first seen
        inside the window — i.e. this is a repost we should not re-alert on."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
        cur = self._conn.execute(
            """
            SELECT 1 FROM listings
            WHERE fingerprint = ? AND listing_id != ? AND first_seen_at >= ?
            LIMIT 1
            """,
            (fp, exclude_listing_id, cutoff),
        )
        return cur.fetchone() is not None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_store.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/store.py tests/test_store.py
git commit -m "feat: sqlite schema and listing ledger with relist lookup"
```

---

## Task 5: Detail, extraction, notification, run, and state repositories

**Files:**
- Modify: `src/marketsearch/store.py` (extend `_SCHEMA`, add methods)
- Test: `tests/test_store_repos.py`

**Interfaces:**
- Consumes: `Store` (Task 4), `ListingDetail` (Task 1)
- Produces, all on `Store`:
  - `save_detail(detail: ListingDetail, content_hash: str) -> None`
  - `get_detail(listing_id: str) -> ListingDetail | None`
  - `get_detail_content_hash(listing_id: str) -> str | None`
  - `save_extraction(listing_id, attributes: dict, verdict: str, confidence: float, reasoning: str, unknowns: list[str], model: str, input_tokens: int, output_tokens: int, cost_cents: float) -> None`
  - `latest_extraction(listing_id: str) -> ExtractionRow | None`
  - `set_watched_ids(ids: set[str]) -> None` — makes the DB mirror Facebook's saved list exactly
  - `watched_listing_ids() -> set[str]`
  - `already_notified(listing_id: str, channel: str, kind: str) -> bool`
  - `record_notification(listing_id: str, channel: str, kind: str, status: str) -> None`
  - `start_run() -> int` / `finish_run(run_id: int, counters: dict[str, int]) -> None`
  - `get_state(key: str) -> str | None` / `set_state(key: str, value: str) -> None`
  - `ExtractionRow` frozen dataclass: `listing_id`, `attributes`, `verdict`, `confidence`, `reasoning`, `unknowns`, `model`, `created_at`

Valid `kind` values used across the system: `"match"`, `"unverified"`, `"price_change"`, `"removed"`. Valid `channel` values: `"email"`, `"sms"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_store_repos.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.models import ListingDetail, RawListing
from marketsearch.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.initialize()
    s.upsert_listing(
        RawListing(
            listing_id="1", title="2019 Bobcat T770", price_cents=3_800_000,
            location="Olathe, KS", url="https://example.com/1",
            thumbnail_url=None, seller_name="Dale S",
        ),
        "bobcat-t770", "fp1",
    )
    yield s
    s.close()


def detail(listing_id: str = "1", description: str = "2400 hours, runs great") -> ListingDetail:
    return ListingDetail(
        listing_id=listing_id, description=description,
        structured_fields={"condition": "used"},
        photo_urls=["https://example.com/a.jpg"], distance_miles=42.0,
    )


def test_detail_roundtrip(store: Store):
    store.save_detail(detail(), "hash-a")
    got = store.get_detail("1")
    assert got.description == "2400 hours, runs great"
    assert got.structured_fields == {"condition": "used"}
    assert got.photo_urls == ["https://example.com/a.jpg"]
    assert got.distance_miles == 42.0


def test_get_detail_returns_none_when_absent(store: Store):
    assert store.get_detail("nope") is None


def test_save_detail_overwrites_and_updates_hash(store: Store):
    store.save_detail(detail(), "hash-a")
    store.save_detail(detail(description="Now 2500 hours"), "hash-b")
    assert store.get_detail("1").description == "Now 2500 hours"
    assert store.get_detail_content_hash("1") == "hash-b"


def test_extraction_roundtrip(store: Store):
    store.save_extraction(
        listing_id="1", attributes={"core": {"engine_hours": 2400}},
        verdict="match", confidence=0.9, reasoning="Under 3000 hours, 2-speed confirmed",
        unknowns=[], model="claude-opus-5", input_tokens=1500,
        output_tokens=400, cost_cents=1.75,
    )
    row = store.latest_extraction("1")
    assert row.verdict == "match"
    assert row.attributes["core"]["engine_hours"] == 2400
    assert row.unknowns == []


def test_latest_extraction_returns_most_recent(store: Store):
    for verdict in ("unverifiable", "match"):
        store.save_extraction(
            listing_id="1", attributes={}, verdict=verdict, confidence=0.5,
            reasoning="r", unknowns=[], model="claude-opus-5",
            input_tokens=1, output_tokens=1, cost_cents=0.1,
        )
    assert store.latest_extraction("1").verdict == "match"


def test_set_watched_ids_mirrors_facebook_exactly(store: Store):
    store.upsert_listing(
        RawListing(listing_id="2", title="T300", price_cents=None, location=None,
                   url="https://example.com/2", thumbnail_url=None, seller_name=None),
        "bobcat-t300", "fp2",
    )
    store.set_watched_ids({"1", "2"})
    assert store.watched_listing_ids() == {"1", "2"}
    store.set_watched_ids({"2"})  # user un-saved listing 1 on Facebook
    assert store.watched_listing_ids() == {"2"}


def test_notification_idempotency(store: Store):
    assert store.already_notified("1", "email", "match") is False
    store.record_notification("1", "email", "match", "sent")
    assert store.already_notified("1", "email", "match") is True
    assert store.already_notified("1", "sms", "match") is False
    assert store.already_notified("1", "email", "price_change") is False


def test_failed_notification_is_not_treated_as_sent(store: Store):
    store.record_notification("1", "email", "match", "failed")
    assert store.already_notified("1", "email", "match") is False


def test_run_lifecycle(store: Store):
    run_id = store.start_run()
    assert isinstance(run_id, int)
    store.finish_run(run_id, {"found": 10, "new": 3, "matched": 1, "errors": 0})
    cur = store._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    row = cur.fetchone()
    assert row["ended_at"] is not None
    assert row["matched"] == 1


def test_state_kv(store: Store):
    assert store.get_state("needs_login") is None
    store.set_state("needs_login", "true")
    assert store.get_state("needs_login") == "true"
    store.set_state("needs_login", "false")
    assert store.get_state("needs_login") == "false"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_store_repos.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'save_detail'`

- [ ] **Step 3: Extend the schema**

In `src/marketsearch/store.py`, append to the `_SCHEMA` string (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS listing_details (
    listing_id        TEXT PRIMARY KEY REFERENCES listings(listing_id),
    description       TEXT NOT NULL,
    structured_fields TEXT NOT NULL,
    photo_urls        TEXT NOT NULL,
    distance_miles    REAL,
    content_hash      TEXT NOT NULL,
    fetched_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id    TEXT NOT NULL REFERENCES listings(listing_id),
    attributes    TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    confidence    REAL NOT NULL,
    reasoning     TEXT NOT NULL,
    unknowns      TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_cents    REAL NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_extractions_listing ON extractions(listing_id, id DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL,
    channel    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    status     TEXT NOT NULL,
    sent_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_lookup
    ON notifications(listing_id, channel, kind, status);

CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    found       INTEGER NOT NULL DEFAULT 0,
    new         INTEGER NOT NULL DEFAULT 0,
    prefiltered INTEGER NOT NULL DEFAULT 0,
    extracted   INTEGER NOT NULL DEFAULT 0,
    matched     INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

- [ ] **Step 4: Add the repository methods**

Append to `src/marketsearch/store.py` — first the imports and dataclass at module level:

```python
import json


@dataclass(frozen=True)
class ExtractionRow:
    listing_id: str
    attributes: dict
    verdict: str
    confidence: float
    reasoning: str
    unknowns: list[str]
    model: str
    created_at: str
```

Then these methods inside `class Store`:

```python
    # ---- listing details -----------------------------------------------

    def save_detail(self, detail: ListingDetail, content_hash: str) -> None:
        self._conn.execute(
            """
            INSERT INTO listing_details (listing_id, description, structured_fields,
                                         photo_urls, distance_miles, content_hash, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                description = excluded.description,
                structured_fields = excluded.structured_fields,
                photo_urls = excluded.photo_urls,
                distance_miles = excluded.distance_miles,
                content_hash = excluded.content_hash,
                fetched_at = excluded.fetched_at
            """,
            (
                detail.listing_id, detail.description,
                json.dumps(detail.structured_fields), json.dumps(detail.photo_urls),
                detail.distance_miles, content_hash, utcnow(),
            ),
        )
        self._conn.commit()

    def get_detail(self, listing_id: str) -> ListingDetail | None:
        cur = self._conn.execute(
            "SELECT * FROM listing_details WHERE listing_id = ?", (listing_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ListingDetail(
            listing_id=row["listing_id"],
            description=row["description"],
            structured_fields=json.loads(row["structured_fields"]),
            photo_urls=json.loads(row["photo_urls"]),
            distance_miles=row["distance_miles"],
        )

    def get_detail_content_hash(self, listing_id: str) -> str | None:
        cur = self._conn.execute(
            "SELECT content_hash FROM listing_details WHERE listing_id = ?", (listing_id,)
        )
        row = cur.fetchone()
        return row["content_hash"] if row else None

    # ---- extractions ----------------------------------------------------

    def save_extraction(
        self, listing_id: str, attributes: dict, verdict: str, confidence: float,
        reasoning: str, unknowns: list[str], model: str, input_tokens: int,
        output_tokens: int, cost_cents: float,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO extractions (listing_id, attributes, verdict, confidence, reasoning,
                                     unknowns, model, input_tokens, output_tokens,
                                     cost_cents, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing_id, json.dumps(attributes), verdict, confidence, reasoning,
                json.dumps(unknowns), model, input_tokens, output_tokens,
                cost_cents, utcnow(),
            ),
        )
        self._conn.commit()

    def latest_extraction(self, listing_id: str) -> ExtractionRow | None:
        cur = self._conn.execute(
            "SELECT * FROM extractions WHERE listing_id = ? ORDER BY id DESC LIMIT 1",
            (listing_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ExtractionRow(
            listing_id=row["listing_id"],
            attributes=json.loads(row["attributes"]),
            verdict=row["verdict"],
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            unknowns=json.loads(row["unknowns"]),
            model=row["model"],
            created_at=row["created_at"],
        )

    # ---- watched (mirrors Facebook's saved list) -------------------------

    def set_watched_ids(self, ids: set[str]) -> None:
        """Make the DB reflect Facebook's saved list exactly. Facebook is the
        source of truth, so un-saving there clears the flag here."""
        self._conn.execute("UPDATE listings SET watched = 0 WHERE watched = 1")
        for listing_id in ids:
            self._conn.execute(
                "UPDATE listings SET watched = 1 WHERE listing_id = ?", (listing_id,)
            )
        self._conn.commit()

    def watched_listing_ids(self) -> set[str]:
        cur = self._conn.execute("SELECT listing_id FROM listings WHERE watched = 1")
        return {r["listing_id"] for r in cur.fetchall()}

    # ---- notifications --------------------------------------------------

    def already_notified(self, listing_id: str, channel: str, kind: str) -> bool:
        cur = self._conn.execute(
            """
            SELECT 1 FROM notifications
            WHERE listing_id = ? AND channel = ? AND kind = ? AND status = 'sent'
            LIMIT 1
            """,
            (listing_id, channel, kind),
        )
        return cur.fetchone() is not None

    def record_notification(
        self, listing_id: str, channel: str, kind: str, status: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO notifications (listing_id, channel, kind, status, sent_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (listing_id, channel, kind, status, utcnow()),
        )
        self._conn.commit()

    # ---- runs ------------------------------------------------------------

    def start_run(self) -> int:
        cur = self._conn.execute("INSERT INTO runs (started_at) VALUES (?)", (utcnow(),))
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, counters: dict[str, int]) -> None:
        self._conn.execute(
            """
            UPDATE runs SET ended_at = ?, found = ?, new = ?, prefiltered = ?,
                            extracted = ?, matched = ?, errors = ?
            WHERE run_id = ?
            """,
            (
                utcnow(), counters.get("found", 0), counters.get("new", 0),
                counters.get("prefiltered", 0), counters.get("extracted", 0),
                counters.get("matched", 0), counters.get("errors", 0), run_id,
            ),
        )
        self._conn.commit()

    # ---- key/value state -------------------------------------------------

    def get_state(self, key: str) -> str | None:
        cur = self._conn.execute("SELECT value FROM app_state WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()
```

Also add `from marketsearch.models import ListingDetail, RawListing` to the existing import of `RawListing`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_store.py tests/test_store_repos.py -v`
Expected: 23 passed

- [ ] **Step 6: Commit**

```bash
git add src/marketsearch/store.py tests/test_store_repos.py
git commit -m "feat: detail, extraction, notification, run and state repositories"
```

---

## Task 6: Deterministic prefilter

**Files:**
- Create: `src/marketsearch/prefilter.py`
- Test: `tests/test_prefilter.py`

**Interfaces:**
- Consumes: `RawListing` (Task 1), `SearchConfig` (Task 2)
- Produces:
  - `PrefilterResult` frozen dataclass: `.keep: bool`, `.reason: str | None`
  - `prefilter(listing: RawListing, search: SearchConfig) -> PrefilterResult`

This runs before any detail page is fetched. Every listing it drops costs zero API calls and zero Facebook page loads for the rest of its life.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prefilter.py`:

```python
from __future__ import annotations

import pytest

from marketsearch.config import SearchConfig
from marketsearch.models import RawListing
from marketsearch.prefilter import prefilter


def search(**overrides) -> SearchConfig:
    base = dict(
        name="bobcat-t770",
        query="Bobcat T770",
        price_min_cents=1_500_000,
        price_max_cents=6_000_000,
        title_must_match=["t770"],
        title_must_not_match=["wanted", "parts only"],
        on_unknown="alert",
        criteria="Under 3000 hours.",
    )
    base.update(overrides)
    return SearchConfig(**base)


def listing(title: str = "2019 Bobcat T770", price_cents: int | None = 3_800_000) -> RawListing:
    return RawListing(
        listing_id="1", title=title, price_cents=price_cents, location=None,
        url="https://example.com/1", thumbnail_url=None, seller_name=None,
    )


def test_keeps_a_normal_match():
    result = prefilter(listing(), search())
    assert result.keep is True
    assert result.reason is None


def test_drops_title_missing_required_token():
    result = prefilter(listing(title="2019 Bobcat T650"), search())
    assert result.keep is False
    assert "t770" in result.reason


def test_title_matching_is_case_insensitive():
    assert prefilter(listing(title="BOBCAT T770 LOADER"), search()).keep is True


def test_drops_wanted_ads():
    result = prefilter(listing(title="WANTED: Bobcat T770"), search())
    assert result.keep is False
    assert "wanted" in result.reason


def test_drops_parts_listings():
    result = prefilter(listing(title="Bobcat T770 parts only"), search())
    assert result.keep is False
    assert "parts only" in result.reason


def test_exclusions_checked_before_inclusions():
    """A title matching both lists is rejected — exclusion wins."""
    result = prefilter(listing(title="Wanted Bobcat T770"), search())
    assert result.keep is False
    assert "wanted" in result.reason


def test_drops_price_above_maximum():
    result = prefilter(listing(price_cents=9_500_000), search())
    assert result.keep is False
    assert "above" in result.reason


def test_drops_price_below_minimum():
    result = prefilter(listing(price_cents=500_000), search())
    assert result.keep is False
    assert "below" in result.reason


def test_price_at_boundaries_is_kept():
    assert prefilter(listing(price_cents=1_500_000), search()).keep is True
    assert prefilter(listing(price_cents=6_000_000), search()).keep is True


def test_missing_price_is_kept_for_extraction_to_judge():
    """Marketplace occasionally omits price. Dropping those silently would
    discard exactly the listings worth a phone call."""
    result = prefilter(listing(price_cents=None), search())
    assert result.keep is True


def test_empty_matcher_lists_keep_everything():
    result = prefilter(
        listing(title="Some random skid steer"),
        search(title_must_match=[], title_must_not_match=[]),
    )
    assert result.keep is True


def test_all_required_tokens_must_be_present():
    result = prefilter(
        listing(title="Bobcat T770"),
        search(title_must_match=["t770", "cab"]),
    )
    assert result.keep is False
    assert "cab" in result.reason
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_prefilter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.prefilter'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/prefilter.py`:

```python
"""Free, deterministic gates applied before any detail page is fetched.

Ordering matters: exclusions are checked first because they are the most
decisive signal (a "wanted" ad is never interesting regardless of price), then
required tokens, then price.
"""

from __future__ import annotations

from dataclasses import dataclass

from marketsearch.config import SearchConfig
from marketsearch.models import RawListing


@dataclass(frozen=True)
class PrefilterResult:
    keep: bool
    reason: str | None


_KEEP = PrefilterResult(keep=True, reason=None)


def _drop(reason: str) -> PrefilterResult:
    return PrefilterResult(keep=False, reason=reason)


def prefilter(listing: RawListing, search: SearchConfig) -> PrefilterResult:
    title = listing.title.lower()

    for token in search.title_must_not_match:
        if token in title:
            return _drop(f"title contains excluded term '{token}'")

    for token in search.title_must_match:
        if token not in title:
            return _drop(f"title missing required term '{token}'")

    # A missing price is not a rejection. Marketplace sometimes omits it, and
    # those listings are disproportionately worth a look.
    if listing.price_cents is not None:
        if listing.price_cents < search.price_min_cents:
            return _drop(
                f"price ${listing.price_cents / 100:,.0f} below minimum "
                f"${search.price_min_cents / 100:,.0f}"
            )
        if listing.price_cents > search.price_max_cents:
            return _drop(
                f"price ${listing.price_cents / 100:,.0f} above maximum "
                f"${search.price_max_cents / 100:,.0f}"
            )

    return _KEEP
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_prefilter.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/prefilter.py tests/test_prefilter.py
git commit -m "feat: deterministic prefilter on title tokens and price band"
```

---

## Task 7: Extraction schema models

**Files:**
- Create: `src/marketsearch/extraction_models.py`
- Test: `tests/test_extraction_models.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Extraction` (Pydantic) with `.core: CoreFacts`, `.specs: MachineSpecs`, `.condition: ConditionHistory`, `.deal: DealContext`, `.verdict: Literal["match","no_match","unverifiable"]`, `.confidence: float`, `.reasoning: str`, `.unknowns: list[str]`
  - `EXTRACTION_JSON_SCHEMA: dict` — the schema dict passed to `output_config.format.schema`

**Critical API constraints** (from the Global Constraints): every model sets `extra="forbid"` so the schema emits `additionalProperties: false`, and **no field may have a default** — a field with a default is omitted from `required`, which the structured-output API rejects. Optional values are expressed as `X | None` with no default, making them required-but-nullable.

- [ ] **Step 1: Write the failing test**

Create `tests/test_extraction_models.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_extraction_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.extraction_models'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/extraction_models.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_extraction_models.py -v`
Expected: 4 passed

If `test_every_property_is_required` fails, a field was given a default — remove the default and make the annotation `X | None`.

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/extraction_models.py tests/test_extraction_models.py
git commit -m "feat: extraction schema models with API-compatible json schema"
```

---

## Task 8: Claude extraction

**Files:**
- Create: `src/marketsearch/extract.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `RawListing`, `ListingDetail` (Task 1), `Extraction`, `EXTRACTION_JSON_SCHEMA` (Task 7)
- Produces:
  - `build_prompt(listing: RawListing, detail: ListingDetail, criteria: str) -> str`
  - `ExtractionResult` frozen dataclass: `.extraction: Extraction`, `.input_tokens: int`, `.output_tokens: int`, `.cost_cents: float`
  - `Extractor(client, model: str, effort: str)` with `.extract(listing, detail, criteria) -> ExtractionResult`
  - `ExtractionError(Exception)`

`client` is duck-typed — anything exposing `.messages.create(...)`. Production passes `anthropic.Anthropic()`; tests pass a stub. This is what keeps the test suite off the network.

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract.py`:

```python
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from marketsearch.extract import ExtractionError, Extractor, build_prompt
from marketsearch.models import ListingDetail, RawListing

VALID_PAYLOAD = {
    "core": {"year": 2019, "make_model": "Bobcat T770", "engine_hours": 2400,
             "asking_price": 38000, "location": "Olathe, KS"},
    "specs": {"cab_enclosed": True, "has_ac": True, "two_speed": True, "high_flow": False,
              "tracks_or_tires": "tracks", "undercarriage_condition": "good",
              "aux_hydraulics": True},
    "condition": {"runs": True, "stated_issues": [], "recent_service": [],
                  "damage_notes": None, "one_owner_claim": False},
    "deal": {"attachments": ["bucket"], "seller_type": "private",
             "financing_or_trade": False, "price_vs_market_note": "fair"},
    "verdict": "match", "confidence": 0.9,
    "reasoning": "2,400 hours, 2-speed confirmed.", "unknowns": [],
}


class StubClient:
    """Records the request and replays a canned response."""

    def __init__(self, payload=None, stop_reason="end_turn", input_tokens=1500,
                 output_tokens=400):
        self.captured: dict = {}
        text = json.dumps(payload if payload is not None else VALID_PAYLOAD)
        self._response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason=stop_reason,
            usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        )
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.captured = kwargs
        return self._response


def listing() -> RawListing:
    return RawListing(
        listing_id="1", title="2019 Bobcat T770", price_cents=3_800_000,
        location="Olathe, KS", url="https://example.com/1",
        thumbnail_url=None, seller_name="Dale S",
    )


def detail(description: str = "2,400 hours. 2-speed. Cab with AC.") -> ListingDetail:
    return ListingDetail(
        listing_id="1", description=description,
        structured_fields={"condition": "used"}, photo_urls=[], distance_miles=42.0,
    )


def test_prompt_contains_criteria_title_and_description():
    prompt = build_prompt(listing(), detail(), "Under 3000 engine hours.")
    assert "Under 3000 engine hours." in prompt
    assert "2019 Bobcat T770" in prompt
    assert "2,400 hours" in prompt


def test_prompt_includes_asking_price_in_dollars():
    prompt = build_prompt(listing(), detail(), "any")
    assert "$38,000" in prompt


def test_prompt_handles_missing_price():
    no_price = listing().model_copy(update={"price_cents": None})
    prompt = build_prompt(no_price, detail(), "any")
    assert "not stated" in prompt.lower()


def test_extract_returns_parsed_extraction():
    extractor = Extractor(StubClient(), model="claude-opus-5", effort="low")
    result = extractor.extract(listing(), detail(), "Under 3000 engine hours.")
    assert result.extraction.verdict == "match"
    assert result.extraction.core.engine_hours == 2400


def test_extract_sends_the_json_schema_and_effort():
    client = StubClient()
    Extractor(client, model="claude-opus-5", effort="low").extract(
        listing(), detail(), "any"
    )
    output_config = client.captured["output_config"]
    assert output_config["effort"] == "low"
    assert output_config["format"]["type"] == "json_schema"
    assert output_config["format"]["schema"]["additionalProperties"] is False
    assert client.captured["model"] == "claude-opus-5"


def test_extract_never_sends_deprecated_output_format():
    client = StubClient()
    Extractor(client, model="claude-opus-5", effort="low").extract(listing(), detail(), "any")
    assert "output_format" not in client.captured


def test_cost_is_computed_from_token_usage():
    client = StubClient(input_tokens=1_000_000, output_tokens=1_000_000)
    result = Extractor(client, model="claude-opus-5", effort="low").extract(
        listing(), detail(), "any"
    )
    # Opus 5: $5/MTok in, $25/MTok out -> $30.00 -> 3000 cents
    assert result.cost_cents == pytest.approx(3000.0)


def test_cost_uses_the_configured_model_price():
    client = StubClient(input_tokens=1_000_000, output_tokens=1_000_000)
    result = Extractor(client, model="claude-haiku-4-5", effort="low").extract(
        listing(), detail(), "any"
    )
    # Haiku 4.5: $1/MTok in, $5/MTok out -> $6.00 -> 600 cents
    assert result.cost_cents == pytest.approx(600.0)


def test_unknown_model_price_falls_back_to_zero_not_a_crash():
    client = StubClient()
    result = Extractor(client, model="some-future-model", effort="low").extract(
        listing(), detail(), "any"
    )
    assert result.cost_cents == 0.0


def test_refusal_raises_extraction_error():
    client = StubClient(stop_reason="refusal")
    with pytest.raises(ExtractionError, match="refused"):
        Extractor(client, model="claude-opus-5", effort="low").extract(
            listing(), detail(), "any"
        )


def test_truncated_response_raises_extraction_error():
    client = StubClient(stop_reason="max_tokens")
    with pytest.raises(ExtractionError, match="truncated"):
        Extractor(client, model="claude-opus-5", effort="low").extract(
            listing(), detail(), "any"
        )


def test_malformed_json_raises_extraction_error():
    client = StubClient()
    client._response.content = [SimpleNamespace(type="text", text="not json at all")]
    with pytest.raises(ExtractionError, match="did not return valid JSON"):
        Extractor(client, model="claude-opus-5", effort="low").extract(
            listing(), detail(), "any"
        )


def test_schema_violating_json_raises_extraction_error():
    client = StubClient(payload={"verdict": "match"})  # missing everything else
    with pytest.raises(ExtractionError, match="did not match the schema"):
        Extractor(client, model="claude-opus-5", effort="low").extract(
            listing(), detail(), "any"
        )


def test_thinking_blocks_before_text_are_skipped():
    """Opus 5 has thinking on by default, so content[0] may be a thinking block."""
    client = StubClient()
    client._response.content = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=json.dumps(VALID_PAYLOAD)),
    ]
    result = Extractor(client, model="claude-opus-5", effort="low").extract(
        listing(), detail(), "any"
    )
    assert result.extraction.verdict == "match"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.extract'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/extract.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_extract.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/extract.py tests/test_extract.py
git commit -m "feat: claude extraction with structured output and cost accounting"
```

---

## Task 9: Extraction golden files

**Files:**
- Create: `tests/fixtures/listings/*.json` (six cases), `tests/test_extract_golden.py`
- Test: `tests/test_extract_golden.py`

**Interfaces:**
- Consumes: `Extractor`, `build_prompt` (Task 8)
- Produces: no importable code — this is the suite that tells you whether a criteria edit broke something, and the harness later reused by `marketsearch replay`.

Two test groups live here. The **schema test runs in CI** and guards fixture well-formedness. The **judgment test is marked `live_api`** and is deselected by default; it costs a few cents and is run deliberately after prompt or criteria changes.

- [ ] **Step 1: Create the six fixture files**

Each is a real-shaped listing paired with the verdict it must produce. Create `tests/fixtures/listings/01-clean-match.json`:

```json
{
  "name": "clean match — hours stated plainly",
  "title": "2019 Bobcat T770 Compact Track Loader",
  "price_cents": 3800000,
  "description": "2019 Bobcat T770, 2,400 hours. Enclosed cab with heat and A/C. 2-speed, high flow. Tracks are about 70%. Recently serviced, new filters. Comes with 80\" bucket and forks. One owner, always shedded. Clean machine.",
  "criteria": "Under 3000 engine hours. 2-speed required. Enclosed cab with A/C strongly preferred. Reject anything describing major engine or hydraulic problems, or an undercarriage described as worn out or needing replacement.",
  "expect": {"verdict": "match", "engine_hours": 2400, "two_speed": true, "cab_enclosed": true}
}
```

Create `tests/fixtures/listings/02-hours-buried.json`:

```json
{
  "name": "hours buried mid-paragraph in prose",
  "title": "Bobcat T770 skid steer track machine",
  "price_cents": 3500000,
  "description": "Selling my T770 because I picked up a bigger machine. It has been a great unit for me over the four years I owned it, never left me stranded, and I just put a set of tracks on it last fall so those are basically new. Sitting at twenty eight hundred hours on the meter. Cab is enclosed, air works. Two speed. Priced to sell.",
  "criteria": "Under 3000 engine hours. 2-speed required. Enclosed cab with A/C strongly preferred. Reject anything describing major engine or hydraulic problems, or an undercarriage described as worn out or needing replacement.",
  "expect": {"verdict": "match", "engine_hours": 2800, "two_speed": true}
}
```

Create `tests/fixtures/listings/03-hours-only-in-title.json`:

```json
{
  "name": "hours appear only in the title",
  "title": "2017 Bobcat T770 1850 HRS 2 SPEED HIGH FLOW",
  "price_cents": 4200000,
  "description": "Runs and operates as it should. Call or text.",
  "criteria": "Under 3000 engine hours. 2-speed required. Enclosed cab with A/C strongly preferred. Reject anything describing major engine or hydraulic problems, or an undercarriage described as worn out or needing replacement.",
  "expect": {"verdict": "match", "engine_hours": 1850, "two_speed": true}
}
```

Create `tests/fixtures/listings/04-no-hours-stated.json`:

```json
{
  "name": "no hours anywhere — must be unverifiable, not no_match",
  "title": "Bobcat T770",
  "price_cents": 3800000,
  "description": "Runs great. $38,000 firm. Serious inquiries only.",
  "criteria": "Under 3000 engine hours. 2-speed required. Enclosed cab with A/C strongly preferred. Reject anything describing major engine or hydraulic problems, or an undercarriage described as worn out or needing replacement.",
  "expect": {"verdict": "unverifiable", "engine_hours": null, "unknowns_include": "engine_hours"}
}
```

Create `tests/fixtures/listings/05-disqualifying-condition.json`:

```json
{
  "name": "undercarriage explicitly shot — must be no_match",
  "title": "2016 Bobcat T770 2400 hours",
  "price_cents": 2600000,
  "description": "2016 T770 with 2400 hours. Runs and drives fine. Undercarriage is completely worn out and will need to be replaced, rollers and tracks are done. Priced accordingly. 2 speed, cab with air.",
  "criteria": "Under 3000 engine hours. 2-speed required. Enclosed cab with A/C strongly preferred. Reject anything describing major engine or hydraulic problems, or an undercarriage described as worn out or needing replacement.",
  "expect": {"verdict": "no_match", "engine_hours": 2400}
}
```

Create `tests/fixtures/listings/06-shouty-no-punctuation.json`:

```json
{
  "name": "all caps, no punctuation, hours over the limit",
  "title": "BOBCAT T770 TRACK LOADER",
  "price_cents": 2900000,
  "description": "BOBCAT T770 4600 HOURS RUNS AND WORKS GOOD CAB WITH HEAT AND AC 2 SPEED HIGH FLOW AUX HYDRAULICS COMES WITH BUCKET CALL FOR MORE INFO NO TRADES",
  "criteria": "Under 3000 engine hours. 2-speed required. Enclosed cab with A/C strongly preferred. Reject anything describing major engine or hydraulic problems, or an undercarriage described as worn out or needing replacement.",
  "expect": {"verdict": "no_match", "engine_hours": 4600}
}
```

- [ ] **Step 2: Write the test**

Create `tests/test_extract_golden.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from marketsearch.extract import Extractor
from marketsearch.models import ListingDetail, RawListing

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "listings"
CASES = sorted(GOLDEN_DIR.glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def as_inputs(case: dict) -> tuple[RawListing, ListingDetail]:
    listing = RawListing(
        listing_id="golden", title=case["title"], price_cents=case["price_cents"],
        location="Olathe, KS", url="https://example.com/golden",
        thumbnail_url=None, seller_name=None,
    )
    detail = ListingDetail(
        listing_id="golden", description=case["description"],
        structured_fields={}, photo_urls=[], distance_miles=None,
    )
    return listing, detail


def test_golden_directory_is_not_empty():
    assert CASES, "no golden fixtures found — the suite would silently pass"


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_fixture_is_well_formed(path: Path):
    """Runs in CI. Guards against a malformed fixture that would make the live
    suite fail for the wrong reason."""
    case = load(path)
    for key in ("name", "title", "price_cents", "description", "criteria", "expect"):
        assert key in case, f"{path.name} missing '{key}'"
    assert case["expect"]["verdict"] in {"match", "no_match", "unverifiable"}
    as_inputs(case)  # must construct without a validation error


@pytest.mark.live_api
@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_extraction_matches_golden_verdict(path: Path):
    """Deselected by default. Run with: pytest -m live_api"""
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    case = load(path)
    listing, detail = as_inputs(case)
    extractor = Extractor(anthropic.Anthropic(), model="claude-opus-5", effort="low")
    result = extractor.extract(listing, detail, case["criteria"])
    got = result.extraction
    expect = case["expect"]

    assert got.verdict == expect["verdict"], (
        f"{case['name']}\nexpected {expect['verdict']}, got {got.verdict}\n"
        f"reasoning: {got.reasoning}"
    )

    if "engine_hours" in expect:
        assert got.core.engine_hours == expect["engine_hours"], (
            f"{case['name']}\nhours mismatch — reasoning: {got.reasoning}"
        )
    if "two_speed" in expect:
        assert got.specs.two_speed == expect["two_speed"]
    if "cab_enclosed" in expect:
        assert got.specs.cab_enclosed == expect["cab_enclosed"]
    if "unknowns_include" in expect:
        assert expect["unknowns_include"] in got.unknowns
```

- [ ] **Step 3: Run the CI-safe tests**

Run: `pytest tests/test_extract_golden.py -v`
Expected: 7 passed (1 directory check + 6 fixture checks); the 6 live tests are deselected.

- [ ] **Step 4: Run the live suite once to confirm the prompt actually works**

Run: `pytest tests/test_extract_golden.py -m live_api -v`
Expected: 6 passed, costing roughly 10–15 cents total.

If a case fails, the fix is the `SYSTEM_PROMPT` in `src/marketsearch/extract.py` or the field descriptions in `extraction_models.py` — **not** the fixture. The fixtures encode what correct behaviour is; changing one to match a wrong answer defeats the suite.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/listings tests/test_extract_golden.py
git commit -m "test: golden-file extraction cases covering hour-parsing edge cases"
```
