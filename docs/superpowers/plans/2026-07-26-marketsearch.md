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

Valid `kind` values used across the system: `"match"`, `"unverified"`, `"price_change"`, `"description_change"`, `"removed"`. Valid `channel` values: `"email"`, `"sms"`.

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

---

## Task 10: Facebook page parsing

**Files:**
- Create: `src/marketsearch/sources/__init__.py`, `src/marketsearch/sources/base.py`, `src/marketsearch/sources/parse.py`, `tests/fixtures/pages/*.html`
- Test: `tests/test_parse.py`

**Interfaces:**
- Consumes: `RawListing`, `ListingDetail` (Task 1)
- Produces:
  - `sources/base.py`: `ListingSource` Protocol with `search(query, location, radius_miles) -> list[RawListing]`, `fetch_detail(listing_id) -> ListingDetail`, `fetch_saved() -> list[RawListing]`; exceptions `SourceError`, `LoginRequired`, `ParseError`
  - `sources/parse.py`: `extract_json_blobs(html) -> list[Any]`, `iter_dicts(obj) -> Iterator[dict]`, `parse_search_results(html) -> list[RawListing]`, `parse_item_detail(html, listing_id) -> ListingDetail`, `parse_saved_listings(html) -> list[RawListing]`, `detect_login_wall(html) -> str | None`

`fetch_saved` returns full `RawListing` objects, not bare ids. A machine you saved while browsing may never have appeared in a search, so the watched-listing task needs its title, price, and seller to build a card and a fingerprint — an id alone would force a second page load per saved item.

**Design note.** Parsing walks the embedded JSON looking for *nodes with the right keys*, never for a fixed path. Facebook rotates CSS class names and restructures its payload tree constantly, but the leaf key names (`marketplace_listing_title`, `listing_price`) are far more stable. A path-based parser breaks weekly; a key-based one survives most reshuffles.

- [ ] **Step 1: Create the fixture pages**

These are synthetic but structurally faithful — a `<script type="application/json">` blob wrapping listing nodes, which is the shape Facebook actually ships. Task 11 replaces them with captures of real pages.

Create `tests/fixtures/pages/search.html`:

```html
<!DOCTYPE html><html><head><title>Marketplace</title></head><body>
<script type="application/json" data-sjs>
{"require":[["ScheduledServerJS","handle",null,[{"__bbox":{"result":{"data":{"marketplace_search":{"feed_units":{"edges":[
 {"node":{"listing":{"id":"1001","marketplace_listing_title":"2019 Bobcat T770 Compact Track Loader","listing_price":{"amount":"38000","formatted_amount":"$38,000"},"location":{"reverse_geocode":{"city":"Olathe","state":"KS","city_page":{"display_name":"Olathe, KS"}}},"primary_listing_photo":{"image":{"uri":"https://scontent.example.com/1001.jpg"}},"marketplace_listing_seller":{"name":"Dale S"}}}},
 {"node":{"listing":{"id":"1002","marketplace_listing_title":"WANTED Bobcat T770","listing_price":{"amount":"1","formatted_amount":"$1"},"location":{"reverse_geocode":{"city":"Topeka","state":"KS","city_page":{"display_name":"Topeka, KS"}}},"primary_listing_photo":{"image":{"uri":"https://scontent.example.com/1002.jpg"}},"marketplace_listing_seller":{"name":"Rita M"}}}},
 {"node":{"listing":{"id":"1003","marketplace_listing_title":"Bobcat T300 skid steer","listing_price":null,"location":{"reverse_geocode":{"city":"Lawrence","state":"KS","city_page":{"display_name":"Lawrence, KS"}}},"primary_listing_photo":null,"marketplace_listing_seller":null}}}
]}}}}}}]]]}
</script>
<script type="application/json">{"unrelated":{"nested":{"noise":true}}}</script>
</body></html>
```

Create `tests/fixtures/pages/item.html`:

```html
<!DOCTYPE html><html><body>
<script type="application/json" data-sjs>
{"require":[["ScheduledServerJS","handle",null,[{"__bbox":{"result":{"data":{"viewer":{"marketplace_product_details_page":{"target":{
 "id":"1001",
 "marketplace_listing_title":"2019 Bobcat T770 Compact Track Loader",
 "redacted_description":{"text":"2019 Bobcat T770, 2,400 hours. Enclosed cab with heat and A/C. 2-speed, high flow. Recently serviced."},
 "listing_price":{"amount":"38000","formatted_amount":"$38,000"},
 "location_text":{"text":"Olathe, KS"},
 "delivery_types":["IN_PERSON"],
 "attribute_data":[{"label":"Condition","value":"Used - good"},{"label":"Category","value":"Heavy Equipment"}],
 "listing_photos":[{"image":{"uri":"https://scontent.example.com/1001-a.jpg"}},{"image":{"uri":"https://scontent.example.com/1001-b.jpg"}}]
}}}}}}}]]]}
</script>
</body></html>
```

Create `tests/fixtures/pages/saved.html`:

```html
<!DOCTYPE html><html><body>
<script type="application/json" data-sjs>
{"require":[["ScheduledServerJS","handle",null,[{"__bbox":{"result":{"data":{"viewer":{"saved_dashboard":{"saved_items":{"edges":[
 {"node":{"listing":{"id":"1001","marketplace_listing_title":"2019 Bobcat T770 Compact Track Loader","listing_price":{"amount":"38000"},"location":null,"primary_listing_photo":null,"marketplace_listing_seller":null}}}},
 {"node":{"listing":{"id":"2002","marketplace_listing_title":"2017 Bobcat T300","listing_price":{"amount":"24500"},"location":null,"primary_listing_photo":null,"marketplace_listing_seller":null}}}}
]}}}}}}}]]]}
</script>
</body></html>
```

Create `tests/fixtures/pages/login_wall.html`:

```html
<!DOCTYPE html><html><body>
<form action="/login/device-based/regular/login/" method="post">
<input name="email"><input name="pass" type="password">
<button name="login">Log In</button>
</form>
</body></html>
```

Create `tests/fixtures/pages/checkpoint.html`:

```html
<!DOCTYPE html><html><body>
<div id="checkpoint">We need to confirm it's you. Please complete a security check to continue.</div>
</body></html>
```

Create `tests/fixtures/pages/empty_results.html`:

```html
<!DOCTYPE html><html><body>
<script type="application/json" data-sjs>
{"require":[["ScheduledServerJS","handle",null,[{"__bbox":{"result":{"data":{"marketplace_search":{"feed_units":{"edges":[]}}}}}}]]]}
</script>
<div>No results found</div>
</body></html>
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_parse.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.sources.base import ParseError
from marketsearch.sources.parse import (
    detect_login_wall,
    extract_json_blobs,
    iter_dicts,
    parse_item_detail,
    parse_saved_listings,
    parse_search_results,
)


def page(fixtures_dir: Path, name: str) -> str:
    return (fixtures_dir / "pages" / name).read_text(encoding="utf-8")


def test_extract_json_blobs_finds_all_script_payloads(fixtures_dir: Path):
    blobs = extract_json_blobs(page(fixtures_dir, "search.html"))
    assert len(blobs) == 2


def test_extract_json_blobs_skips_unparseable_scripts():
    html = '<script type="application/json">{"good":1}</script>' \
           '<script type="application/json">not json</script>'
    assert extract_json_blobs(html) == [{"good": 1}]


def test_iter_dicts_walks_lists_and_nested_dicts():
    tree = {"a": [{"b": 1}, {"c": {"d": 2}}]}
    found = list(iter_dicts(tree))
    assert {"b": 1} in found
    assert {"d": 2} in found


def test_parse_search_results_returns_all_listings(fixtures_dir: Path):
    listings = parse_search_results(page(fixtures_dir, "search.html"))
    assert [l.listing_id for l in listings] == ["1001", "1002", "1003"]


def test_parse_search_results_maps_fields(fixtures_dir: Path):
    listings = parse_search_results(page(fixtures_dir, "search.html"))
    first = listings[0]
    assert first.title == "2019 Bobcat T770 Compact Track Loader"
    assert first.price_cents == 3_800_000
    assert first.location == "Olathe, KS"
    assert first.seller_name == "Dale S"
    assert first.thumbnail_url == "https://scontent.example.com/1001.jpg"
    assert first.url == "https://www.facebook.com/marketplace/item/1001/"


def test_parse_search_results_tolerates_missing_optional_fields(fixtures_dir: Path):
    listings = parse_search_results(page(fixtures_dir, "search.html"))
    third = listings[2]
    assert third.price_cents is None
    assert third.thumbnail_url is None
    assert third.seller_name is None


def test_parse_search_results_deduplicates_repeated_nodes():
    """Facebook's payload often contains the same listing in several places."""
    html = (
        '<script type="application/json">'
        '{"a":{"id":"1","marketplace_listing_title":"T770","listing_price":{"amount":"1"}},'
        ' "b":{"id":"1","marketplace_listing_title":"T770","listing_price":{"amount":"1"}}}'
        "</script>"
    )
    assert len(parse_search_results(html)) == 1


def test_empty_results_page_returns_empty_list_not_an_error(fixtures_dir: Path):
    """Distinguishing 'zero results' from 'could not parse' is the whole point."""
    assert parse_search_results(page(fixtures_dir, "empty_results.html")) == []


def test_page_with_no_json_at_all_raises_parse_error():
    with pytest.raises(ParseError, match="no JSON payload"):
        parse_search_results("<html><body>Something went wrong</body></html>")


def test_parse_item_detail(fixtures_dir: Path):
    detail = parse_item_detail(page(fixtures_dir, "item.html"), "1001")
    assert "2,400 hours" in detail.description
    assert detail.photo_urls == [
        "https://scontent.example.com/1001-a.jpg",
        "https://scontent.example.com/1001-b.jpg",
    ]
    assert detail.structured_fields["Condition"] == "Used - good"
    assert detail.structured_fields["Category"] == "Heavy Equipment"


def test_parse_item_detail_raises_when_the_listing_node_is_absent(fixtures_dir: Path):
    with pytest.raises(ParseError, match="1001"):
        parse_item_detail(page(fixtures_dir, "empty_results.html"), "1001")


def test_parse_saved_listings_returns_full_listings(fixtures_dir: Path):
    saved = parse_saved_listings(page(fixtures_dir, "saved.html"))
    assert [l.listing_id for l in saved] == ["1001", "2002"]
    assert saved[0].title == "2019 Bobcat T770 Compact Track Loader"
    assert saved[1].price_cents == 2_450_000


def test_parse_saved_listings_returns_empty_for_an_empty_collection(fixtures_dir: Path):
    assert parse_saved_listings(page(fixtures_dir, "empty_results.html")) == []


def test_detect_login_wall(fixtures_dir: Path):
    assert detect_login_wall(page(fixtures_dir, "login_wall.html")) == "login"


def test_detect_checkpoint(fixtures_dir: Path):
    assert detect_login_wall(page(fixtures_dir, "checkpoint.html")) == "checkpoint"


def test_detect_login_wall_returns_none_for_a_normal_page(fixtures_dir: Path):
    assert detect_login_wall(page(fixtures_dir, "search.html")) is None
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.sources'`

- [ ] **Step 4: Write `sources/base.py`**

Create `src/marketsearch/sources/__init__.py` (empty file).

Create `src/marketsearch/sources/base.py`:

```python
"""The boundary between MarketSearch and any particular marketplace.

Everything downstream of this file speaks only RawListing and ListingDetail.
Swapping Facebook for a paid scraping API means writing one new class here.
"""

from __future__ import annotations

from typing import Protocol

from marketsearch.models import ListingDetail, RawListing


class SourceError(Exception):
    """Any failure while talking to the listing source."""


class LoginRequired(SourceError):
    """The source demanded a login or presented a security checkpoint.

    Callers must stop immediately rather than retrying — retrying a checkpoint
    is how a soft flag becomes a hard one.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(f"facebook requires attention: {kind}")
        self.kind = kind


class ParseError(SourceError):
    """The page loaded but its structure was not recognised.

    Distinct from 'zero results', which is an ordinary empty list.
    """


class ListingSource(Protocol):
    def search(self, query: str, location: str, radius_miles: int) -> list[RawListing]: ...
    def fetch_detail(self, listing_id: str) -> ListingDetail: ...
    def fetch_saved(self) -> list[RawListing]: ...
```

- [ ] **Step 5: Write `sources/parse.py`**

Create `src/marketsearch/sources/parse.py`:

```python
"""Pure functions over Facebook page HTML.

No browser, no network — every rule here is testable against a saved page.
This is the module that breaks when Facebook changes, so it needs the fastest
possible test cycle.

Strategy: walk every embedded JSON payload looking for *nodes carrying the
right keys*, never for a fixed path. Facebook restructures its payload tree
often; the leaf key names change far more slowly.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from marketsearch.models import ListingDetail, RawListing
from marketsearch.sources.base import ParseError

_SCRIPT_JSON = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_LOGIN_MARKERS = ('name="pass"', 'action="/login/', "login_form")
_CHECKPOINT_MARKERS = ("checkpoint", "confirm it's you", "security check")

ITEM_URL = "https://www.facebook.com/marketplace/item/{listing_id}/"


def extract_json_blobs(html: str) -> list[Any]:
    """Every parseable JSON payload embedded in the page. Unparseable script
    bodies are skipped rather than fatal — Facebook ships several formats."""
    blobs: list[Any] = []
    for match in _SCRIPT_JSON.finditer(html):
        try:
            blobs.append(json.loads(match.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
    return blobs


def iter_dicts(obj: Any) -> Iterator[dict]:
    """Yield every dict anywhere in a nested structure, including the root."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_dicts(item)


def _all_dicts(html: str) -> list[dict]:
    blobs = extract_json_blobs(html)
    if not blobs:
        raise ParseError(
            "no JSON payload found in page — Facebook's markup has probably changed"
        )
    return [d for blob in blobs for d in iter_dicts(blob)]


def _looks_like_listing(node: dict) -> bool:
    return "id" in node and "marketplace_listing_title" in node


def _price_cents(node: dict) -> int | None:
    price = node.get("listing_price")
    if not isinstance(price, dict):
        return None
    amount = price.get("amount")
    if amount is None:
        return None
    try:
        return int(round(float(amount) * 100))
    except (TypeError, ValueError):
        return None


def _location(node: dict) -> str | None:
    loc = node.get("location")
    if isinstance(loc, dict):
        geo = loc.get("reverse_geocode")
        if isinstance(geo, dict):
            page = geo.get("city_page")
            if isinstance(page, dict) and page.get("display_name"):
                return str(page["display_name"])
            city, state = geo.get("city"), geo.get("state")
            if city and state:
                return f"{city}, {state}"
    text = node.get("location_text")
    if isinstance(text, dict) and text.get("text"):
        return str(text["text"])
    return None


def _thumbnail(node: dict) -> str | None:
    photo = node.get("primary_listing_photo")
    if isinstance(photo, dict):
        image = photo.get("image")
        if isinstance(image, dict) and image.get("uri"):
            return str(image["uri"])
    return None


def _seller(node: dict) -> str | None:
    seller = node.get("marketplace_listing_seller")
    if isinstance(seller, dict) and seller.get("name"):
        return str(seller["name"])
    return None


def _to_raw_listing(node: dict) -> RawListing:
    listing_id = str(node["id"])
    return RawListing(
        listing_id=listing_id,
        title=str(node["marketplace_listing_title"]),
        price_cents=_price_cents(node),
        location=_location(node),
        url=ITEM_URL.format(listing_id=listing_id),
        thumbnail_url=_thumbnail(node),
        seller_name=_seller(node),
    )


def parse_search_results(html: str) -> list[RawListing]:
    """All listings on a search page, in payload order, deduplicated by id.

    An empty list means Facebook returned no results. A ParseError means the
    page could not be understood at all. Conflating those is how a scraper
    silently reports nothing for three weeks.
    """
    listings: dict[str, RawListing] = {}
    for node in _all_dicts(html):
        if _looks_like_listing(node):
            listing = _to_raw_listing(node)
            listings.setdefault(listing.listing_id, listing)
    return list(listings.values())


def parse_saved_listings(html: str) -> list[RawListing]:
    """Listings from the saved-items page.

    The saved page ships the same listing nodes as search, so this is the same
    walk. It returns full listings rather than ids because a machine saved
    while browsing may never have appeared in a search — the watched-listing
    pipeline needs its title and price to build a card without a second fetch.
    """
    return parse_search_results(html)


def _description(node: dict) -> str:
    for key in ("redacted_description", "description"):
        value = node.get(key)
        if isinstance(value, dict) and value.get("text"):
            return str(value["text"])
        if isinstance(value, str):
            return value
    return ""


def _structured_fields(node: dict) -> dict[str, object]:
    fields: dict[str, object] = {}
    attributes = node.get("attribute_data")
    if isinstance(attributes, list):
        for attribute in attributes:
            if isinstance(attribute, dict) and attribute.get("label"):
                fields[str(attribute["label"])] = attribute.get("value")
    for key in ("delivery_types", "creation_time", "is_sold"):
        if key in node:
            fields[key] = node[key]
    return fields


def _photo_urls(node: dict) -> list[str]:
    urls: list[str] = []
    photos = node.get("listing_photos")
    if isinstance(photos, list):
        for photo in photos:
            if isinstance(photo, dict):
                image = photo.get("image")
                if isinstance(image, dict) and image.get("uri"):
                    uri = str(image["uri"])
                    if uri not in urls:
                        urls.append(uri)
    return urls


def parse_item_detail(html: str, listing_id: str) -> ListingDetail:
    """The detail page for one listing.

    Prefers the node whose id matches; falls back to the richest listing-shaped
    node, since Facebook occasionally omits the id on the detail payload.
    """
    candidates = [n for n in _all_dicts(html) if _looks_like_listing(n)]
    exact = [n for n in candidates if str(n.get("id")) == str(listing_id)]
    pool = exact or [n for n in candidates if "redacted_description" in n or "description" in n]
    if not pool:
        raise ParseError(f"no listing node found for {listing_id} on the detail page")

    node = max(pool, key=lambda n: len(_description(n)))
    return ListingDetail(
        listing_id=str(listing_id),
        description=_description(node),
        structured_fields=_structured_fields(node),
        photo_urls=_photo_urls(node),
        distance_miles=None,
    )


def detect_login_wall(html: str) -> str | None:
    """Return 'login', 'checkpoint', or None.

    Checked before parsing on every page. A non-None result must stop the run
    immediately — never retry into a checkpoint.
    """
    lowered = html.lower()
    if any(marker in lowered for marker in _CHECKPOINT_MARKERS):
        return "checkpoint"
    if any(marker.lower() in lowered for marker in _LOGIN_MARKERS):
        return "login"
    return None
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_parse.py -v`
Expected: 16 passed

- [ ] **Step 7: Commit**

```bash
git add src/marketsearch/sources tests/test_parse.py tests/fixtures/pages
git commit -m "feat: key-based parsing of facebook marketplace json payloads"
```

---

## Task 11: Playwright Facebook driver

**Files:**
- Create: `src/marketsearch/sources/facebook.py`
- Test: `tests/test_facebook_source.py`

**Interfaces:**
- Consumes: `parse_*` and `detect_login_wall` (Task 10), `LoginRequired`, `ParseError` (Task 10)
- Produces:
  - `build_search_url(query: str, radius_miles: int) -> str`
  - `build_item_url(listing_id: str) -> str`
  - `SAVED_URL: str`
  - `FacebookSource(profile_dir: Path, headless: bool = True, debug_dir: Path | None = None, fetch_html: Callable[[str], str] | None = None)` implementing `ListingSource`, usable as a context manager
  - `open_login_browser(profile_dir: Path) -> None`

**Design note.** `fetch_html` is injectable. Production leaves it `None` and the class drives Playwright; tests pass a stub, so the whole suite runs with no browser and no network. This is also what lets Task 15's pipeline tests exercise real orchestration against canned pages.

**Location handling.** Facebook derives Marketplace location from the logged-in profile, not from a URL parameter that can be reliably constructed. Rather than guess at location-id resolution, the user sets their Marketplace location and radius by hand once during `marketsearch login`; the persistent profile remembers it. `radius_miles` is still sent as a URL hint, and `location` is carried in the interface for diagnostics and for future non-Facebook sources.

- [ ] **Step 1: Write the failing test**

Create `tests/test_facebook_source.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.sources.base import LoginRequired, ParseError
from marketsearch.sources.facebook import (
    FacebookSource,
    build_item_url,
    build_search_url,
)


def page(fixtures_dir: Path, name: str) -> str:
    return (fixtures_dir / "pages" / name).read_text(encoding="utf-8")


def source_returning(html: str, tmp_path: Path, **kwargs) -> FacebookSource:
    return FacebookSource(
        profile_dir=tmp_path / "profile",
        fetch_html=lambda url: html,
        debug_dir=tmp_path / "debug",
        **kwargs,
    )


def test_build_search_url_encodes_the_query():
    url = build_search_url("Bobcat T770", radius_miles=250)
    assert "query=Bobcat+T770" in url or "query=Bobcat%20T770" in url
    assert url.startswith("https://www.facebook.com/marketplace/search")


def test_build_search_url_sorts_newest_first():
    assert "sortBy=creation_time_descend" in build_search_url("x", radius_miles=100)


def test_build_search_url_converts_miles_to_kilometres():
    url = build_search_url("x", radius_miles=250)
    assert "radiusKM=402" in url  # 250 mi -> 402 km


def test_build_item_url():
    assert build_item_url("1001") == "https://www.facebook.com/marketplace/item/1001/"


def test_search_returns_parsed_listings(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "search.html"), tmp_path)
    listings = src.search("Bobcat T770", location="Olathe, KS", radius_miles=250)
    assert [l.listing_id for l in listings] == ["1001", "1002", "1003"]


def test_search_returns_empty_list_for_no_results(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "empty_results.html"), tmp_path)
    assert src.search("Bobcat T999", location="x", radius_miles=250) == []


def test_login_wall_raises_login_required(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "login_wall.html"), tmp_path)
    with pytest.raises(LoginRequired) as exc:
        src.search("x", location="y", radius_miles=1)
    assert exc.value.kind == "login"


def test_checkpoint_raises_login_required(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "checkpoint.html"), tmp_path)
    with pytest.raises(LoginRequired) as exc:
        src.search("x", location="y", radius_miles=1)
    assert exc.value.kind == "checkpoint"


def test_login_wall_is_detected_before_parsing(fixtures_dir: Path, tmp_path: Path):
    """A login page has no listing JSON. Without the check it would surface as
    a misleading ParseError and trigger the wrong recovery."""
    src = source_returning(page(fixtures_dir, "login_wall.html"), tmp_path)
    with pytest.raises(LoginRequired):
        src.fetch_detail("1001")


def test_parse_failure_writes_the_page_to_the_debug_dir(tmp_path: Path):
    src = source_returning("<html><body>totally unexpected</body></html>", tmp_path)
    with pytest.raises(ParseError):
        src.search("x", location="y", radius_miles=1)
    written = list((tmp_path / "debug").glob("*.html"))
    assert len(written) == 1
    assert "totally unexpected" in written[0].read_text(encoding="utf-8")


def test_fetch_detail_returns_parsed_detail(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "item.html"), tmp_path)
    detail = src.fetch_detail("1001")
    assert "2,400 hours" in detail.description
    assert len(detail.photo_urls) == 2


def test_fetch_saved_returns_full_listings(fixtures_dir: Path, tmp_path: Path):
    src = source_returning(page(fixtures_dir, "saved.html"), tmp_path)
    saved = src.fetch_saved()
    assert [l.listing_id for l in saved] == ["1001", "2002"]
    assert saved[0].title == "2019 Bobcat T770 Compact Track Loader"


def test_source_is_a_context_manager(fixtures_dir: Path, tmp_path: Path):
    with source_returning(page(fixtures_dir, "saved.html"), tmp_path) as src:
        assert [l.listing_id for l in src.fetch_saved()] == ["1001", "2002"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_facebook_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.sources.facebook'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/sources/facebook.py`:

```python
"""Playwright driver for Facebook Marketplace.

The only module in the project that knows what a Facebook page looks like, and
therefore the only one that should need changing when Facebook does.

Facebook derives Marketplace location from the logged-in profile rather than a
URL parameter that can be reliably constructed. The user sets location and
radius by hand once during `marketsearch login`; the persistent Chrome profile
remembers it.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from marketsearch.models import ListingDetail, RawListing
from marketsearch.sources.base import LoginRequired, ParseError, SourceError
from marketsearch.sources.parse import (
    detect_login_wall,
    parse_item_detail,
    parse_saved_listings,
    parse_search_results,
)

log = logging.getLogger(__name__)

SEARCH_BASE = "https://www.facebook.com/marketplace/search"
SAVED_URL = "https://www.facebook.com/marketplace/you/saved"
ITEM_BASE = "https://www.facebook.com/marketplace/item"

# Pause between page loads within a run, so a sweep does not read as a burst.
_MIN_PAGE_DELAY_S = 3.0
_MAX_PAGE_DELAY_S = 8.0

_PAGE_TIMEOUT_MS = 45_000


def build_search_url(query: str, radius_miles: int) -> str:
    params = {
        "query": query,
        "radiusKM": int(round(radius_miles * 1.60934)),
        "sortBy": "creation_time_descend",
    }
    return f"{SEARCH_BASE}?{urlencode(params)}"


def build_item_url(listing_id: str) -> str:
    return f"{ITEM_BASE}/{listing_id}/"


class FacebookSource:
    def __init__(
        self,
        profile_dir: Path,
        headless: bool = True,
        debug_dir: Path | None = None,
        fetch_html: Callable[[str], str] | None = None,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self._fetch_html_override = fetch_html
        self._playwright = None
        self._context = None
        self._page = None
        self._loaded_any_page = False

    # ---- lifecycle -------------------------------------------------------

    def __enter__(self) -> "FacebookSource":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel="chrome",
            headless=self.headless,
            viewport={"width": 1440, "height": 900},
        )
        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )

    # ---- page fetching ---------------------------------------------------

    def _fetch(self, url: str) -> str:
        if self._fetch_html_override is not None:
            return self._fetch_html_override(url)

        self._ensure_browser()
        if self._loaded_any_page:
            time.sleep(random.uniform(_MIN_PAGE_DELAY_S, _MAX_PAGE_DELAY_S))

        log.debug("loading %s", url)
        self._page.goto(url, timeout=_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        self._page.wait_for_timeout(2500)  # let the JSON payload land
        self._loaded_any_page = True
        return self._page.content()

    def _save_debug(self, label: str, html: str) -> None:
        if self.debug_dir is None:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = self.debug_dir / f"{stamp}-{label}.html"
        path.write_text(html, encoding="utf-8")
        log.warning("saved unparseable page to %s", path)

    def _load(self, url: str, label: str) -> str:
        """Fetch, then check for a login wall *before* attempting to parse.

        A login page contains no listing JSON, so parsing it first would raise
        a misleading ParseError and trigger the wrong recovery.
        """
        try:
            html = self._fetch(url)
        except LoginRequired:
            raise
        except Exception as exc:  # playwright timeouts, navigation failures
            raise SourceError(f"failed to load {url}: {exc}") from exc

        wall = detect_login_wall(html)
        if wall is not None:
            raise LoginRequired(wall)
        return html

    # ---- ListingSource ---------------------------------------------------

    def search(self, query: str, location: str, radius_miles: int) -> list[RawListing]:
        url = build_search_url(query, radius_miles)
        log.info("searching %r (location %s, %d mi)", query, location, radius_miles)
        html = self._load(url, "search")
        try:
            return parse_search_results(html)
        except ParseError:
            self._save_debug("search", html)
            raise

    def fetch_detail(self, listing_id: str) -> ListingDetail:
        html = self._load(build_item_url(listing_id), "item")
        try:
            return parse_item_detail(html, listing_id)
        except ParseError:
            self._save_debug(f"item-{listing_id}", html)
            raise

    def fetch_saved(self) -> list[RawListing]:
        html = self._load(SAVED_URL, "saved")
        try:
            return parse_saved_listings(html)
        except ParseError:
            self._save_debug("saved", html)
            raise


def open_login_browser(profile_dir: Path) -> None:
    """Open a visible Chrome on the persistent profile and block until closed.

    The user logs into Facebook, opens Marketplace, and sets their location and
    search radius by hand. All of it persists in the profile directory.
    """
    from playwright.sync_api import sync_playwright

    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.facebook.com/marketplace/", timeout=_PAGE_TIMEOUT_MS)
        print(
            "\nA browser window is open.\n"
            "  1. Log into Facebook if prompted.\n"
            "  2. Open Marketplace and set your location and search radius.\n"
            "  3. Close the browser window when done.\n"
        )
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        context.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_facebook_source.py -v`
Expected: 13 passed

- [ ] **Step 5: Install the browser binary**

Run: `playwright install chromium`

- [ ] **Step 6: Capture real pages and re-verify the parser**

The fixtures from Task 10 are structurally faithful but synthetic. Replace them with real captures now that a browser driver exists.

1. Run `python -c "from marketsearch.sources.facebook import open_login_browser; from pathlib import Path; open_login_browser(Path('chrome-profile'))"` and log in, set location and radius.
2. Save this scratch script as `scripts/capture_pages.py`:

```python
from __future__ import annotations

from pathlib import Path

from marketsearch.sources.facebook import (
    SAVED_URL,
    FacebookSource,
    build_item_url,
    build_search_url,
)

OUT = Path("tests/fixtures/pages")
OUT.mkdir(parents=True, exist_ok=True)

with FacebookSource(Path("chrome-profile"), headless=False) as src:
    for name, url in [
        ("real_search.html", build_search_url("Bobcat T770", 250)),
        ("real_saved.html", SAVED_URL),
    ]:
        (OUT / name).write_text(src._load(url, name), encoding="utf-8")
        print("wrote", name)

    listings = src.search("Bobcat T770", "anchor", 250)
    if listings:
        html = src._load(build_item_url(listings[0].listing_id), "item")
        (OUT / "real_item.html").write_text(html, encoding="utf-8")
        print("wrote real_item.html for", listings[0].listing_id)
```

3. Run it: `python scripts/capture_pages.py`
4. Add tests to `tests/test_parse.py` that run the same assertions against the `real_*.html` captures — at minimum: `parse_search_results` returns a non-empty list, every result has a non-empty `title` and a `listing_id` of digits, and `parse_item_detail` returns a non-empty `description`.
5. **If those tests fail, fix `parse.py`, not the captures.** Real payload key names may differ from the synthetic fixtures; that discovery is the entire point of this step.

- [ ] **Step 7: Commit**

```bash
git add src/marketsearch/sources/facebook.py tests/test_facebook_source.py \
        tests/fixtures/pages scripts/capture_pages.py tests/test_parse.py
git commit -m "feat: playwright facebook source with checkpoint detection"
```

---

## Task 12: Email rendering

**Files:**
- Create: `src/marketsearch/notify/__init__.py`, `src/marketsearch/notify/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `ListingRow` (Task 4), `ExtractionRow` (Task 5)
- Produces:
  - `MatchCard` frozen dataclass: `.listing: ListingRow`, `.extraction: ExtractionRow`, `.photos: list[bytes]`
  - `ChangeCard` frozen dataclass: `.listing: ListingRow`, `.kind: str`, `.old_price_cents: int | None`, `.new_price_cents: int | None`
  - `RenderedEmail` frozen dataclass: `.subject: str`, `.html: str`, `.images: list[tuple[str, bytes]]`
  - `render_email(matches: list[MatchCard], unverified: list[MatchCard], changes: list[ChangeCard]) -> RenderedEmail`
  - `render_sms(matches, unverified, changes) -> str`
  - `download_photos(urls: list[str], limit: int = 3, get: Callable[[str], bytes] | None = None) -> list[bytes]`

Photos are embedded as CID attachments rather than linked. Facebook's image URLs are signed and expire within days, so a linked email decays into broken-image boxes — including six months from now, when you are trying to remember what the machine looked like.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render.py`:

```python
from __future__ import annotations

import pytest

from marketsearch.notify.render import (
    ChangeCard,
    MatchCard,
    download_photos,
    render_email,
    render_sms,
)
from marketsearch.store import ExtractionRow, ListingRow


def listing(listing_id="1", title="2019 Bobcat T770", price_cents=3_800_000) -> ListingRow:
    return ListingRow(
        listing_id=listing_id, search_name="bobcat-t770", title=title,
        price_cents=price_cents, location="Olathe, KS",
        url=f"https://www.facebook.com/marketplace/item/{listing_id}/",
        thumbnail_url=None, seller_name="Dale S", fingerprint="fp",
        stage="matched", reject_reason=None, watched=False,
        first_seen_at="2026-07-26T10:00:00+00:00",
        last_seen_at="2026-07-26T10:00:00+00:00", last_change_check_at=None,
    )


def extraction(verdict="match", unknowns=None) -> ExtractionRow:
    return ExtractionRow(
        listing_id="1",
        attributes={
            "core": {"year": 2019, "make_model": "Bobcat T770", "engine_hours": 2400,
                     "asking_price": 38000, "location": "Olathe, KS"},
            "specs": {"cab_enclosed": True, "has_ac": True, "two_speed": True,
                      "high_flow": False, "tracks_or_tires": "tracks",
                      "undercarriage_condition": "70% remaining", "aux_hydraulics": True},
            "condition": {"runs": True, "stated_issues": [], "recent_service": ["new filters"],
                          "damage_notes": None, "one_owner_claim": True},
            "deal": {"attachments": ["bucket", "forks"], "seller_type": "private",
                     "financing_or_trade": False, "price_vs_market_note": "fair"},
        },
        verdict=verdict, confidence=0.9,
        reasoning="2,400 hours is under the limit and 2-speed is confirmed.",
        unknowns=unknowns or [], model="claude-opus-5",
        created_at="2026-07-26T10:00:00+00:00",
    )


def match(photos=None) -> MatchCard:
    return MatchCard(listing=listing(), extraction=extraction(), photos=photos or [])


def test_subject_counts_matches():
    email = render_email([match(), match()], [], [])
    assert "2 new matches" in email.subject


def test_subject_is_singular_for_one_match():
    email = render_email([match()], [], [])
    assert "1 new match" in email.subject
    assert "matches" not in email.subject


def test_subject_mentions_changes():
    change = ChangeCard(listing=listing(), kind="price_change",
                        old_price_cents=4_100_000, new_price_cents=3_800_000)
    email = render_email([], [], [change])
    assert "1 price change" in email.subject


def test_body_shows_price_hours_and_reasoning():
    html = render_email([match()], [], []).html
    assert "$38,000" in html
    assert "2,400" in html
    assert "2-speed is confirmed" in html


def test_body_links_to_the_listing():
    html = render_email([match()], [], []).html
    assert "https://www.facebook.com/marketplace/item/1/" in html


def test_body_shows_attributes_table():
    html = render_email([match()], [], []).html
    for label in ("Hours", "Year", "Cab", "2-speed", "Attachments", "Seller"):
        assert label in html


def test_unverified_section_names_the_gap():
    card = MatchCard(
        listing=listing(),
        extraction=extraction(verdict="unverifiable", unknowns=["engine_hours"]),
        photos=[],
    )
    html = render_email([], [card], []).html
    assert "Unverified" in html
    assert "engine_hours" in html


def test_price_drop_shows_both_prices():
    change = ChangeCard(listing=listing(), kind="price_change",
                        old_price_cents=4_100_000, new_price_cents=3_800_000)
    html = render_email([], [], [change]).html
    assert "$41,000" in html
    assert "$38,000" in html


def test_removed_listing_says_likely_sold():
    change = ChangeCard(listing=listing(), kind="removed",
                        old_price_cents=3_800_000, new_price_cents=None)
    html = render_email([], [], [change]).html
    assert "likely sold" in html.lower()


def test_photos_become_cid_references():
    email = render_email([match(photos=[b"\x89PNG-a", b"\x89PNG-b"])], [], [])
    assert len(email.images) == 2
    for cid, _data in email.images:
        assert f'cid:{cid}' in email.html


def test_photo_cids_are_unique_across_cards():
    a = MatchCard(listing=listing("1"), extraction=extraction(), photos=[b"a"])
    b = MatchCard(listing=listing("2"), extraction=extraction(), photos=[b"b"])
    email = render_email([a, b], [], [])
    cids = [cid for cid, _ in email.images]
    assert len(set(cids)) == 2


def test_card_without_photos_still_renders():
    html = render_email([match(photos=[])], [], []).html
    assert "2019 Bobcat T770" in html


def test_html_escapes_listing_titles():
    """A seller-controlled title must not be able to inject markup."""
    card = MatchCard(
        listing=listing(title='T770 <script>alert("x")</script>'),
        extraction=extraction(), photos=[],
    )
    html = render_email([card], [], []).html
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_sms_is_short_and_mentions_counts():
    text = render_sms([match(), match()], [match()], [])
    assert len(text) <= 160
    assert "2" in text
    assert "check email" in text.lower()


def test_sms_mentions_changes_when_there_are_no_matches():
    change = ChangeCard(listing=listing(), kind="price_change",
                        old_price_cents=4_100_000, new_price_cents=3_800_000)
    text = render_sms([], [], [change])
    assert "1 change" in text


def test_download_photos_respects_the_limit():
    calls: list[str] = []

    def get(url: str) -> bytes:
        calls.append(url)
        return b"img"

    urls = [f"https://example.com/{i}.jpg" for i in range(10)]
    assert len(download_photos(urls, limit=3, get=get)) == 3
    assert len(calls) == 3


def test_download_photos_skips_failures_without_raising():
    def get(url: str) -> bytes:
        if "bad" in url:
            raise RuntimeError("404")
        return b"img"

    photos = download_photos(
        ["https://example.com/bad.jpg", "https://example.com/good.jpg"], limit=3, get=get
    )
    assert photos == [b"img"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.notify'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/notify/__init__.py` (empty file).

Create `src/marketsearch/notify/render.py`:

```python
"""Turn verdicts and changes into one email and one short SMS.

Photos are embedded as CID attachments rather than linked. Facebook's image
URLs are signed and expire within days, so a linked email decays into broken
image boxes — including months later, when you are trying to remember what the
machine looked like.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Callable

from jinja2 import Environment, select_autoescape

from marketsearch.store import ExtractionRow, ListingRow

log = logging.getLogger(__name__)

PHOTO_LIMIT = 3
_PHOTO_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class MatchCard:
    listing: ListingRow
    extraction: ExtractionRow
    photos: list[bytes]


@dataclass(frozen=True)
class ChangeCard:
    listing: ListingRow
    kind: str  # "price_change" | "removed"
    old_price_cents: int | None
    new_price_cents: int | None


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    images: list[tuple[str, bytes]]


def _dollars(cents: int | None) -> str:
    return "—" if cents is None else f"${cents / 100:,.0f}"


def _yes_no(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _hours(attributes: dict) -> str:
    hours = attributes.get("core", {}).get("engine_hours")
    return "—" if hours is None else f"{hours:,}"


def _attribute_rows(attributes: dict) -> list[tuple[str, str]]:
    core = attributes.get("core", {})
    specs = attributes.get("specs", {})
    condition = attributes.get("condition", {})
    deal = attributes.get("deal", {})
    return [
        ("Hours", _hours(attributes)),
        ("Year", _yes_no(core.get("year"))),
        ("Cab", _yes_no(specs.get("cab_enclosed"))),
        ("A/C", _yes_no(specs.get("has_ac"))),
        ("2-speed", _yes_no(specs.get("two_speed"))),
        ("High flow", _yes_no(specs.get("high_flow"))),
        ("Undercarriage", _yes_no(specs.get("undercarriage_condition"))),
        ("Runs", _yes_no(condition.get("runs"))),
        ("Issues", ", ".join(condition.get("stated_issues") or []) or "none stated"),
        ("Attachments", ", ".join(deal.get("attachments") or []) or "none stated"),
        ("Seller", _yes_no(deal.get("seller_type"))),
    ]


_TEMPLATE = """\
<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;
                   color:#1c1e21;max-width:680px;margin:0 auto;padding:16px">
{% macro card(item) %}
  <div style="border:1px solid #dddfe2;border-radius:8px;padding:16px;margin-bottom:20px">
    <h2 style="margin:0 0 4px;font-size:18px">{{ item.title }}</h2>
    <div style="font-size:22px;font-weight:600;margin-bottom:2px">{{ item.price }}</div>
    <div style="color:#65676b;font-size:13px;margin-bottom:12px">{{ item.location }}</div>
    {% if item.unknowns %}
      <div style="background:#fff3cd;border-radius:6px;padding:8px 10px;margin-bottom:12px;
                  font-size:13px">
        Unverified — not stated in the listing: {{ item.unknowns }}
      </div>
    {% endif %}
    {% if item.cids %}
      <div style="margin-bottom:12px">
        {% for cid in item.cids %}
          <img src="cid:{{ cid }}" style="max-width:200px;border-radius:6px;
                                          margin-right:6px;vertical-align:top">
        {% endfor %}
      </div>
    {% endif %}
    <table style="border-collapse:collapse;font-size:13px;margin-bottom:12px">
      {% for label, value in item.rows %}
        <tr>
          <td style="padding:3px 14px 3px 0;color:#65676b;white-space:nowrap">{{ label }}</td>
          <td style="padding:3px 0">{{ value }}</td>
        </tr>
      {% endfor %}
    </table>
    <div style="font-size:13px;font-style:italic;color:#65676b;margin-bottom:14px">
      {{ item.reasoning }}
    </div>
    <a href="{{ item.url }}"
       style="display:inline-block;background:#1877f2;color:#fff;text-decoration:none;
              padding:9px 18px;border-radius:6px;font-size:14px">View on Marketplace</a>
  </div>
{% endmacro %}

{% if matches %}
  <h1 style="font-size:20px">{{ matches|length }} new
    match{{ '' if matches|length == 1 else 'es' }}</h1>
  {% for item in matches %}{{ card(item) }}{% endfor %}
{% endif %}

{% if unverified %}
  <h1 style="font-size:20px">Unverified</h1>
  <p style="font-size:13px;color:#65676b">
    These cleared price and keyword filters, but the listing does not state
    everything your criteria ask about.
  </p>
  {% for item in unverified %}{{ card(item) }}{% endfor %}
{% endif %}

{% if changes %}
  <h1 style="font-size:20px">Watched listings</h1>
  {% for change in changes %}
    <div style="border-left:3px solid #1877f2;padding:8px 0 8px 12px;margin-bottom:14px">
      <div style="font-weight:600">{{ change.headline }}</div>
      <div style="font-size:13px;color:#65676b;margin-bottom:6px">{{ change.detail }}</div>
      <a href="{{ change.url }}" style="font-size:13px;color:#1877f2">View on Marketplace</a>
    </div>
  {% endfor %}
{% endif %}
</body></html>
"""

_ENV = Environment(autoescape=select_autoescape(default=True, default_for_string=True))


def _card_context(card: MatchCard) -> tuple[dict, list[tuple[str, bytes]]]:
    images: list[tuple[str, bytes]] = []
    cids: list[str] = []
    for photo in card.photos:
        cid = f"photo-{uuid.uuid4().hex}"
        cids.append(cid)
        images.append((cid, photo))

    context = {
        "title": card.listing.title,
        "price": _dollars(card.listing.price_cents),
        "location": card.listing.location or "location not stated",
        "url": card.listing.url,
        "rows": _attribute_rows(card.extraction.attributes),
        "reasoning": card.extraction.reasoning,
        "unknowns": ", ".join(card.extraction.unknowns) if card.extraction.unknowns else "",
        "cids": cids,
    }
    return context, images


def _change_context(change: ChangeCard) -> dict:
    if change.kind == "removed":
        return {
            "headline": f"Listing removed (likely sold): {change.listing.title}",
            "detail": f"Last seen at {_dollars(change.old_price_cents)}.",
            "url": change.listing.url,
        }
    direction = "Price drop" if (
        change.old_price_cents is not None
        and change.new_price_cents is not None
        and change.new_price_cents < change.old_price_cents
    ) else "Price change"
    return {
        "headline": f"{direction}: {change.listing.title}",
        "detail": f"{_dollars(change.old_price_cents)} → {_dollars(change.new_price_cents)}",
        "url": change.listing.url,
    }


def _subject(n_matches: int, n_unverified: int, n_changes: int) -> str:
    parts: list[str] = []
    if n_matches:
        parts.append(f"{n_matches} new match{'' if n_matches == 1 else 'es'}")
    if n_unverified:
        parts.append(f"{n_unverified} unverified")
    if n_changes:
        parts.append(f"{n_changes} price change{'' if n_changes == 1 else 's'}")
    return "MarketSearch: " + (", ".join(parts) if parts else "nothing new")


def render_email(
    matches: list[MatchCard], unverified: list[MatchCard], changes: list[ChangeCard]
) -> RenderedEmail:
    images: list[tuple[str, bytes]] = []

    match_ctx = []
    for card in matches:
        context, card_images = _card_context(card)
        match_ctx.append(context)
        images.extend(card_images)

    unverified_ctx = []
    for card in unverified:
        context, card_images = _card_context(card)
        unverified_ctx.append(context)
        images.extend(card_images)

    html = _ENV.from_string(_TEMPLATE).render(
        matches=match_ctx,
        unverified=unverified_ctx,
        changes=[_change_context(c) for c in changes],
    )
    return RenderedEmail(
        subject=_subject(len(matches), len(unverified), len(changes)),
        html=html,
        images=images,
    )


def render_sms(
    matches: list[MatchCard], unverified: list[MatchCard], changes: list[ChangeCard]
) -> str:
    parts: list[str] = []
    if matches:
        names = sorted({c.listing.search_name for c in matches})
        parts.append(f"{len(matches)} new match{'' if len(matches) == 1 else 'es'} "
                     f"({', '.join(names)})")
    if unverified:
        parts.append(f"{len(unverified)} unverified")
    if changes:
        parts.append(f"{len(changes)} change{'' if len(changes) == 1 else 's'}")
    body = ", ".join(parts) if parts else "activity"
    return f"MarketSearch: {body} — check email."[:160]


def download_photos(
    urls: list[str], limit: int = PHOTO_LIMIT, get: Callable[[str], bytes] | None = None
) -> list[bytes]:
    """Fetch up to `limit` photos. A failure drops that photo rather than the
    whole alert — a card without pictures still tells you what you need."""
    if get is None:
        import httpx

        def get(url: str) -> bytes:  # type: ignore[misc]
            response = httpx.get(url, timeout=_PHOTO_TIMEOUT_S, follow_redirects=True)
            response.raise_for_status()
            return response.content

    photos: list[bytes] = []
    for url in urls[:limit]:
        try:
            photos.append(get(url))
        except Exception as exc:
            log.warning("photo download failed for %s: %s", url, exc)
    return photos
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_render.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/notify tests/test_render.py
git commit -m "feat: html email rendering with inline photos and sms nudge"
```

---

## Task 13: Delivery and notification idempotency

**Files:**
- Create: `src/marketsearch/notify/delivery.py`
- Test: `tests/test_delivery.py`

**Interfaces:**
- Consumes: `RenderedEmail` (Task 12), `Config` (Task 2), `Store` (Task 5)
- Produces:
  - `build_mime(email: RenderedEmail, from_addr: str, to_addr: str) -> EmailMessage`
  - `EmailSender(config: EmailConfig, password: str, smtp_factory=None)` with `.send(email: RenderedEmail) -> None`
  - `SmsSender(config: SmsConfig, account_sid: str, auth_token: str, client_factory=None)` with `.send(text: str) -> None`
  - `Dispatcher(store, email_sender, sms_sender, enabled: bool)` with `.dispatch(matches, unverified, changes) -> bool` (returns whether anything was sent)
  - `DeliveryError(Exception)`
  - `resolve_secret(env_name: str) -> str`

`Dispatcher` owns the two rules that keep the inbox trustworthy: **nothing is sent when `enabled` is False**, and **a listing is never alerted on twice for the same kind**, because `notifications` rows are only written on a confirmed send.

- [ ] **Step 1: Write the failing test**

Create `tests/test_delivery.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.config import EmailConfig, SmsConfig
from marketsearch.notify.delivery import (
    DeliveryError,
    Dispatcher,
    EmailSender,
    SmsSender,
    build_mime,
    resolve_secret,
)
from marketsearch.notify.render import ChangeCard, MatchCard, RenderedEmail
from marketsearch.models import RawListing
from marketsearch.store import ExtractionRow, ListingRow, Store


def email_config() -> EmailConfig:
    return EmailConfig.model_validate({
        "to": "me@example.com", "from": "bot@example.com",
        "smtp_host": "smtp.example.com", "smtp_port": 587,
        "username": "bot@example.com", "password_env": "TEST_SMTP_PASSWORD",
    })


def sms_config() -> SmsConfig:
    return SmsConfig(to="+15555550100", twilio_from="+15555550101",
                     account_sid_env="TEST_SID", auth_token_env="TEST_TOKEN")


def rendered(images=None) -> RenderedEmail:
    return RenderedEmail(subject="MarketSearch: 1 new match",
                         html="<html><body>hi <img src='cid:photo-1'></body></html>",
                         images=images if images is not None else [("photo-1", b"\x89PNG")])


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float = 30.0):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in_as: str | None = None
        self.sent: list[object] = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username: str, password: str):
        self.logged_in_as = username

    def send_message(self, message):
        self.sent.append(message)


class FakeTwilio:
    def __init__(self, sid: str, token: str):
        self.sid = sid
        self.sent: list[dict] = []
        self.messages = self

    def create(self, body: str, from_: str, to: str):
        self.sent.append({"body": body, "from_": from_, "to": to})


@pytest.fixture(autouse=True)
def _reset_smtp():
    FakeSMTP.instances.clear()


def test_resolve_secret_reads_the_environment(monkeypatch):
    monkeypatch.setenv("SOME_SECRET", "abc123")
    assert resolve_secret("SOME_SECRET") == "abc123"


def test_resolve_secret_raises_a_clear_error_when_missing(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    with pytest.raises(DeliveryError, match="MISSING_SECRET"):
        resolve_secret("MISSING_SECRET")


def test_build_mime_sets_headers_and_html():
    message = build_mime(rendered(), "bot@example.com", "me@example.com")
    assert message["Subject"] == "MarketSearch: 1 new match"
    assert message["From"] == "bot@example.com"
    assert message["To"] == "me@example.com"


def test_build_mime_attaches_images_with_matching_cids():
    message = build_mime(rendered(), "bot@example.com", "me@example.com")
    cids = [part["Content-ID"] for part in message.walk() if part.get("Content-ID")]
    assert "<photo-1>" in cids


def test_build_mime_without_images_is_still_valid():
    message = build_mime(rendered(images=[]), "bot@example.com", "me@example.com")
    assert message["Subject"]


def test_email_sender_uses_starttls_and_logs_in():
    sender = EmailSender(email_config(), password="secret", smtp_factory=FakeSMTP)
    sender.send(rendered())
    smtp = FakeSMTP.instances[0]
    assert smtp.started_tls is True
    assert smtp.logged_in_as == "bot@example.com"
    assert len(smtp.sent) == 1


def test_email_sender_wraps_failures_in_delivery_error():
    class Boom(FakeSMTP):
        def send_message(self, message):
            raise OSError("connection reset")

    sender = EmailSender(email_config(), password="secret", smtp_factory=Boom)
    with pytest.raises(DeliveryError, match="email send failed"):
        sender.send(rendered())


def test_sms_sender_sends_body_from_and_to():
    fake = FakeTwilio("sid", "token")
    sender = SmsSender(sms_config(), "sid", "token", client_factory=lambda s, t: fake)
    sender.send("MarketSearch: 1 new match — check email.")
    assert fake.sent[0]["to"] == "+15555550100"
    assert fake.sent[0]["from_"] == "+15555550101"
    assert "1 new match" in fake.sent[0]["body"]
```

Continue the same file with the `Dispatcher` tests:

```python
def listing_row(listing_id="1") -> ListingRow:
    return ListingRow(
        listing_id=listing_id, search_name="bobcat-t770", title="2019 Bobcat T770",
        price_cents=3_800_000, location="Olathe, KS",
        url=f"https://www.facebook.com/marketplace/item/{listing_id}/",
        thumbnail_url=None, seller_name="Dale S", fingerprint="fp", stage="matched",
        reject_reason=None, watched=False, first_seen_at="2026-07-26T10:00:00+00:00",
        last_seen_at="2026-07-26T10:00:00+00:00", last_change_check_at=None,
    )


def extraction_row(listing_id="1") -> ExtractionRow:
    return ExtractionRow(
        listing_id=listing_id, attributes={"core": {"engine_hours": 2400}},
        verdict="match", confidence=0.9, reasoning="under the limit",
        unknowns=[], model="claude-opus-5", created_at="2026-07-26T10:00:00+00:00",
    )


class RecordingEmail:
    def __init__(self):
        self.sent: list[RenderedEmail] = []

    def send(self, email: RenderedEmail) -> None:
        self.sent.append(email)


class RecordingSms:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "d.db")
    s.initialize()
    for listing_id in ("1", "2"):
        s.upsert_listing(
            RawListing(listing_id=listing_id, title="T770", price_cents=3_800_000,
                       location=None, url=f"https://example.com/{listing_id}",
                       thumbnail_url=None, seller_name=None),
            "bobcat-t770", "fp",
        )
    yield s
    s.close()


def make_dispatcher(store, enabled=True):
    email, sms = RecordingEmail(), RecordingSms()
    return Dispatcher(store, email, sms, enabled=enabled), email, sms


def test_dispatch_sends_one_email_and_one_sms(store: Store):
    dispatcher, email, sms = make_dispatcher(store)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    assert dispatcher.dispatch([card], [], []) is True
    assert len(email.sent) == 1
    assert len(sms.sent) == 1


def test_dispatch_sends_nothing_when_disabled(store: Store):
    dispatcher, email, sms = make_dispatcher(store, enabled=False)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    assert dispatcher.dispatch([card], [], []) is False
    assert email.sent == []
    assert sms.sent == []


def test_dispatch_sends_nothing_when_there_is_nothing_to_say(store: Store):
    dispatcher, email, sms = make_dispatcher(store)
    assert dispatcher.dispatch([], [], []) is False
    assert email.sent == []


def test_already_notified_listings_are_filtered_out(store: Store):
    dispatcher, email, _ = make_dispatcher(store)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    dispatcher.dispatch([card], [], [])
    dispatcher2, email2, _ = make_dispatcher(store)
    assert dispatcher2.dispatch([card], [], []) is False
    assert email2.sent == []


def test_new_listing_still_sends_when_another_was_already_notified(store: Store):
    dispatcher, email, _ = make_dispatcher(store)
    first = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    dispatcher.dispatch([first], [], [])

    dispatcher2, email2, _ = make_dispatcher(store)
    second = MatchCard(listing=listing_row("2"), extraction=extraction_row("2"), photos=[])
    assert dispatcher2.dispatch([first, second], [], []) is True
    assert "2 new" not in email2.sent[0].subject  # only the unseen one is included


def test_notification_rows_are_written_only_after_a_successful_send(store: Store):
    class Failing:
        def send(self, email):
            raise DeliveryError("smtp down")

    dispatcher = Dispatcher(store, Failing(), RecordingSms(), enabled=True)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    with pytest.raises(DeliveryError):
        dispatcher.dispatch([card], [], [])
    assert store.already_notified("1", "email", "match") is False


def test_sms_failure_does_not_undo_a_successful_email(store: Store):
    class FailingSms:
        def send(self, text):
            raise DeliveryError("twilio down")

    email = RecordingEmail()
    dispatcher = Dispatcher(store, email, FailingSms(), enabled=True)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    assert dispatcher.dispatch([card], [], []) is True
    assert len(email.sent) == 1
    assert store.already_notified("1", "email", "match") is True
    assert store.already_notified("1", "sms", "match") is False


def test_price_change_and_match_are_tracked_separately(store: Store):
    dispatcher, _, _ = make_dispatcher(store)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    dispatcher.dispatch([card], [], [])

    dispatcher2, email2, _ = make_dispatcher(store)
    change = ChangeCard(listing=listing_row("1"), kind="price_change",
                        old_price_cents=4_100_000, new_price_cents=3_800_000)
    assert dispatcher2.dispatch([], [], [change]) is True
    assert len(email2.sent) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_delivery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.notify.delivery'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/notify/delivery.py`:

```python
"""Sending, and the rules that keep the inbox worth reading.

Two invariants:
  * nothing is sent unless notifications are explicitly enabled;
  * a listing is never alerted on twice for the same reason, because the
    notifications table is only written after a confirmed send.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Callable, Protocol

from marketsearch.config import EmailConfig, SmsConfig
from marketsearch.notify.render import (
    ChangeCard,
    MatchCard,
    RenderedEmail,
    render_email,
    render_sms,
)
from marketsearch.store import Store

log = logging.getLogger(__name__)

_SMTP_TIMEOUT_S = 30.0


class DeliveryError(Exception):
    """A notification could not be delivered."""


def resolve_secret(env_name: str) -> str:
    value = os.environ.get(env_name)
    if not value:
        raise DeliveryError(
            f"environment variable {env_name} is not set — add it to your .env file"
        )
    return value


def build_mime(email: RenderedEmail, from_addr: str, to_addr: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = email.subject
    message["From"] = from_addr
    message["To"] = to_addr
    message.set_content(
        "This message contains listing photos and formatting. "
        "View it in an HTML-capable mail client."
    )
    message.add_alternative(email.html, subtype="html")

    html_part = message.get_payload()[-1]
    for cid, data in email.images:
        html_part.add_related(data, maintype="image", subtype="jpeg", cid=f"<{cid}>")
    return message


class EmailSender:
    def __init__(
        self,
        config: EmailConfig,
        password: str,
        smtp_factory: Callable[..., smtplib.SMTP] | None = None,
    ) -> None:
        self._config = config
        self._password = password
        self._smtp_factory = smtp_factory or smtplib.SMTP

    def send(self, email: RenderedEmail) -> None:
        message = build_mime(email, self._config.from_, self._config.to)
        try:
            with self._smtp_factory(
                self._config.smtp_host, self._config.smtp_port, timeout=_SMTP_TIMEOUT_S
            ) as smtp:
                smtp.starttls()
                smtp.login(self._config.username, self._password)
                smtp.send_message(message)
        except Exception as exc:
            raise DeliveryError(f"email send failed: {exc}") from exc
        log.info("sent email: %s", email.subject)


class SmsSender:
    def __init__(
        self,
        config: SmsConfig,
        account_sid: str,
        auth_token: str,
        client_factory: Callable[[str, str], object] | None = None,
    ) -> None:
        self._config = config
        self._sid = account_sid
        self._token = auth_token
        self._client_factory = client_factory

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory(self._sid, self._token)
        from twilio.rest import Client

        return Client(self._sid, self._token)

    def send(self, text: str) -> None:
        try:
            self._client().messages.create(
                body=text, from_=self._config.twilio_from, to=self._config.to
            )
        except Exception as exc:
            raise DeliveryError(f"sms send failed: {exc}") from exc
        log.info("sent sms: %s", text)


class _EmailChannel(Protocol):
    def send(self, email: RenderedEmail) -> None: ...


class _SmsChannel(Protocol):
    def send(self, text: str) -> None: ...


class Dispatcher:
    def __init__(
        self,
        store: Store,
        email_sender: _EmailChannel,
        sms_sender: _SmsChannel,
        enabled: bool,
    ) -> None:
        self._store = store
        self._email = email_sender
        self._sms = sms_sender
        self._enabled = enabled

    def dispatch(
        self,
        matches: list[MatchCard],
        unverified: list[MatchCard],
        changes: list[ChangeCard],
    ) -> bool:
        """Send at most one email and one SMS. Returns True if anything went out."""
        matches = [c for c in matches
                   if not self._store.already_notified(c.listing.listing_id, "email", "match")]
        unverified = [c for c in unverified
                      if not self._store.already_notified(
                          c.listing.listing_id, "email", "unverified")]
        changes = [c for c in changes
                   if not self._store.already_notified(
                       c.listing.listing_id, "email", c.kind)]

        if not (matches or unverified or changes):
            return False

        if not self._enabled:
            log.info(
                "notifications disabled — would have sent %d match(es), %d unverified, "
                "%d change(s)",
                len(matches), len(unverified), len(changes),
            )
            return False

        self._email.send(render_email(matches, unverified, changes))

        for card in matches:
            self._store.record_notification(card.listing.listing_id, "email", "match", "sent")
        for card in unverified:
            self._store.record_notification(
                card.listing.listing_id, "email", "unverified", "sent")
        for change in changes:
            self._store.record_notification(
                change.listing.listing_id, "email", change.kind, "sent")

        # The SMS is only a nudge. Losing it must not cost the email, whose
        # notification rows are already committed above.
        try:
            self._sms.send(render_sms(matches, unverified, changes))
        except DeliveryError as exc:
            log.warning("sms nudge failed (email was delivered): %s", exc)
        else:
            for card in matches:
                self._store.record_notification(
                    card.listing.listing_id, "sms", "match", "sent")

        return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_delivery.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/notify/delivery.py tests/test_delivery.py
git commit -m "feat: smtp and twilio delivery with per-listing idempotency"
```

---

## Task 14: Run state, locking, and operational alert throttling

**Files:**
- Create: `src/marketsearch/runstate.py`, `src/marketsearch/logging_setup.py`
- Test: `tests/test_runstate.py`

**Interfaces:**
- Consumes: `Store` (Task 5)
- Produces:
  - `AlreadyRunning(Exception)`
  - `RunLock(path: Path, stale_after_hours: int = 6)` — context manager, raises `AlreadyRunning`
  - `set_needs_login(store, kind: str) -> None`, `clear_needs_login(store) -> bool`, `needs_login(store) -> str | None`
  - `OperationalAlerts(store, now: Callable[[], datetime] | None = None)` with `.should_send(problem: str) -> bool`, `.mark_sent(problem: str) -> None`, `.clear(problem: str) -> bool`
  - `setup_logging(log_path: Path, verbose: bool = False) -> None`

**Why throttling matters.** A tool that emails every 45 minutes about being broken gets filtered to spam — and then the real matches go unseen too. Operational problems alert once per 24 hours, with a single "back to normal" message when they clear.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runstate.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketsearch.runstate import (
    AlreadyRunning,
    OperationalAlerts,
    RunLock,
    clear_needs_login,
    needs_login,
    set_needs_login,
)
from marketsearch.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "r.db")
    s.initialize()
    yield s
    s.close()


def test_lock_is_acquired_and_released(tmp_path: Path):
    path = tmp_path / "run.lock"
    with RunLock(path):
        assert path.exists()
    assert not path.exists()


def test_second_lock_raises_already_running(tmp_path: Path):
    path = tmp_path / "run.lock"
    with RunLock(path):
        with pytest.raises(AlreadyRunning):
            with RunLock(path):
                pass


def test_lock_released_even_when_body_raises(tmp_path: Path):
    path = tmp_path / "run.lock"
    with pytest.raises(ValueError):
        with RunLock(path):
            raise ValueError("boom")
    assert not path.exists()


def test_stale_lock_is_reclaimed(tmp_path: Path):
    """A machine that lost power mid-run must not be wedged forever."""
    path = tmp_path / "run.lock"
    path.write_text("99999", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(hours=12)).timestamp()
    import os
    os.utime(path, (old, old))

    with RunLock(path, stale_after_hours=6):
        assert path.exists()


def test_fresh_lock_is_not_reclaimed(tmp_path: Path):
    path = tmp_path / "run.lock"
    path.write_text("99999", encoding="utf-8")
    with pytest.raises(AlreadyRunning):
        with RunLock(path, stale_after_hours=6):
            pass


def test_needs_login_roundtrip(store: Store):
    assert needs_login(store) is None
    set_needs_login(store, "checkpoint")
    assert needs_login(store) == "checkpoint"


def test_clear_needs_login_reports_whether_it_was_set(store: Store):
    assert clear_needs_login(store) is False
    set_needs_login(store, "login")
    assert clear_needs_login(store) is True
    assert needs_login(store) is None


def test_first_operational_alert_is_allowed(store: Store):
    alerts = OperationalAlerts(store)
    assert alerts.should_send("parse_failure") is True


def test_second_alert_within_24h_is_suppressed(store: Store):
    alerts = OperationalAlerts(store)
    alerts.mark_sent("parse_failure")
    assert alerts.should_send("parse_failure") is False


def test_alert_is_allowed_again_after_24h(store: Store):
    now = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
    alerts = OperationalAlerts(store, now=lambda: now)
    alerts.mark_sent("parse_failure")

    later = now + timedelta(hours=25)
    alerts_later = OperationalAlerts(store, now=lambda: later)
    assert alerts_later.should_send("parse_failure") is True


def test_different_problems_throttle_independently(store: Store):
    alerts = OperationalAlerts(store)
    alerts.mark_sent("parse_failure")
    assert alerts.should_send("needs_login") is True


def test_clear_reports_whether_an_all_clear_is_warranted(store: Store):
    alerts = OperationalAlerts(store)
    assert alerts.clear("parse_failure") is False
    alerts.mark_sent("parse_failure")
    assert alerts.clear("parse_failure") is True
    assert alerts.clear("parse_failure") is False
    assert alerts.should_send("parse_failure") is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_runstate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.runstate'`

- [ ] **Step 3: Write `runstate.py`**

Create `src/marketsearch/runstate.py`:

```python
"""Operational state: the run lock, the needs-login flag, and alert throttling."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from marketsearch.store import Store

log = logging.getLogger(__name__)

NEEDS_LOGIN_KEY = "needs_login"
_ALERT_KEY_PREFIX = "alert_last_sent:"
_ALERT_INTERVAL_HOURS = 24


class AlreadyRunning(Exception):
    """Another sweep is in progress."""


class RunLock:
    """Prevents a slow sweep from colliding with the next scheduled one.

    A lock older than `stale_after_hours` is reclaimed, so a machine that lost
    power mid-run is not wedged until someone notices.
    """

    def __init__(self, path: Path, stale_after_hours: int = 6) -> None:
        self.path = Path(path)
        self.stale_after_hours = stale_after_hours
        self._held = False

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not self._is_stale():
                raise AlreadyRunning(f"another run holds {self.path}") from None
            log.warning("reclaiming stale lock at %s", self.path)
            self.path.unlink(missing_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        self._held = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def _is_stale(self) -> bool:
        try:
            age = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
        except FileNotFoundError:
            return True
        return age > self.stale_after_hours * 3600


def set_needs_login(store: Store, kind: str) -> None:
    store.set_state(NEEDS_LOGIN_KEY, kind)


def needs_login(store: Store) -> str | None:
    value = store.get_state(NEEDS_LOGIN_KEY)
    return value or None


def clear_needs_login(store: Store) -> bool:
    """Clear the flag. Returns True if it had been set."""
    was_set = needs_login(store) is not None
    store.set_state(NEEDS_LOGIN_KEY, "")
    return was_set


class OperationalAlerts:
    """One alert per problem per 24 hours, plus one all-clear when it resolves."""

    def __init__(self, store: Store, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _key(self, problem: str) -> str:
        return f"{_ALERT_KEY_PREFIX}{problem}"

    def should_send(self, problem: str) -> bool:
        raw = self._store.get_state(self._key(problem))
        if not raw:
            return True
        last = datetime.fromisoformat(raw)
        return self._now() - last >= timedelta(hours=_ALERT_INTERVAL_HOURS)

    def mark_sent(self, problem: str) -> None:
        self._store.set_state(self._key(problem), self._now().isoformat())

    def clear(self, problem: str) -> bool:
        """Reset the throttle. Returns True if an alert was outstanding, which
        is the caller's cue to send a single 'back to normal' message."""
        was_active = bool(self._store.get_state(self._key(problem)))
        self._store.set_state(self._key(problem), "")
        return was_active
```

- [ ] **Step 4: Write `logging_setup.py`**

Create `src/marketsearch/logging_setup.py`:

```python
"""Rotating file log plus console output."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MAX_BYTES = 2_000_000
_BACKUPS = 5


def setup_logging(log_path: Path, verbose: bool = False) -> None:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(console)

    # Playwright and httpx are chatty at DEBUG.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_runstate.py -v`
Expected: 12 passed

- [ ] **Step 6: Commit**

```bash
git add src/marketsearch/runstate.py src/marketsearch/logging_setup.py tests/test_runstate.py
git commit -m "feat: run lock, needs-login flag, and operational alert throttling"
```

---

## Task 15: Scan pipeline

**Files:**
- Create: `src/marketsearch/pipeline.py`
- Test: `tests/test_pipeline_scan.py`

**Interfaces:**
- Consumes: `Config` (Task 2), `Store` (Task 5), `prefilter` (Task 6), `Extractor`/`ExtractionError` (Task 8), `ListingSource`/`ParseError` (Task 10), `MatchCard`/`download_photos` (Task 12), `fingerprint` (Task 3)
- Produces:
  - `ScanCounters` dataclass: `found`, `new`, `prefiltered`, `extracted`, `matched`, `errors` (all `int`, default 0), plus `.as_dict() -> dict[str, int]`
  - `ScanOutcome` frozen dataclass: `.matches: list[MatchCard]`, `.unverified: list[MatchCard]`, `.counters: ScanCounters`
  - `Scanner(config, store, source, extractor, photo_fetcher=download_photos, dry_run=False)` with `.scan() -> ScanOutcome`
  - `RELIST_WINDOW_DAYS = 60`
  - `content_hash(detail: ListingDetail) -> str`
  - `listing_row_from(listing: RawListing, search_name: str, fp: str, stage: str) -> ListingRow`

**Ordering is load-bearing.** Dedupe before prefilter means a listing is examined once in its life. Prefilter before detail means no detail page — the most bot-visible action — is loaded for a machine outside the price band. Extraction runs only on listings that already cleared the cheap gates.

`LoginRequired` is deliberately **not** caught here. It must propagate to the caller, which stops the whole run.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_scan.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.config import Config
from marketsearch.extract import ExtractionError, ExtractionResult
from marketsearch.extraction_models import Extraction
from marketsearch.models import ListingDetail, RawListing
from marketsearch.pipeline import Scanner
from marketsearch.sources.base import LoginRequired, ParseError
from marketsearch.store import Store

CONFIG_DICT = {
    "account": {"profile_dir": "profile"},
    "location": {"anchor": "Olathe, KS", "radius_miles": 250},
    "extraction": {"model": "claude-opus-5", "effort": "low", "max_extractions_per_run": 25},
    "notifications": {
        "email": {"to": "a@b.c", "from": "d@e.f", "smtp_host": "h", "smtp_port": 587,
                  "username": "u", "password_env": "P"},
        "sms": {"to": "+1", "twilio_from": "+2", "account_sid_env": "S",
                "auth_token_env": "T"},
    },
    "searches": [{
        "name": "bobcat-t770", "query": "Bobcat T770",
        "price_min_cents": 1_500_000, "price_max_cents": 6_000_000,
        "title_must_match": ["t770"], "title_must_not_match": ["wanted"],
        "on_unknown": "alert", "criteria": "Under 3000 engine hours.",
    }],
}


def config(**overrides) -> Config:
    data = {**CONFIG_DICT, **overrides}
    return Config.model_validate(data)


def listing(listing_id: str, title="2019 Bobcat T770", price_cents=3_800_000) -> RawListing:
    return RawListing(
        listing_id=listing_id, title=title, price_cents=price_cents,
        location="Olathe, KS", url=f"https://example.com/{listing_id}",
        thumbnail_url=None, seller_name="Dale S",
    )


def extraction(verdict="match", unknowns=None) -> Extraction:
    return Extraction.model_validate({
        "core": {"year": 2019, "make_model": "Bobcat T770", "engine_hours": 2400,
                 "asking_price": 38000, "location": "Olathe, KS"},
        "specs": {"cab_enclosed": True, "has_ac": True, "two_speed": True,
                  "high_flow": False, "tracks_or_tires": "tracks",
                  "undercarriage_condition": "good", "aux_hydraulics": True},
        "condition": {"runs": True, "stated_issues": [], "recent_service": [],
                      "damage_notes": None, "one_owner_claim": False},
        "deal": {"attachments": [], "seller_type": "private",
                 "financing_or_trade": False, "price_vs_market_note": None},
        "verdict": verdict, "confidence": 0.9, "reasoning": "reasons",
        "unknowns": unknowns or [],
    })


class FakeSource:
    def __init__(self, results=None, detail_error=None):
        self.results = results or []
        self.detail_error = detail_error
        self.detail_calls: list[str] = []

    def search(self, query, location, radius_miles):
        return list(self.results)

    def fetch_detail(self, listing_id):
        self.detail_calls.append(listing_id)
        if self.detail_error is not None:
            raise self.detail_error
        return ListingDetail(
            listing_id=listing_id, description="2400 hours",
            structured_fields={}, photo_urls=["https://example.com/p.jpg"],
            distance_miles=None,
        )

    def fetch_saved(self):
        return []


class FakeExtractor:
    def __init__(self, result=None, error=None):
        self._result = result or extraction()
        self._error = error
        self.calls = 0

    def extract(self, listing, detail, criteria):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return ExtractionResult(
            extraction=self._result, input_tokens=1500,
            output_tokens=400, cost_cents=1.75,
        )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "p.db")
    s.initialize()
    yield s
    s.close()


def scanner(store, source, extractor=None, cfg=None, **kwargs) -> Scanner:
    return Scanner(
        config=cfg or config(), store=store, source=source,
        extractor=extractor or FakeExtractor(),
        photo_fetcher=lambda urls, limit=3: [b"img"],
        **kwargs,
    )


def test_a_matching_listing_produces_a_match_card(store: Store):
    outcome = scanner(store, FakeSource([listing("1")])).scan()
    assert len(outcome.matches) == 1
    assert outcome.matches[0].listing.listing_id == "1"
    assert outcome.counters.matched == 1


def test_match_card_carries_photos(store: Store):
    outcome = scanner(store, FakeSource([listing("1")])).scan()
    assert outcome.matches[0].photos == [b"img"]


def test_already_seen_listings_are_skipped_entirely(store: Store):
    source = FakeSource([listing("1")])
    extractor = FakeExtractor()
    scanner(store, source, extractor).scan()
    scanner(store, FakeSource([listing("1")]), extractor).scan()
    assert extractor.calls == 1


def test_prefiltered_listing_never_loads_a_detail_page(store: Store):
    source = FakeSource([listing("1", title="WANTED Bobcat T770")])
    outcome = scanner(store, source).scan()
    assert source.detail_calls == []
    assert outcome.counters.prefiltered == 1
    assert outcome.matches == []


def test_prefilter_reason_is_recorded(store: Store):
    scanner(store, FakeSource([listing("1", price_cents=9_000_000)])).scan()
    row = store.get_listing("1")
    assert row.stage == "prefiltered_out"
    assert "above" in row.reject_reason


def test_repost_within_the_window_is_suppressed(store: Store):
    scanner(store, FakeSource([listing("1")])).scan()
    reposted = listing("2")  # same title, price, seller, location -> same fingerprint
    outcome = scanner(store, FakeSource([reposted])).scan()
    assert outcome.matches == []
    assert "repost" in store.get_listing("2").reject_reason


def test_repost_at_a_lower_price_still_alerts(store: Store):
    scanner(store, FakeSource([listing("1", price_cents=4_100_000)])).scan()
    outcome = scanner(store, FakeSource([listing("2", price_cents=3_800_000)])).scan()
    assert len(outcome.matches) == 1


def test_unverifiable_verdict_goes_to_the_unverified_bucket(store: Store):
    extractor = FakeExtractor(extraction("unverifiable", ["engine_hours"]))
    outcome = scanner(store, FakeSource([listing("1")]), extractor).scan()
    assert outcome.matches == []
    assert len(outcome.unverified) == 1


def test_on_unknown_skip_drops_unverifiable_listings(store: Store):
    cfg = config(searches=[{**CONFIG_DICT["searches"][0], "on_unknown": "skip"}])
    extractor = FakeExtractor(extraction("unverifiable", ["engine_hours"]))
    outcome = scanner(store, FakeSource([listing("1")]), extractor, cfg=cfg).scan()
    assert outcome.unverified == []


def test_no_match_produces_no_card_but_is_recorded(store: Store):
    extractor = FakeExtractor(extraction("no_match"))
    outcome = scanner(store, FakeSource([listing("1")]), extractor).scan()
    assert outcome.matches == []
    assert outcome.unverified == []
    assert store.latest_extraction("1").verdict == "no_match"
    assert store.get_listing("1").stage == "extracted"


def test_extraction_failure_leaves_the_listing_pending_for_retry(store: Store):
    extractor = FakeExtractor(error=ExtractionError("api down"))
    outcome = scanner(store, FakeSource([listing("1")]), extractor).scan()
    assert outcome.counters.errors == 1
    assert store.get_listing("1").stage == "pending"


def test_detail_parse_failure_leaves_the_listing_pending(store: Store):
    source = FakeSource([listing("1")], detail_error=ParseError("markup changed"))
    outcome = scanner(store, source).scan()
    assert outcome.counters.errors == 1
    assert store.get_listing("1").stage == "pending"


def test_login_required_propagates_rather_than_being_swallowed(store: Store):
    class Blocked(FakeSource):
        def search(self, query, location, radius_miles):
            raise LoginRequired("checkpoint")

    with pytest.raises(LoginRequired):
        scanner(store, Blocked()).scan()


def test_extraction_budget_is_respected(store: Store):
    cfg = config(extraction={"model": "claude-opus-5", "effort": "low",
                             "max_extractions_per_run": 2})
    source = FakeSource([listing(str(i)) for i in range(5)])
    extractor = FakeExtractor()
    # Distinct prices so each listing has a distinct fingerprint.
    source.results = [listing(str(i), price_cents=3_000_000 + i * 10_000) for i in range(5)]
    scanner(store, source, extractor, cfg=cfg).scan()
    assert extractor.calls == 2


def test_listings_beyond_the_budget_stay_pending_for_the_next_run(store: Store):
    cfg = config(extraction={"model": "claude-opus-5", "effort": "low",
                             "max_extractions_per_run": 1})
    source = FakeSource([listing(str(i), price_cents=3_000_000 + i * 10_000)
                         for i in range(3)])
    scanner(store, source, cfg=cfg).scan()
    pending = [store.get_listing(str(i)).stage for i in range(3)]
    assert pending.count("pending") == 2


def test_dry_run_writes_nothing(store: Store):
    outcome = scanner(store, FakeSource([listing("1")]), dry_run=True).scan()
    assert len(outcome.matches) == 1
    assert store.get_listing("1") is None


def test_counters_reflect_the_sweep(store: Store):
    source = FakeSource([
        listing("1", price_cents=3_800_000),
        listing("2", title="WANTED Bobcat T770", price_cents=3_900_000),
    ])
    outcome = scanner(store, source).scan()
    assert outcome.counters.found == 2
    assert outcome.counters.new == 2
    assert outcome.counters.prefiltered == 1
    assert outcome.counters.extracted == 1
    assert outcome.counters.matched == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_pipeline_scan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.pipeline'`

- [ ] **Step 3: Write the implementation**

Create `src/marketsearch/pipeline.py`:

```python
"""Orchestration: search, dedupe, prefilter, fetch, extract, record."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Callable

from marketsearch.config import Config, SearchConfig
from marketsearch.extract import ExtractionError, Extractor
from marketsearch.fingerprint import fingerprint
from marketsearch.models import ListingDetail, RawListing
from marketsearch.notify.render import MatchCard, download_photos
from marketsearch.prefilter import prefilter
from marketsearch.sources.base import ListingSource, ParseError, SourceError
from marketsearch.store import ExtractionRow, ListingRow, Store, utcnow

log = logging.getLogger(__name__)

RELIST_WINDOW_DAYS = 60


@dataclass
class ScanCounters:
    found: int = 0
    new: int = 0
    prefiltered: int = 0
    extracted: int = 0
    matched: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ScanOutcome:
    matches: list[MatchCard] = field(default_factory=list)
    unverified: list[MatchCard] = field(default_factory=list)
    counters: ScanCounters = field(default_factory=ScanCounters)


def content_hash(detail: ListingDetail) -> str:
    payload = json.dumps(
        {"d": detail.description, "p": detail.photo_urls}, sort_keys=True
    ).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def listing_row_from(
    listing: RawListing, search_name: str, fp: str, stage: str
) -> ListingRow:
    """Build a ListingRow without a database read.

    Used for card construction so that --dry-run, which writes nothing, still
    produces exactly the same output as a real run.
    """
    now = utcnow()
    return ListingRow(
        listing_id=listing.listing_id, search_name=search_name, title=listing.title,
        price_cents=listing.price_cents, location=listing.location, url=listing.url,
        thumbnail_url=listing.thumbnail_url, seller_name=listing.seller_name,
        fingerprint=fp, stage=stage, reject_reason=None, watched=False,
        first_seen_at=now, last_seen_at=now, last_change_check_at=None,
    )


class Scanner:
    def __init__(
        self,
        config: Config,
        store: Store,
        source: ListingSource,
        extractor: Extractor,
        photo_fetcher: Callable[..., list[bytes]] = download_photos,
        dry_run: bool = False,
    ) -> None:
        self._config = config
        self._store = store
        self._source = source
        self._extractor = extractor
        self._photo_fetcher = photo_fetcher
        self._dry_run = dry_run

    def scan(self) -> ScanOutcome:
        matches: list[MatchCard] = []
        unverified: list[MatchCard] = []
        counters = ScanCounters()
        budget = self._config.extraction.max_extractions_per_run

        for search in self._config.searches:
            listings = self._source.search(
                search.query, self._config.location.anchor,
                self._config.location.radius_miles,
            )
            counters.found += len(listings)

            known = self._store.known_listing_ids([l.listing_id for l in listings])
            fresh = [l for l in listings if l.listing_id not in known]
            counters.new += len(fresh)
            log.info(
                "%s: %d listings, %d new", search.name, len(listings), len(fresh)
            )

            for listing in fresh:
                budget -= self._process(
                    listing, search, matches, unverified, counters,
                    budget_remaining=budget,
                )

        return ScanOutcome(matches=matches, unverified=unverified, counters=counters)

    def _set_stage(self, listing_id: str, stage: str, reason: str | None = None) -> None:
        if not self._dry_run:
            self._store.set_stage(listing_id, stage, reason)

    def _process(
        self,
        listing: RawListing,
        search: SearchConfig,
        matches: list[MatchCard],
        unverified: list[MatchCard],
        counters: ScanCounters,
        budget_remaining: int,
    ) -> int:
        """Handle one new listing. Returns the number of extractions consumed."""
        fp = fingerprint(
            listing.title, listing.price_cents, listing.seller_name, listing.location
        )

        if not self._dry_run:
            self._store.upsert_listing(listing, search.name, fp)

        decision = prefilter(listing, search)
        if not decision.keep:
            counters.prefiltered += 1
            self._set_stage(listing.listing_id, "prefiltered_out", decision.reason)
            return 0

        if not self._dry_run and self._store.fingerprint_seen_before(
            fp, listing.listing_id, RELIST_WINDOW_DAYS
        ):
            counters.prefiltered += 1
            self._set_stage(
                listing.listing_id, "prefiltered_out",
                f"repost of a listing seen within {RELIST_WINDOW_DAYS} days",
            )
            return 0

        if budget_remaining <= 0:
            log.info("extraction budget spent; %s stays pending", listing.listing_id)
            self._set_stage(listing.listing_id, "pending")
            return 0

        try:
            detail = self._source.fetch_detail(listing.listing_id)
        except (ParseError, SourceError) as exc:
            log.warning("detail fetch failed for %s: %s", listing.listing_id, exc)
            counters.errors += 1
            self._set_stage(listing.listing_id, "pending")
            return 0

        if not self._dry_run:
            self._store.save_detail(detail, content_hash(detail))

        try:
            result = self._extractor.extract(listing, detail, search.criteria)
        except ExtractionError as exc:
            log.warning("extraction failed for %s: %s", listing.listing_id, exc)
            counters.errors += 1
            self._set_stage(listing.listing_id, "pending")
            return 0

        counters.extracted += 1
        extraction = result.extraction

        if not self._dry_run:
            self._store.save_extraction(
                listing_id=listing.listing_id,
                attributes=extraction.model_dump(
                    include={"core", "specs", "condition", "deal"}
                ),
                verdict=extraction.verdict, confidence=extraction.confidence,
                reasoning=extraction.reasoning, unknowns=extraction.unknowns,
                model=self._config.extraction.model,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                cost_cents=result.cost_cents,
            )

        stage = "matched" if extraction.verdict == "match" else "extracted"
        self._set_stage(listing.listing_id, stage)

        row = ExtractionRow(
            listing_id=listing.listing_id,
            attributes=extraction.model_dump(include={"core", "specs", "condition", "deal"}),
            verdict=extraction.verdict, confidence=extraction.confidence,
            reasoning=extraction.reasoning, unknowns=extraction.unknowns,
            model=self._config.extraction.model, created_at=utcnow(),
        )

        if extraction.verdict == "match":
            counters.matched += 1
            matches.append(
                MatchCard(
                    listing=listing_row_from(listing, search.name, fp, stage),
                    extraction=row,
                    photos=self._photo_fetcher(detail.photo_urls),
                )
            )
        elif extraction.verdict == "unverifiable" and search.on_unknown == "alert":
            unverified.append(
                MatchCard(
                    listing=listing_row_from(listing, search.name, fp, stage),
                    extraction=row,
                    photos=self._photo_fetcher(detail.photo_urls),
                )
            )

        return 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_pipeline_scan.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/pipeline.py tests/test_pipeline_scan.py
git commit -m "feat: scan pipeline with dedupe, prefilter, extraction budget"
```

---

## Task 16: Watched-listing sync and change detection

**Files:**
- Modify: `src/marketsearch/sources/base.py` (add `ListingUnavailable`), `src/marketsearch/sources/parse.py` (add `detect_unavailable`), `src/marketsearch/sources/facebook.py` (raise it), `src/marketsearch/pipeline.py` (add `WatchSyncer`)
- Test: `tests/test_pipeline_watched.py`

**Interfaces:**
- Consumes: everything from Task 15, plus `ChangeCard` (Task 12)
- Produces:
  - `ListingUnavailable(SourceError)` in `sources/base.py`
  - `detect_unavailable(html: str) -> bool` in `sources/parse.py`
  - `WatchOutcome` frozen dataclass: `.changes: list[ChangeCard]`, `.errors: int`
  - `WatchSyncer(config, store, source, extractor, dry_run=False)` with `.sync() -> WatchOutcome`

**How favourites work.** The user saves a listing using Facebook Marketplace's own Save feature. Each run reads the saved page; **Facebook's list is the source of truth and the database mirrors it**, so un-saving is the unfollow and there is no separate state to keep in sync. A saved listing the tool has never seen gets a full extraction on first sight, giving change alerts the same attribute table as everything else and a baseline to diff against.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_watched.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.models import ListingDetail, RawListing
from marketsearch.pipeline import WatchSyncer, content_hash
from marketsearch.sources.base import ListingUnavailable, ParseError
from marketsearch.store import Store

from tests.test_pipeline_scan import CONFIG_DICT, FakeExtractor, config, extraction, listing


class FakeWatchSource:
    def __init__(self, saved=None, details=None, unavailable=None):
        self.saved = saved or []
        self.details = details or {}
        self.unavailable = set(unavailable or [])
        self.detail_calls: list[str] = []

    def search(self, query, location, radius_miles):
        return []

    def fetch_saved(self):
        return list(self.saved)

    def fetch_detail(self, listing_id):
        self.detail_calls.append(listing_id)
        if listing_id in self.unavailable:
            raise ListingUnavailable(f"{listing_id} is gone")
        return self.details.get(
            listing_id,
            ListingDetail(listing_id=listing_id, description="2400 hours",
                          structured_fields={}, photo_urls=[], distance_miles=None),
        )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "w.db")
    s.initialize()
    yield s
    s.close()


def syncer(store, source, extractor=None, **kwargs) -> WatchSyncer:
    return WatchSyncer(
        config=config(), store=store, source=source,
        extractor=extractor or FakeExtractor(), **kwargs,
    )


def seed(store: Store, listing_obj: RawListing, description="2400 hours") -> None:
    store.upsert_listing(listing_obj, "bobcat-t770", "fp")
    detail = ListingDetail(listing_id=listing_obj.listing_id, description=description,
                           structured_fields={}, photo_urls=[], distance_miles=None)
    store.save_detail(detail, content_hash(detail))


def test_saved_ids_are_mirrored_into_the_database(store: Store):
    seed(store, listing("1"))
    syncer(store, FakeWatchSource(saved=[listing("1")])).sync()
    assert store.watched_listing_ids() == {"1"}


def test_unsaving_on_facebook_clears_the_watch_flag(store: Store):
    seed(store, listing("1"))
    syncer(store, FakeWatchSource(saved=[listing("1")])).sync()
    syncer(store, FakeWatchSource(saved=[])).sync()
    assert store.watched_listing_ids() == set()


def test_a_never_seen_saved_listing_is_extracted_for_a_baseline(store: Store):
    extractor = FakeExtractor()
    outcome = syncer(store, FakeWatchSource(saved=[listing("9")]), extractor).sync()
    assert extractor.calls == 1
    assert store.get_listing("9") is not None
    assert store.latest_extraction("9") is not None
    assert outcome.changes == []  # first sight is not a change


def test_no_change_produces_no_card(store: Store):
    seed(store, listing("1"))
    outcome = syncer(store, FakeWatchSource(saved=[listing("1")])).sync()
    assert outcome.changes == []


def test_price_drop_produces_a_change_card(store: Store):
    seed(store, listing("1", price_cents=4_100_000))
    source = FakeWatchSource(saved=[listing("1", price_cents=3_800_000)])
    outcome = syncer(store, source).sync()
    assert len(outcome.changes) == 1
    change = outcome.changes[0]
    assert change.kind == "price_change"
    assert change.old_price_cents == 4_100_000
    assert change.new_price_cents == 3_800_000


def test_price_increase_also_produces_a_change_card(store: Store):
    seed(store, listing("1", price_cents=3_800_000))
    outcome = syncer(store, FakeWatchSource(saved=[listing("1", price_cents=4_000_000)])).sync()
    assert outcome.changes[0].new_price_cents == 4_000_000


def test_new_price_is_persisted(store: Store):
    seed(store, listing("1", price_cents=4_100_000))
    syncer(store, FakeWatchSource(saved=[listing("1", price_cents=3_800_000)])).sync()
    assert store.get_listing("1").price_cents == 3_800_000


def test_edited_description_produces_a_change_card(store: Store):
    seed(store, listing("1"), description="2400 hours")
    source = FakeWatchSource(
        saved=[listing("1")],
        details={"1": ListingDetail(listing_id="1", description="2400 hours. New tracks!",
                                    structured_fields={}, photo_urls=[], distance_miles=None)},
    )
    outcome = syncer(store, source).sync()
    assert len(outcome.changes) == 1
    assert outcome.changes[0].kind == "description_change"


def test_removed_listing_produces_a_removed_card(store: Store):
    seed(store, listing("1"))
    source = FakeWatchSource(saved=[listing("1")], unavailable={"1"})
    outcome = syncer(store, source).sync()
    assert len(outcome.changes) == 1
    assert outcome.changes[0].kind == "removed"
    assert outcome.changes[0].old_price_cents == 3_800_000


def test_a_parse_error_is_an_error_not_a_removal(store: Store):
    """Facebook changing its markup must never be reported as 'sold'."""
    class Broken(FakeWatchSource):
        def fetch_detail(self, listing_id):
            raise ParseError("markup changed")

    seed(store, listing("1"))
    outcome = syncer(store, Broken(saved=[listing("1")])).sync()
    assert outcome.changes == []
    assert outcome.errors == 1


def test_price_change_skips_the_detail_fetch_for_unchanged_text(store: Store):
    """Both a price change and a description edit are reported from one fetch."""
    seed(store, listing("1", price_cents=4_100_000))
    source = FakeWatchSource(saved=[listing("1", price_cents=3_800_000)])
    syncer(store, source).sync()
    assert source.detail_calls == ["1"]


def test_dry_run_writes_nothing(store: Store):
    seed(store, listing("1", price_cents=4_100_000))
    outcome = syncer(store, FakeWatchSource(saved=[listing("1", price_cents=3_800_000)]),
                     dry_run=True).sync()
    assert len(outcome.changes) == 1
    assert store.get_listing("1").price_cents == 4_100_000
    assert store.watched_listing_ids() == set()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_pipeline_watched.py -v`
Expected: FAIL with `ImportError: cannot import name 'ListingUnavailable'`

- [ ] **Step 3: Add `ListingUnavailable` to `sources/base.py`**

```python
class ListingUnavailable(SourceError):
    """The listing page loaded but the listing is gone — sold or withdrawn.

    Deliberately distinct from ParseError. Reporting a markup change as
    'likely sold' would be a confident lie about a machine still for sale.
    """
```

- [ ] **Step 4: Add `detect_unavailable` to `sources/parse.py`**

```python
_UNAVAILABLE_MARKERS = (
    "this listing isn't available",
    "this listing is no longer available",
    "content isn't available",
    "sorry, this content isn't available",
)


def detect_unavailable(html: str) -> bool:
    """True when Facebook says the listing is gone."""
    lowered = html.lower()
    return any(marker in lowered for marker in _UNAVAILABLE_MARKERS)
```

Add a fixture `tests/fixtures/pages/unavailable.html`:

```html
<!DOCTYPE html><html><body>
<div>Sorry, this content isn't available right now</div>
</body></html>
```

And a test in `tests/test_parse.py`:

```python
def test_detect_unavailable(fixtures_dir: Path):
    from marketsearch.sources.parse import detect_unavailable
    assert detect_unavailable(page(fixtures_dir, "unavailable.html")) is True
    assert detect_unavailable(page(fixtures_dir, "item.html")) is False
```

- [ ] **Step 5: Raise it from `FacebookSource.fetch_detail`**

In `src/marketsearch/sources/facebook.py`, import `ListingUnavailable` and `detect_unavailable`, then change `fetch_detail`:

```python
    def fetch_detail(self, listing_id: str) -> ListingDetail:
        html = self._load(build_item_url(listing_id), "item")
        if detect_unavailable(html):
            raise ListingUnavailable(f"listing {listing_id} is no longer available")
        try:
            return parse_item_detail(html, listing_id)
        except ParseError:
            self._save_debug(f"item-{listing_id}", html)
            raise
```

- [ ] **Step 6: Add `WatchSyncer` to `pipeline.py`**

Append to `src/marketsearch/pipeline.py` (and add `ChangeCard` and `ListingUnavailable` to the existing imports):

```python
@dataclass(frozen=True)
class WatchOutcome:
    changes: list[ChangeCard] = field(default_factory=list)
    errors: int = 0


class WatchSyncer:
    """Mirror Facebook's saved list and report what changed.

    Facebook's saved list is the source of truth; the database mirrors it. That
    is what makes un-saving the unfollow, with no separate state to reconcile.
    """

    def __init__(
        self,
        config: Config,
        store: Store,
        source: ListingSource,
        extractor: Extractor,
        dry_run: bool = False,
    ) -> None:
        self._config = config
        self._store = store
        self._source = source
        self._extractor = extractor
        self._dry_run = dry_run

    def _search_for(self, title: str) -> SearchConfig:
        """Pick the search whose title filters this listing satisfies.

        A machine saved while browsing may not belong to any configured search;
        the first search's criteria are a reasonable default and the alert
        still carries the full attribute table.
        """
        lowered = title.lower()
        for search in self._config.searches:
            if search.title_must_match and all(t in lowered for t in search.title_must_match):
                return search
        return self._config.searches[0]

    def sync(self) -> WatchOutcome:
        saved = self._source.fetch_saved()
        log.info("%d saved listing(s) on facebook", len(saved))

        changes: list[ChangeCard] = []
        errors = 0

        for listing in saved:
            try:
                change = self._check(listing)
            except (ParseError, SourceError) as exc:
                log.warning("watched check failed for %s: %s", listing.listing_id, exc)
                errors += 1
                continue
            if change is not None:
                changes.append(change)

        if not self._dry_run:
            self._store.set_watched_ids({l.listing_id for l in saved})

        return WatchOutcome(changes=changes, errors=errors)

    def _check(self, listing: RawListing) -> ChangeCard | None:
        known = self._store.get_listing(listing.listing_id)

        if known is None:
            self._baseline(listing)
            return None

        try:
            detail = self._source.fetch_detail(listing.listing_id)
        except ListingUnavailable:
            return ChangeCard(
                listing=known, kind="removed",
                old_price_cents=known.price_cents, new_price_cents=None,
            )

        if known.price_cents != listing.price_cents:
            if not self._dry_run:
                self._store.update_price(listing.listing_id, listing.price_cents)
                self._store.save_detail(detail, content_hash(detail))
            return ChangeCard(
                listing=known, kind="price_change",
                old_price_cents=known.price_cents, new_price_cents=listing.price_cents,
            )

        new_hash = content_hash(detail)
        if new_hash != self._store.get_detail_content_hash(listing.listing_id):
            if not self._dry_run:
                self._store.save_detail(detail, new_hash)
            return ChangeCard(
                listing=known, kind="description_change",
                old_price_cents=known.price_cents, new_price_cents=listing.price_cents,
            )

        return None

    def _baseline(self, listing: RawListing) -> None:
        """First sight of a listing saved while browsing. Establish a record so
        later runs have something to diff against."""
        search = self._search_for(listing.title)
        fp = fingerprint(
            listing.title, listing.price_cents, listing.seller_name, listing.location
        )
        if not self._dry_run:
            self._store.upsert_listing(listing, search.name, fp)

        detail = self._source.fetch_detail(listing.listing_id)
        if not self._dry_run:
            self._store.save_detail(detail, content_hash(detail))

        try:
            result = self._extractor.extract(listing, detail, search.criteria)
        except ExtractionError as exc:
            log.warning("baseline extraction failed for %s: %s", listing.listing_id, exc)
            return

        if not self._dry_run:
            extraction = result.extraction
            self._store.save_extraction(
                listing_id=listing.listing_id,
                attributes=extraction.model_dump(
                    include={"core", "specs", "condition", "deal"}
                ),
                verdict=extraction.verdict, confidence=extraction.confidence,
                reasoning=extraction.reasoning, unknowns=extraction.unknowns,
                model=self._config.extraction.model,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                cost_cents=result.cost_cents,
            )
            self._store.set_stage(listing.listing_id, "extracted")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_pipeline_watched.py tests/test_parse.py tests/test_facebook_source.py -v`
Expected: all pass (13 watched + 18 parse + 13 source)

- [ ] **Step 8: Commit**

```bash
git add src/marketsearch/pipeline.py src/marketsearch/sources tests/test_pipeline_watched.py \
        tests/test_parse.py tests/fixtures/pages/unavailable.html
git commit -m "feat: watched listing sync from facebook saved items with change detection"
```
