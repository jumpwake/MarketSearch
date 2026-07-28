# Watchlist Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pool model keywords into a shared per-watchlist catalog so every search query's results are checked against every model the user watches, and a listing is rejected only when no watchlist accepts it.

**Architecture:** `config.yaml` gains a `watchlists` list; each watchlist owns criteria, exclusions, a flat list of query strings, and a catalog of models (keywords + price band). The scanner pools results from *every* query across *every* watchlist into one set, then offers each listing to each watchlist in config order, assigning it to the first that accepts. Queries stop being filters and become pure discovery.

**Tech Stack:** Python 3.12+, Pydantic v2, SQLite (stdlib `sqlite3`), Typer, pytest.

Spec: [`docs/superpowers/specs/2026-07-27-watchlist-catalog-design.md`](../specs/2026-07-27-watchlist-catalog-design.md)

## Global Constraints

- Python 3.12+. Run tests with the project venv: `.venv/Scripts/python.exe -m pytest`.
- Pydantic models in `config.py` are `frozen=True`; keep them immutable.
- Keyword matching is **substring** (so `299d` matches `299d3xe`); exclusion matching is **whole-word** via the existing `_contains_word` helper (so `toy` does not match `Toyota`).
- A missing price (`price_cents is None`) is never a rejection.
- All money in the database and in config models is **integer cents**. YAML expresses dollars.
- Never drop or rebuild `marketsearch.db`. It holds ~30 paid-for extractions. Schema changes migrate in place.
- Tasks 1–6 must leave the full suite green. The legacy `searches` config path stays working until Task 7.
- End every commit message with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

### Task 1: Config — watchlist catalog

Adds `watchlists` alongside the existing `searches`. Both load; `searches` is removed in Task 7.

**Files:**
- Modify: `src/marketsearch/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ModelConfig(name: str, keywords: list[str], price_min_cents: int, price_max_cents: int)`
  - `WatchlistConfig(name: str, queries: list[str], models: list[ModelConfig], exclude: list[str], on_unknown: Literal["alert","skip"], criteria: str)`
  - `Config.watchlists: list[WatchlistConfig]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
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


def test_on_unknown_defaults_to_alert(tmp_path: Path):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v -k "watchlist or model_price or keywords_and_exclude or on_unknown_defaults or legacy_searches"`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'watchlists'` / `ConfigError` mismatches.

- [ ] **Step 3: Add the config models**

In `src/marketsearch/config.py`, after `SearchConfig`:

```python
class ModelConfig(BaseModel):
    """One machine or attachment the user watches, with its own price band."""

    model_config = ConfigDict(frozen=True)

    name: str
    keywords: list[str]
    price_min_cents: int
    price_max_cents: int

    @field_validator("keywords")
    @classmethod
    def _lowercase(cls, values: list[str]) -> list[str]:
        return [v.strip().lower() for v in values]


class WatchlistConfig(BaseModel):
    """A catalog of models sharing one set of criteria, exclusions, and queries.

    Queries are discovery only. Every query's results are checked against every
    model here, which is what stops one query's filter from discarding another
    query's machine.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    queries: list[str]
    models: list[ModelConfig]
    exclude: list[str] = []
    on_unknown: Literal["alert", "skip"] = "alert"
    criteria: str

    @field_validator("exclude")
    @classmethod
    def _lowercase(cls, values: list[str]) -> list[str]:
        return [v.strip().lower() for v in values]
```

- [ ] **Step 4: Wire watchlists into Config and load_config**

Add the field to `Config`:

```python
    searches: list[SearchConfig] = []
    watchlists: list[WatchlistConfig] = []
```

Add the normaliser next to `_normalise_search`:

```python
def _normalise_watchlist(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert the human-friendly YAML shape into WatchlistConfig field names."""
    out = dict(raw)
    name = out.get("name")

    if not out.get("queries"):
        raise ConfigError(f"watchlist '{name}' needs at least one query")

    models = out.get("models") or []
    if not models:
        raise ConfigError(f"watchlist '{name}' needs at least one model")

    normalised = []
    for model in models:
        model = dict(model)
        price = model.pop("price", None) or {}
        if "min" not in price or "max" not in price:
            raise ConfigError(
                f"model '{model.get('name')}' needs price.min and price.max"
            )
        model["price_min_cents"] = int(round(float(price["min"]) * 100))
        model["price_max_cents"] = int(round(float(price["max"]) * 100))
        normalised.append(model)

    out["models"] = normalised
    return out
```

Replace the `searches` block inside `load_config` with:

```python
    raw = dict(raw)
    raw["searches"] = [_normalise_search(s) for s in raw.get("searches") or []]
    raw["watchlists"] = [_normalise_watchlist(w) for w in raw.get("watchlists") or []]

    if not raw["searches"] and not raw["watchlists"]:
        raise ConfigError("config must define at least one search or watchlist")

    names = [s["name"] for s in raw["searches"]]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ConfigError(f"duplicate search name(s): {', '.join(sorted(duplicates))}")

    watchlist_names = [w["name"] for w in raw["watchlists"]]
    duplicates = {n for n in watchlist_names if watchlist_names.count(n) > 1}
    if duplicates:
        raise ConfigError(
            f"duplicate watchlist name(s): {', '.join(sorted(duplicates))}"
        )

    # Model names label rows in the database, so they must be unique globally,
    # not merely within their watchlist.
    model_names = [m["name"] for w in raw["watchlists"] for m in w["models"]]
    duplicates = {n for n in model_names if model_names.count(n) > 1}
    if duplicates:
        raise ConfigError(f"duplicate model name(s): {', '.join(sorted(duplicates))}")
```

- [ ] **Step 5: Run the full config suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: PASS, all tests including the pre-existing `searches` ones.

- [ ] **Step 6: Commit**

```bash
git add src/marketsearch/config.py tests/test_config.py
git commit -m "feat: watchlist catalog config with per-model price bands

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Prefilter — identify, price, fall-through

Adds the assignment functions beside the existing `prefilter()`, which stays until Task 7.

**Files:**
- Modify: `src/marketsearch/prefilter.py`
- Test: `tests/test_prefilter.py`

**Interfaces:**
- Consumes: `ModelConfig`, `WatchlistConfig` from Task 1.
- Produces:
  - `Assignment(watchlist: WatchlistConfig, model: ModelConfig)`
  - `Rejection(reason: str)`
  - `identify_model(title: str, watchlist: WatchlistConfig) -> ModelConfig | None`
  - `offer(listing: RawListing, watchlist: WatchlistConfig) -> Assignment | Rejection`
  - `assign(listing: RawListing, watchlists: list[WatchlistConfig]) -> Assignment | Rejection`
  - `NO_MODEL = "matched no watched model"`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_prefilter.py`:

```python
from marketsearch.config import ModelConfig, WatchlistConfig
from marketsearch.models import RawListing
from marketsearch.prefilter import Assignment, Rejection, assign, identify_model, offer


def model(name, keywords, lo, hi):
    return ModelConfig(
        name=name, keywords=keywords,
        price_min_cents=lo * 100, price_max_cents=hi * 100,
    )


MACHINES = WatchlistConfig(
    name="track-loaders",
    queries=["Bobcat T770"],
    models=[
        model("bobcat-t770", ["t770", "t-770"], 15000, 53000),
        model("bobcat-t750", ["t750"], 15000, 50000),
    ],
    exclude=["wanted", "s770"],
    criteria="Under 3000 hours.",
)

ATTACHMENTS = WatchlistConfig(
    name="attachments",
    queries=["skid steer root grapple"],
    models=[model("root-grapple", ["grapple"], 800, 6000)],
    exclude=["mini"],
    criteria="Root grapple.",
)

ALL = [MACHINES, ATTACHMENTS]


def listing(title, price_cents):
    return RawListing(
        listing_id="1", title=title, price_cents=price_cents, location="Peoria, IL",
        url="https://example.com/1", thumbnail_url=None, seller_name=None,
    )


def test_identify_model_matches_substring():
    assert identify_model("2019 bobcat t770 loader", MACHINES).name == "bobcat-t770"


def test_identify_model_returns_none_when_nothing_matches():
    assert identify_model("2018 bobcat t595", MACHINES) is None


def test_a_query_keeps_a_machine_from_another_model():
    """The whole point: the T86 query surfaced a T770 and we keep it."""
    result = assign(listing("2019 Bobcat T770", 4_200_000), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "bobcat-t770"
    assert result.watchlist.name == "track-loaders"


def test_machine_sold_with_a_grapple_is_a_machine():
    result = assign(listing("2019 Bobcat T770 with root grapple", 4_200_000), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "bobcat-t770"


def test_grapple_for_a_machine_falls_through_on_price():
    """t770 matches, but $3,000 is below the machine band, so attachments take it."""
    result = assign(listing("Root grapple for Bobcat T770", 300_000), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "root-grapple"
    assert result.watchlist.name == "attachments"


def test_plain_grapple_falls_through_on_no_model():
    result = assign(listing("Eterra skidsteer Grapple", 549_500), ALL)
    assert isinstance(result, Assignment)
    assert result.model.name == "root-grapple"


def test_rejected_when_no_watchlist_accepts():
    result = assign(listing("2018 Bobcat T595", 3_000_000), ALL)
    assert isinstance(result, Rejection)
    assert result.reason == "matched no watched model"


def test_exclusion_beats_a_model_match():
    result = assign(listing("Wanted: Bobcat T770", 4_000_000), ALL)
    assert isinstance(result, Rejection)
    assert "excluded term 'wanted'" in result.reason


def test_exclusions_are_whole_word():
    """'s770' must not fire on 'Bobcat T770'."""
    assert isinstance(assign(listing("2019 Bobcat T770", 4_000_000), ALL), Assignment)


def test_price_reason_names_the_model():
    result = assign(listing("2019 Bobcat T770", 9_900_000), ALL)
    assert isinstance(result, Rejection)
    assert "bobcat-t770" in result.reason
    assert "above" in result.reason


def test_missing_price_is_not_a_rejection():
    result = assign(listing("2019 Bobcat T770", None), ALL)
    assert isinstance(result, Assignment)


def test_offer_declines_without_consulting_other_watchlists():
    assert isinstance(offer(listing("Eterra Grapple", 549_500), MACHINES), Rejection)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_prefilter.py -v`
Expected: FAIL with `ImportError: cannot import name 'Assignment' from 'marketsearch.prefilter'`.

- [ ] **Step 3: Implement the assignment functions**

First extend the existing config import at the top of `src/marketsearch/prefilter.py`:

```python
from marketsearch.config import ModelConfig, SearchConfig, WatchlistConfig
```

Then append the rest at the bottom of the file, leaving `prefilter()` and
`PrefilterResult` untouched — they are removed in Task 7:

```python
NO_MODEL = "matched no watched model"


@dataclass(frozen=True)
class Assignment:
    """A watchlist accepted this listing, as this model."""

    watchlist: WatchlistConfig
    model: ModelConfig


@dataclass(frozen=True)
class Rejection:
    reason: str


def identify_model(title: str, watchlist: WatchlistConfig) -> ModelConfig | None:
    """First model whose keywords appear in the title.

    Substring, not whole-word: sellers write '299d3xe' and '299d' has to keep
    matching it.
    """
    for model in watchlist.models:
        if any(keyword in title for keyword in model.keywords):
            return model
    return None


def offer(listing: RawListing, watchlist: WatchlistConfig) -> Assignment | Rejection:
    """Ask one watchlist whether it will take this listing.

    Identification precedes the price check because the band belongs to the
    model, not the watchlist — which is also why the catalog cannot be
    flattened into one keyword list.
    """
    title = listing.title.lower()

    for token in watchlist.exclude:
        if _contains_word(title, token):
            return Rejection(f"title contains excluded term '{token}'")

    model = identify_model(title, watchlist)
    if model is None:
        return Rejection(NO_MODEL)

    # A missing price is not a rejection. Marketplace sometimes omits it, and
    # those listings are disproportionately worth a look.
    if listing.price_cents is not None:
        if listing.price_cents < model.price_min_cents:
            return Rejection(
                f"price ${listing.price_cents / 100:,.0f} below {model.name} "
                f"minimum ${model.price_min_cents / 100:,.0f}"
            )
        if listing.price_cents > model.price_max_cents:
            return Rejection(
                f"price ${listing.price_cents / 100:,.0f} above {model.name} "
                f"maximum ${model.price_max_cents / 100:,.0f}"
            )

    return Assignment(watchlist=watchlist, model=model)


def assign(
    listing: RawListing, watchlists: list[WatchlistConfig]
) -> Assignment | Rejection:
    """Offer the listing to each watchlist in order; first acceptance wins.

    A listing is rejected only when every watchlist declines it. That is what
    stops one catalog's filter from ending the life of another catalog's
    listing — the failure that lost a $42,000 T770 to the T86 search.

    The reported reason prefers a specific decline (exclusion, price) over the
    generic one, so 'no watched model' never masks a near miss.
    """
    specific: Rejection | None = None
    for watchlist in watchlists:
        result = offer(listing, watchlist)
        if isinstance(result, Assignment):
            return result
        if specific is None and result.reason != NO_MODEL:
            specific = result
    return specific or Rejection(NO_MODEL)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_prefilter.py -v`
Expected: PASS (all new tests plus the pre-existing `prefilter()` tests).

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/prefilter.py tests/test_prefilter.py
git commit -m "feat: fall-through watchlist assignment

A listing is rejected only when every watchlist declines it. Price
participates in the decision, so a cheap grapple whose title names a
machine falls through to the attachments catalog instead of being
rejected against the machine band.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Store — schema v2 with watchlist and model columns

**Files:**
- Modify: `src/marketsearch/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2.
- Produces:
  - `ListingRow` gains `watchlist_name: str | None` and `model_name: str | None`; keeps `search_name` until Task 7.
  - `Store.upsert_listing(listing, search_name, fp, watchlist_name=None, model_name=None)`
  - `Store.reassign(listing_id: str, watchlist_name: str, model_name: str) -> None`
  - `Store.prefiltered_listings() -> list[ListingRow]`
  - `SCHEMA_VERSION = 2`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store.py`:

```python
import sqlite3

from marketsearch.store import SCHEMA_VERSION, Store


def test_new_database_is_at_current_schema_version(tmp_path):
    with Store(tmp_path / "new.db") as store:
        store.initialize()
        row = store._conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION


def test_v1_database_migrates_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (1);
        CREATE TABLE listings (
            listing_id TEXT PRIMARY KEY, search_name TEXT NOT NULL, title TEXT NOT NULL,
            price_cents INTEGER, location TEXT, url TEXT NOT NULL, thumbnail_url TEXT,
            seller_name TEXT, fingerprint TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT 'pending', reject_reason TEXT,
            watched INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL, last_change_check_at TEXT,
            extraction_attempts INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO listings (listing_id, search_name, title, price_cents, url,
                              fingerprint, first_seen_at, last_seen_at)
        VALUES ('a', 'bobcat-t770', '2019 Bobcat T770', 4200000, 'u', 'f', 't', 't'),
               ('b', 'root-grapple', '72in root grapple', 300000, 'u', 'f', 't', 't');
        """
    )
    conn.commit()
    conn.close()

    with Store(path) as store:
        store.initialize()
        assert store._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()["version"] == SCHEMA_VERSION
        machine = store.get_listing("a")
        grapple = store.get_listing("b")

    assert machine.model_name == "bobcat-t770"
    assert machine.watchlist_name == "track-loaders"
    assert grapple.model_name == "root-grapple"
    assert grapple.watchlist_name == "attachments"


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    with Store(path) as store:
        store.initialize()
    with Store(path) as store:
        store.initialize()
        assert store._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()["version"] == SCHEMA_VERSION


def test_reassign_updates_watchlist_and_model(store: Store):
    store.upsert_listing(make_listing("1"), "bobcat-t86", "fp")
    store.reassign("1", "track-loaders", "bobcat-t770")
    row = store.get_listing("1")
    assert row.watchlist_name == "track-loaders"
    assert row.model_name == "bobcat-t770"


def test_prefiltered_listings_returns_only_rejected_rows(store: Store):
    store.upsert_listing(make_listing("1"), "bobcat-t86", "fp")
    store.set_stage("1", "prefiltered_out", "no model")
    assert [r.listing_id for r in store.prefiltered_listings()] == ["1"]
    store.set_stage("1", "matched")
    assert store.prefiltered_listings() == []
```

These use the `store` fixture and `make_listing` helper already defined at the top
of `tests/test_store.py`. Do not add fixtures to `tests/conftest.py` — it holds
only `fixtures_dir`, and these test modules use module-level helpers instead.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_store.py -v -k "schema or migrat or reassign or prefiltered_listings"`
Expected: FAIL — `SCHEMA_VERSION` is 1, and `reassign` / `prefiltered_listings` do not exist.

- [ ] **Step 3: Bump the schema and add the columns**

In `src/marketsearch/store.py`, set `SCHEMA_VERSION = 2` and add the two columns to the `listings` table in `_SCHEMA`, directly after `search_name`:

```sql
    search_name    TEXT NOT NULL,
    watchlist_name TEXT,
    model_name     TEXT,
```

- [ ] **Step 4: Replace `initialize` with a migrating version**

```python
    def initialize(self) -> None:
        self._conn.executescript(_SCHEMA)
        cur = self._conn.execute("SELECT version FROM schema_version")
        row = cur.fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            self._conn.commit()
            return
        self._migrate(row["version"])

    def _migrate(self, version: int) -> None:
        """Bring an existing database up to SCHEMA_VERSION, in place.

        Never rebuild: the extractions in here cost real money to produce.
        """
        if version >= SCHEMA_VERSION:
            return

        if version < 2:
            columns = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(listings)")
            }
            if "watchlist_name" not in columns:
                self._conn.execute(
                    "ALTER TABLE listings ADD COLUMN watchlist_name TEXT"
                )
            if "model_name" not in columns:
                self._conn.execute("ALTER TABLE listings ADD COLUMN model_name TEXT")
            # v1 stored the claiming search's name. That name was always the
            # model in practice; only the grapple search belonged elsewhere.
            self._conn.execute(
                """
                UPDATE listings
                   SET model_name = search_name,
                       watchlist_name = CASE WHEN search_name = 'root-grapple'
                                             THEN 'attachments'
                                             ELSE 'track-loaders' END
                 WHERE model_name IS NULL
                """
            )

        self._conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        self._conn.commit()
```

- [ ] **Step 5: Extend ListingRow and the row mapper**

Add to `ListingRow` **at the very end of the field list, with defaults**:

```python
    watchlist_name: str | None = None
    model_name: str | None = None
```

The position and the defaults are both load-bearing. `ListingRow` is a frozen
dataclass whose existing fields have no defaults, so a defaulted field must come
last — and without defaults, every existing positional constructor call breaks,
including the `listing()` helper at the top of `tests/test_render.py`.

Add to `_row_to_listing`, after `search_name=row["search_name"],`:

```python
        watchlist_name=row["watchlist_name"],
        model_name=row["model_name"],
```

- [ ] **Step 6: Teach upsert_listing the new columns and add the new methods**

Change the signature and SQL of `upsert_listing`:

```python
    def upsert_listing(
        self,
        listing: RawListing,
        search_name: str,
        fp: str,
        watchlist_name: str | None = None,
        model_name: str | None = None,
    ) -> None:
        now = utcnow()
        self._conn.execute(
            """
            INSERT INTO listings (listing_id, search_name, watchlist_name, model_name,
                                  title, price_cents, location,
                                  url, thumbnail_url, seller_name, fingerprint,
                                  first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                title = excluded.title,
                price_cents = excluded.price_cents,
                location = excluded.location,
                thumbnail_url = excluded.thumbnail_url,
                seller_name = excluded.seller_name,
                last_seen_at = excluded.last_seen_at
            """,
            (
                listing.listing_id, search_name, watchlist_name, model_name,
                listing.title, listing.price_cents,
                listing.location, listing.url, listing.thumbnail_url,
                listing.seller_name, fp, now, now,
            ),
        )
        self._conn.commit()

    def reassign(
        self, listing_id: str, watchlist_name: str, model_name: str
    ) -> None:
        """Move a listing to the watchlist and model that now accept it."""
        self._conn.execute(
            "UPDATE listings SET watchlist_name = ?, model_name = ? WHERE listing_id = ?",
            (watchlist_name, model_name, listing_id),
        )
        self._conn.commit()

    def prefiltered_listings(self) -> list[ListingRow]:
        """Every listing rejected before extraction — the requeue corpus."""
        cur = self._conn.execute(
            "SELECT * FROM listings WHERE stage = 'prefiltered_out'"
            " ORDER BY first_seen_at"
        )
        return [_row_to_listing(row) for row in cur.fetchall()]
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS. `ListingRow` gained fields with defaults supplied by the mapper, and `upsert_listing`'s new parameters are optional, so existing callers are unaffected.

- [ ] **Step 8: Commit**

```bash
git add src/marketsearch/store.py tests/test_store.py tests/conftest.py
git commit -m "feat: schema v2 with watchlist_name and model_name

Migrates in place. v1 rows map search_name to model_name, with the
grapple search moving to the attachments watchlist.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Pipeline — pooled scan with fall-through assignment

**Files:**
- Modify: `src/marketsearch/pipeline.py`
- Test: `tests/test_pipeline_scan.py`

**Interfaces:**
- Consumes: `assign`, `Assignment`, `Rejection` (Task 2); `Store.upsert_listing` with `watchlist_name`/`model_name`, `Store.pending_listings` (Task 3).
- Produces:
  - `Scanner.scan()` unchanged externally — still returns `ScanOutcome`.
  - `Store.pending_listings()` becomes argument-free.
  - `listing_row_from(listing, watchlist_name, model_name, fp, stage) -> ListingRow`

**Behaviour note:** `counters.found` becomes the count of *unique* listings pooled across all queries, not the sum of per-query result lengths. Run-history numbers will drop and stop double-counting.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline_scan.py`. That module builds config with
`Config.model_validate` directly, so model price bands are given as
`price_min_cents` / `price_max_cents` — `load_config`'s dollar conversion is
bypassed. Put these helpers beside the existing `CONFIG_DICT` and `FakeSource`:

```python
WATCHLIST_DICT = {
    **{k: v for k, v in CONFIG_DICT.items() if k != "searches"},
    "watchlists": [
        {
            "name": "track-loaders",
            "queries": ["Bobcat T770", "Bobcat T86 track loader"],
            "models": [
                {"name": "bobcat-t770", "keywords": ["t770"],
                 "price_min_cents": 1_500_000, "price_max_cents": 5_300_000},
                {"name": "bobcat-t86", "keywords": ["t86"],
                 "price_min_cents": 3_000_000, "price_max_cents": 7_000_000},
            ],
            "exclude": ["wanted", "s770"],
            "on_unknown": "alert",
            "criteria": "Under 3000 engine hours.",
        },
        {
            "name": "attachments",
            "queries": ["skid steer root grapple"],
            "models": [
                {"name": "root-grapple", "keywords": ["grapple"],
                 "price_min_cents": 80_000, "price_max_cents": 600_000},
            ],
            "exclude": ["mini"],
            "on_unknown": "alert",
            "criteria": "Root grapple.",
        },
    ],
}


def watchlist_config(**overrides) -> Config:
    return Config.model_validate({**WATCHLIST_DICT, **overrides})


class QueryFakeSource(FakeSource):
    """FakeSource that answers per query string, so pooling can be tested."""

    def __init__(self, by_query: dict[str, list[RawListing]]):
        super().__init__(results=[])
        self.by_query = by_query
        self.queries: list[str] = []

    def search(self, query, location, radius_miles):
        self.queries.append(query)
        return list(self.by_query.get(query, []))
```

Then the tests, using the `store` fixture already defined in that module:

```python
def test_listing_from_one_query_is_kept_by_another_watchlists_model(store: Store):
    """The T86 query surfaces a T770. It must be kept, not rejected."""
    t770 = listing("1917658885567966", title="2019 Bobcat T770", price_cents=4_200_000)
    source = QueryFakeSource({"Bobcat T86 track loader": [t770]})

    outcome = Scanner(watchlist_config(), store, source, FakeExtractor()).scan()
    row = store.get_listing("1917658885567966")

    assert outcome.counters.prefiltered == 0
    assert row.model_name == "bobcat-t770"
    assert row.watchlist_name == "track-loaders"
    assert row.stage in {"matched", "extracted"}


def test_every_watchlists_queries_are_searched(store: Store):
    source = QueryFakeSource({})
    Scanner(watchlist_config(), store, source, FakeExtractor()).scan()
    assert source.queries == [
        "Bobcat T770", "Bobcat T86 track loader", "skid steer root grapple",
    ]


def test_pooled_results_are_deduplicated_across_queries(store: Store):
    t770 = listing("dupe", title="2019 Bobcat T770", price_cents=4_200_000)
    source = QueryFakeSource(
        {"Bobcat T770": [t770], "Bobcat T86 track loader": [t770]}
    )

    outcome = Scanner(watchlist_config(), store, source, FakeExtractor()).scan()

    assert outcome.counters.found == 1
    assert outcome.counters.new == 1


def test_listing_no_watchlist_accepts_is_rejected_with_a_true_reason(store: Store):
    junk = listing("junk", title="2018 Bobcat T595", price_cents=3_000_000)
    source = QueryFakeSource({"Bobcat T770": [junk]})

    Scanner(watchlist_config(), store, source, FakeExtractor()).scan()
    row = store.get_listing("junk")

    assert row.stage == "prefiltered_out"
    assert row.reject_reason == "matched no watched model"


def test_cheap_grapple_naming_a_machine_lands_in_attachments(store: Store):
    grapple = listing("g1", title="Root grapple for Bobcat T770", price_cents=300_000)
    source = QueryFakeSource({"skid steer root grapple": [grapple]})

    Scanner(watchlist_config(), store, source, FakeExtractor()).scan()
    row = store.get_listing("g1")

    assert row.model_name == "root-grapple"
    assert row.watchlist_name == "attachments"
```

Existing tests in this module that pass `config()` to `Scanner` will now scan
nothing, because `watchlists` is empty. Port each to `watchlist_config()` and
`QueryFakeSource` rather than reintroducing the search path. `tests/test_shakedown.py`
imports `config` from this module — leave `config()` and `CONFIG_DICT` in place
until Task 7 so that import keeps working.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline_scan.py -v -k "another_watchlists_model or pooled_results or true_reason"`
Expected: FAIL — `Scanner.scan` iterates `config.searches`, which the fixture leaves empty, so nothing is scanned.

- [ ] **Step 3: Rewrite Scanner.scan to pool and assign**

Replace the body of `Scanner.scan` in `src/marketsearch/pipeline.py`:

```python
    def scan(self) -> ScanOutcome:
        matches: list[MatchCard] = []
        unverified: list[MatchCard] = []
        counters = ScanCounters()
        budget = self._config.extraction.max_extractions_per_run
        watchlists = self._config.watchlists

        # Every query feeds one pool. A query is discovery, not a filter: the
        # T86 query routinely returns T770s, and those are worth keeping.
        pooled: dict[str, RawListing] = {}
        for watchlist in watchlists:
            for query in watchlist.queries:
                for listing in self._source.search(
                    query,
                    self._config.location.anchor,
                    self._config.location.radius_miles,
                ):
                    pooled.setdefault(listing.listing_id, listing)

        counters.found = len(pooled)

        known = self._store.known_listing_ids(list(pooled))
        fresh = [l for lid, l in pooled.items() if lid not in known]
        counters.new = len(fresh)

        # Listings that failed extraction earlier are already in `pooled`, so
        # dedupe would exclude them forever. Pull them back in explicitly.
        retries = [] if self._dry_run else self._store.pending_listings()
        log.info(
            "%d pooled listing(s), %d new, %d awaiting retry",
            len(pooled), len(fresh), len(retries),
        )

        for listing in fresh + retries:
            budget -= self._process(
                listing, watchlists, matches, unverified, counters,
                budget_remaining=budget,
            )

        return ScanOutcome(matches=matches, unverified=unverified, counters=counters)
```

- [ ] **Step 4: Rewrite _process to take watchlists and assign**

Replace the signature and the first half of `Scanner._process`:

```python
    def _process(
        self,
        listing: RawListing,
        watchlists: list[WatchlistConfig],
        matches: list[MatchCard],
        unverified: list[MatchCard],
        counters: ScanCounters,
        budget_remaining: int,
    ) -> int:
        """Handle one listing. Returns the number of extractions consumed."""
        fp = fingerprint(
            listing.title, listing.price_cents, listing.seller_name, listing.location
        )
        decision = assign(listing, watchlists)

        if isinstance(decision, Rejection):
            if not self._dry_run:
                self._store.upsert_listing(listing, "", fp)
            counters.prefiltered += 1
            self._set_stage(listing.listing_id, "prefiltered_out", decision.reason)
            return 0

        watchlist, model = decision.watchlist, decision.model
        if not self._dry_run:
            self._store.upsert_listing(
                listing, model.name, fp,
                watchlist_name=watchlist.name, model_name=model.name,
            )
            # upsert leaves an existing row's assignment alone; a listing whose
            # accepting watchlist changed after a config edit must follow it.
            self._store.reassign(listing.listing_id, watchlist.name, model.name)
```

Then, in the rest of `_process`, replace every `search.criteria` with `watchlist.criteria`, every `search.on_unknown` with `watchlist.on_unknown`, and every `search.name` with `model.name`. Delete the old `prefilter(listing, search)` block and the `if not decision.keep:` branch it fed.

- [ ] **Step 5: Update listing_row_from and the imports**

```python
def listing_row_from(
    listing: RawListing, watchlist_name: str, model_name: str, fp: str, stage: str
) -> ListingRow:
    """Build a ListingRow without a database read.

    Used for card construction so that --dry-run, which writes nothing, still
    produces exactly the same output as a real run.
    """
    now = utcnow()
    return ListingRow(
        listing_id=listing.listing_id, search_name=model_name,
        watchlist_name=watchlist_name, model_name=model_name,
        title=listing.title,
        price_cents=listing.price_cents, location=listing.location, url=listing.url,
        thumbnail_url=listing.thumbnail_url, seller_name=listing.seller_name,
        fingerprint=fp, stage=stage, reject_reason=None, watched=False,
        first_seen_at=now, last_seen_at=now, last_change_check_at=None,
        extraction_attempts=0,
    )
```

Update both call sites in `_process` to `listing_row_from(listing, watchlist.name, model.name, fp, stage)`.

Change the prefilter import at the top of `pipeline.py`:

```python
from marketsearch.prefilter import Assignment, Rejection, assign
```

and add `WatchlistConfig` to the `marketsearch.config` import.

- [ ] **Step 6: Make pending_listings argument-free**

In `src/marketsearch/store.py`:

```python
    def pending_listings(self) -> list[RawListing]:
        """Listings awaiting a retry. Excludes 'failed', so a listing that has
        exhausted its attempts is never picked up again.

        Not scoped by watchlist: assignment is recomputed on every pass, so the
        stored one is not an input to the retry decision.
        """
        cur = self._conn.execute("SELECT * FROM listings WHERE stage = 'pending'")
```

- [ ] **Step 7: Update WatchSyncer._baseline and _search_for**

In `WatchSyncer`, replace `_search_for` with a watchlist-aware version and fix `_baseline`:

```python
    def _assign(self, listing: RawListing) -> tuple[WatchlistConfig, str]:
        """Pick the watchlist whose criteria judge a saved listing.

        A machine saved while browsing may match no catalog at all; the first
        watchlist's criteria are a reasonable default and the alert still
        carries the full attribute table.
        """
        decision = assign(listing, self._config.watchlists)
        if isinstance(decision, Assignment):
            return decision.watchlist, decision.model.name
        return self._config.watchlists[0], ""
```

In `_baseline`, replace the `search = self._search_for(listing.title)` line and the `upsert_listing` / `extract` calls:

```python
        watchlist, model_name = self._assign(listing)
        fp = fingerprint(
            listing.title, listing.price_cents, listing.seller_name, listing.location
        )
        if not self._dry_run:
            self._store.upsert_listing(
                listing, model_name, fp,
                watchlist_name=watchlist.name, model_name=model_name or None,
            )
```

and pass `watchlist.criteria` to `self._extractor.extract(...)`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS. Tests still written against `config.searches` will fail here — port them to `watchlist_config` rather than reintroducing the search path.

- [ ] **Step 9: Commit**

```bash
git add src/marketsearch/pipeline.py src/marketsearch/store.py tests/
git commit -m "feat: pool every query and assign by fall-through

counters.found now counts unique pooled listings instead of summing
per-query result lengths, so run history stops double-counting.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Consumers — shakedown and render

**Files:**
- Modify: `src/marketsearch/shakedown.py`, `src/marketsearch/notify/render.py`, `src/marketsearch/cli.py`
- Test: `tests/test_shakedown.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `ListingRow.watchlist_name` / `.model_name` (Task 3).
- Produces:
  - `shakedown._watchlist_by_name(config, name) -> WatchlistConfig | None`
  - `shakedown.replay(store, config, extractor, model_name, since, save)` — the `search_name` parameter is renamed `model_name`.
  - `Store.listings_with_details(model_name: str | None, since: str)` — parameter renamed.

- [ ] **Step 1: Write the failing tests**

In `tests/test_render.py`, first give the module-level `listing()` helper a
`model_name` parameter, since `ListingRow` now carries one:

```python
def listing(listing_id="1", title="2019 Bobcat T770", price_cents=3_800_000,
            model_name="bobcat-t770") -> ListingRow:
    return ListingRow(
        listing_id=listing_id, search_name="bobcat-t770", title=title,
        price_cents=price_cents, location="Olathe, KS",
        url=f"https://www.facebook.com/marketplace/item/{listing_id}/",
        thumbnail_url=None, seller_name="Dale S", fingerprint="fp",
        stage="matched", reject_reason=None, watched=False,
        first_seen_at="2026-07-26T10:00:00+00:00",
        last_seen_at="2026-07-26T10:00:00+00:00", last_change_check_at=None,
        extraction_attempts=0,
        watchlist_name="track-loaders", model_name=model_name,
    )
```

Then add the test:

```python
def test_sms_names_models_not_searches():
    cards = [
        MatchCard(listing=listing("1", model_name="bobcat-t770"),
                  extraction=extraction(), photos=[]),
        MatchCard(listing=listing("2", model_name="root-grapple"),
                  extraction=extraction(), photos=[]),
    ]
    body = render_sms(cards, [], [])
    assert "bobcat-t770" in body
    assert "root-grapple" in body
```

Add to `tests/test_shakedown.py`, extending its existing import from
`tests.test_pipeline_scan` to include `watchlist_config`:

```python
from marketsearch.shakedown import _watchlist_by_name
from tests.test_pipeline_scan import watchlist_config


def test_watchlist_resolves_from_a_model_name():
    """A row labelled with a model must find its watchlist's criteria."""
    watchlist = _watchlist_by_name(watchlist_config(), "bobcat-t770")
    assert watchlist is not None
    assert watchlist.name == "track-loaders"
    assert watchlist.criteria == "Under 3000 engine hours."


def test_unknown_model_resolves_to_no_watchlist():
    assert _watchlist_by_name(watchlist_config(), "newholland-c") is None


def test_grapple_model_resolves_to_the_attachments_watchlist():
    watchlist = _watchlist_by_name(watchlist_config(), "root-grapple")
    assert watchlist.name == "attachments"
```

`seed_extracted` in that module calls
`store.upsert_listing(listing(listing_id), "bobcat-t770", f"fp{listing_id}")`.
Add the assignment so replay can resolve criteria:

```python
    store.upsert_listing(listing(listing_id), "bobcat-t770", f"fp{listing_id}",
                         watchlist_name="track-loaders", model_name="bobcat-t770")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shakedown.py tests/test_render.py -v -k "watchlist or models_not_searches"`
Expected: FAIL with `ImportError: cannot import name '_watchlist_by_name'`.

- [ ] **Step 3: Replace _search_by_name with _watchlist_by_name**

In `src/marketsearch/shakedown.py`, replace the function at line 39:

```python
def _watchlist_by_name(config: Config, model_name: str) -> WatchlistConfig | None:
    """The watchlist that owns a model, which is where its criteria live."""
    for watchlist in config.watchlists:
        for model in watchlist.models:
            if model.name == model_name:
                return watchlist
    return None
```

Update the import at the top from `SearchConfig` to `WatchlistConfig`.

- [ ] **Step 4: Update both call sites**

In `collect_run_cards`:

```python
            watchlist = _watchlist_by_name(config, listing.model_name)
            if watchlist is None or watchlist.on_unknown == "alert":
                unverified.append(card)
```

In `replay`, rename the parameter and update the lookup:

```python
def replay(
    store: Store,
    config: Config,
    extractor: Extractor,
    model_name: str | None,
    since: str,
    save: bool = False,
) -> list[ReplayRow]:
    """Re-judge stored listings with the criteria currently in config.yaml."""
    cutoff = parse_since(since).isoformat()
    corpus = store.listings_with_details(model_name, cutoff)
    rows: list[ReplayRow] = []

    for listing, detail, previous in corpus:
        watchlist = _watchlist_by_name(config, listing.model_name)
        if watchlist is None:
            log.warning(
                "listing %s is labelled model %r which is no longer in config; skipping",
                listing.listing_id, listing.model_name,
            )
            continue

        try:
            result = extractor.extract(_as_raw(listing), detail, watchlist.criteria)
```

- [ ] **Step 5: Update the store query and render**

In `src/marketsearch/store.py`, rename the `listings_with_details` parameter and column:

```python
    def listings_with_details(
        self, model_name: str | None, since: str
    ) -> list[tuple[ListingRow, ListingDetail, ExtractionRow | None]]:
        """Stored listings that have a saved detail — the replay corpus."""
        sql = (
            "SELECT l.listing_id AS lid FROM listings l"
            " JOIN listing_details d ON d.listing_id = l.listing_id"
            " WHERE l.first_seen_at >= ?"
        )
        params: list[object] = [since]
        if model_name is not None:
            sql += " AND l.model_name = ?"
            params.append(model_name)
```

In `src/marketsearch/notify/render.py:240`:

```python
        names = sorted({c.listing.model_name for c in matches if c.listing.model_name})
```

In `src/marketsearch/cli.py`, update the `replay` command's help text and keyword argument:

```python
    search: str = typer.Option(..., "--search", help="Model name from config.yaml."),
```

```python
        rows = run_replay(
            store, cfg, build_extractor(cfg), model_name=search,
            since=since, save=save,
        )
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/marketsearch/shakedown.py src/marketsearch/notify/render.py src/marketsearch/store.py src/marketsearch/cli.py tests/
git commit -m "refactor: resolve criteria through the watchlist that owns a model

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `marketsearch requeue`

Rescues listings rejected under the old per-search filters. Needed because reclaiming during a normal run requires the listing to reappear in live results, and the 120-result truncation means several will not.

**Files:**
- Create: `src/marketsearch/requeue.py`
- Modify: `src/marketsearch/cli.py`
- Test: `tests/test_requeue.py`

**Interfaces:**
- Consumes: `assign`, `Assignment` (Task 2); `Store.prefiltered_listings`, `Store.reassign`, `Store.set_stage` (Task 3).
- Produces:
  - `requeue(store: Store, config: Config, dry_run: bool = False) -> list[RequeueRow]`
  - `RequeueRow(listing_id: str, title: str, old_reason: str | None, model_name: str, watchlist_name: str)`
  - `format_requeue(rows: list[RequeueRow], dry_run: bool) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_requeue.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.models import RawListing
from marketsearch.requeue import format_requeue, requeue
from marketsearch.store import Store

from tests.test_pipeline_scan import watchlist_config


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "q.db")
    s.initialize()
    yield s
    s.close()


def _stranded(store: Store) -> str:
    """A T770 rejected by the T86 search, exactly as v1 recorded it."""
    listing = RawListing(
        listing_id="1917658885567966", title="2019 Bobcat T770",
        price_cents=4_200_000, location="Cameron, Missouri",
        url="https://example.com/x", thumbnail_url=None, seller_name=None,
    )
    store.upsert_listing(listing, "bobcat-t86", "fp",
                         watchlist_name="track-loaders", model_name="bobcat-t86")
    store.set_stage(listing.listing_id, "prefiltered_out",
                    "title matched none of: 't86', 't-86', 't 86'")
    return listing.listing_id


def test_requeue_reclaims_a_listing_the_catalog_now_accepts(store: Store):
    listing_id = _stranded(store)

    rows = requeue(store, watchlist_config())
    row = store.get_listing(listing_id)

    assert [r.listing_id for r in rows] == [listing_id]
    assert rows[0].model_name == "bobcat-t770"
    assert row.stage == "pending"
    assert row.model_name == "bobcat-t770"
    assert row.watchlist_name == "track-loaders"


def test_requeue_leaves_genuinely_rejected_listings_alone(store: Store):
    junk = RawListing(
        listing_id="junk", title="2018 Bobcat T595", price_cents=3_000_000,
        location="Peoria, Illinois", url="https://example.com/j",
        thumbnail_url=None, seller_name=None,
    )
    store.upsert_listing(junk, "bobcat-t770", "fp2")
    store.set_stage("junk", "prefiltered_out", "matched no watched model")

    assert requeue(store, watchlist_config()) == []
    assert store.get_listing("junk").stage == "prefiltered_out"


def test_requeue_dry_run_writes_nothing(store: Store):
    listing_id = _stranded(store)

    rows = requeue(store, watchlist_config(), dry_run=True)

    assert len(rows) == 1
    assert store.get_listing(listing_id).stage == "prefiltered_out"


def test_requeue_never_touches_the_network(tmp_path, watchlist_config):
    """requeue takes no source argument at all — this is a signature guarantee."""
    import inspect

    assert "source" not in inspect.signature(requeue).parameters


def test_format_requeue_reports_nothing_found():
    assert "nothing" in format_requeue([], dry_run=False).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_requeue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.requeue'`.

- [ ] **Step 3: Implement the module**

Create `src/marketsearch/requeue.py`:

```python
"""Re-test stored rejections against the current catalog.

Editing config.yaml used to have no way of rescuing a listing rejected under
the old filters: rejected rows are skipped by the scanner's dedupe, and a
relist under a new id is caught as a repost. This closes that door — using the
database only, with no scraping, so it is safe to run as often as you like.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from marketsearch.config import Config
from marketsearch.models import RawListing
from marketsearch.prefilter import Assignment, assign
from marketsearch.store import ListingRow, Store

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequeueRow:
    listing_id: str
    title: str
    old_reason: str | None
    model_name: str
    watchlist_name: str


def _as_raw(listing: ListingRow) -> RawListing:
    return RawListing(
        listing_id=listing.listing_id, title=listing.title,
        price_cents=listing.price_cents, location=listing.location,
        url=listing.url, thumbnail_url=listing.thumbnail_url,
        seller_name=listing.seller_name,
    )


def requeue(store: Store, config: Config, dry_run: bool = False) -> list[RequeueRow]:
    """Reset every rejected listing the catalog now accepts back to 'pending'.

    Takes no listing source. Reclaiming during a normal run would require the
    listing to reappear in live search results, and search results are
    truncated — so this reads the ledger instead.
    """
    reclaimed: list[RequeueRow] = []

    for listing in store.prefiltered_listings():
        decision = assign(_as_raw(listing), config.watchlists)
        if not isinstance(decision, Assignment):
            continue

        reclaimed.append(
            RequeueRow(
                listing_id=listing.listing_id,
                title=listing.title,
                old_reason=listing.reject_reason,
                model_name=decision.model.name,
                watchlist_name=decision.watchlist.name,
            )
        )
        if not dry_run:
            store.reassign(
                listing.listing_id, decision.watchlist.name, decision.model.name
            )
            store.set_stage(listing.listing_id, "pending")

    log.info("requeued %d listing(s)", len(reclaimed))
    return reclaimed


def format_requeue(rows: list[RequeueRow], dry_run: bool) -> str:
    if not rows:
        return "Nothing to requeue — no rejected listing matches the current catalog."

    lines = [f"{len(rows)} listing(s) reclaimed:", ""]
    for row in rows:
        lines.append(f"  {row.model_name:<16} {row.title[:56]}")
        lines.append(f"  {'':<16} was: {row.old_reason}")
    if dry_run:
        lines.append("")
        lines.append("(Dry run — nothing written. Re-run without --dry-run to apply.)")
    return "\n".join(lines)
```

- [ ] **Step 4: Add the CLI command**

Append to `src/marketsearch/cli.py`:

```python
@app.command(name="requeue")
def requeue_command(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config"),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing."),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Re-test rejected listings against the current catalog. No scraping."""
    from marketsearch.requeue import format_requeue
    from marketsearch.requeue import requeue as run_requeue

    setup_logging(DEFAULT_LOG, verbose)
    cfg = _load(config)

    with Store(db) as store:
        store.initialize()
        rows = run_requeue(store, cfg, dry_run=dry_run)

    typer.echo(format_requeue(rows, dry_run))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_requeue.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/marketsearch/requeue.py src/marketsearch/cli.py tests/test_requeue.py
git commit -m "feat: marketsearch requeue reclaims listings the catalog now accepts

Reads the ledger only, never the network.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Cleanup — retire the search path and migrate the real config

**Files:**
- Modify: `config.yaml`, `src/marketsearch/config.py`, `src/marketsearch/prefilter.py`, `src/marketsearch/store.py`, `README.md`
- Test: `tests/test_config.py`, `tests/test_prefilter.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: `SCHEMA_VERSION = 3`; `Config.searches`, `SearchConfig`, `prefilter()`, `PrefilterResult` and `listings.search_name` all removed.

- [ ] **Step 1: Rewrite config.yaml**

Convert the 15 `searches` entries into two watchlists. `x_criteria` and `x_common` stay as YAML anchors but are now referenced twice instead of fifteen times. **Do not add `"grapple"` to the junk terms** — fall-through handles the overlap, and a junk term would discard a machine sold with an attachment.

```yaml
watchlists:

  - name: track-loaders
    criteria: *standard_criteria
    exclude: *junk_terms
    on_unknown: alert
    queries:
      - "Bobcat T770"
      - "Bobcat T750"
      - "Bobcat T870"
      - "Bobcat T76 track loader"
      - "Bobcat T86 track loader"
      - "John Deere 333G skid steer"
      - "Takeuchi TL12 track loader"
      - "Caterpillar 299D track loader"
      - "ASV Posi-Track track loader"
      - "Kubota SVL95 track loader"
      - "Case TV380 track loader"
      - "Bobcat T320 track loader"
      - "Takeuchi TL250"
      - "John Deere 329D skid steer"
    models:
      - {name: bobcat-t770,    keywords: ["t770", "t-770", "t 770"], price: {min: 15000, max: 53000}}
      - {name: bobcat-t750,    keywords: ["t750", "t-750", "t 750"], price: {min: 15000, max: 50000}}
      - {name: bobcat-t870,    keywords: ["t870", "t-870", "t 870"], price: {min: 20000, max: 60000}}
      - {name: bobcat-t76,     keywords: ["t76", "t-76", "t 76"], price: {min: 25000, max: 60000}}
      - {name: bobcat-t86,     keywords: ["t86", "t-86", "t 86"], price: {min: 30000, max: 70000}}
      - {name: deere-333,      keywords: ["333g", "333 g", "333p", "333 p", "333e", "333d"], price: {min: 15000, max: 55000}}
      - {name: takeuchi-tl12,  keywords: ["tl12", "tl 12", "tl-12", "tl12v2", "tl12 v2", "tl-12v2", "tl10v2", "tl10 v2", "tl-10v2"], price: {min: 15000, max: 53000}}
      - {name: cat-299d,       keywords: ["299d", "299 d", "299c"], price: {min: 12000, max: 53000}}
      - {name: asv-positrack,  keywords: ["rt-120", "rt120", "rt 120", "pt-100", "pt100", "rt-100", "rt100"], price: {min: 12000, max: 45000}}
      - {name: kubota-svl95,   keywords: ["svl95", "svl 95", "svl-95", "svl90", "svl 90", "svl97"], price: {min: 15000, max: 45000}}
      - {name: case-tv-tr,     keywords: ["tv380", "tv 380", "tv370", "tv 370", "tv450", "tv 450", "tr340", "tr 340"], price: {min: 12000, max: 45000}}
      - {name: bobcat-t320,    keywords: ["t320", "t-320", "t 320"], price: {min: 10000, max: 35000}}
      - {name: takeuchi-tl250, keywords: ["tl250", "tl 250", "tl-250", "tl150", "tl 150"], price: {min: 10000, max: 35000}}
      - {name: deere-329,      keywords: ["329d", "329 d", "329e", "329 e"], price: {min: 10000, max: 35000}}

  - name: attachments
    criteria: *grapple_criteria
    exclude: ["mini", "wanted", "looking for", "tractor bucket"]
    on_unknown: alert
    queries:
      - "skid steer root grapple"
    models:
      - {name: root-grapple, keywords: ["grapple"], price: {min: 800, max: 6000}}
```

Move the existing inline grapple criteria into a `x_grapple_criteria: &grapple_criteria |` anchor beside `x_criteria`, then delete the whole `searches:` block.

**Model order is load-bearing within a watchlist.** `identify_model` returns the first match, and `bobcat-t76`'s `t76` keyword would also match a `t760` if one ever existed. Keep the more specific four-character models above the shorter ones, as ordered above.

- [ ] **Step 2: Verify the real config loads and assigns correctly**

```bash
.venv/Scripts/python.exe -c "
from pathlib import Path
from marketsearch.config import load_config
from marketsearch.models import RawListing
from marketsearch.prefilter import assign, Assignment

cfg = load_config(Path('config.yaml'))
print(f'{len(cfg.watchlists)} watchlists, {sum(len(w.models) for w in cfg.watchlists)} models')

cases = [
    ('2019 Bobcat T770', 4_200_000, 'bobcat-t770'),
    ('2019 Bobcat T770 with root grapple', 4_200_000, 'bobcat-t770'),
    ('Root grapple for Bobcat T770', 300_000, 'root-grapple'),
    ('Eterra skidsteer Grapple', 549_500, 'root-grapple'),
    ('FOR SALE - 2012 Bobcat S770', 1_750_000, None),
]
for title, price, expected in cases:
    l = RawListing(listing_id='x', title=title, price_cents=price, location=None,
                   url='u', thumbnail_url=None, seller_name=None)
    r = assign(l, cfg.watchlists)
    got = r.model.name if isinstance(r, Assignment) else None
    print(('  OK  ' if got == expected else '  FAIL'), f'{title[:44]:<46} -> {got}')
"
```

Expected: `2 watchlists, 15 models` and `OK` on all five rows.

- [ ] **Step 3: Requeue the real database against a backup**

```bash
cp marketsearch.db marketsearch.db.bak-before-watchlists
.venv/Scripts/python.exe -m marketsearch.cli requeue --dry-run
```

Expected: at least the six listings the spec measured — two T770s (Cameron MO $42,000, Otley IA $35,900), two T750s (Center MO $41,000, Roach MO $26,000), a T76 (Sharon Grove KY $36,900), and a 333G (Burlington IA $49,500).

Then apply it:

```bash
.venv/Scripts/python.exe -m marketsearch.cli requeue
```

- [ ] **Step 4: Delete the legacy search path**

In `src/marketsearch/config.py`: delete `SearchConfig`, `_normalise_search`, the `searches` field on `Config`, and the `searches` handling and duplicate-name check in `load_config`. Change the empty-config guard to:

```python
    if not raw["watchlists"]:
        raise ConfigError("config must define at least one watchlist")
```

In `src/marketsearch/prefilter.py`: delete `PrefilterResult`, `_KEEP`, `_drop`, and `prefilter`. Keep `_word` and `_contains_word` — `offer` uses them.

In `tests/test_config.py`: delete `MINIMAL`, `test_legacy_searches_config_still_loads`, and the other `searches`-based tests, replacing any coverage they uniquely provided with `WATCHLIST` equivalents. In `tests/test_prefilter.py`: delete the tests for the removed `prefilter()`.

- [ ] **Step 5: Drop the search_name column at schema v3**

In `src/marketsearch/store.py`, set `SCHEMA_VERSION = 3`, remove the `search_name` line from `_SCHEMA`, remove `search_name` from `ListingRow` and `_row_to_listing`, and drop the parameter from `upsert_listing`:

```python
    def upsert_listing(
        self,
        listing: RawListing,
        fp: str,
        watchlist_name: str | None = None,
        model_name: str | None = None,
    ) -> None:
```

Remove `search_name` from that method's INSERT column list and values tuple, and update the three call sites in `pipeline.py` (`_process` twice, `WatchSyncer._baseline` once) plus `listing_row_from`, which loses its `search_name=model_name` argument.

Dropping a field from the frozen `ListingRow` and a positional parameter from
`upsert_listing` breaks every test that supplies them. Update these too:

- `tests/test_render.py` — the `listing()` helper drops `search_name="bobcat-t770"`.
- `tests/test_shakedown.py` — `seed_extracted` drops the positional `"bobcat-t770"`,
  becoming `store.upsert_listing(listing(listing_id), f"fp{listing_id}", watchlist_name="track-loaders", model_name="bobcat-t770")`.
- `tests/test_store.py` and `tests/test_requeue.py` — every
  `store.upsert_listing(x, "<name>", "fp")` drops its middle argument.

Find them all with:

```bash
grep -rn "upsert_listing\|search_name" tests/
```

Extend `_migrate`:

```python
        if version < 3:
            columns = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(listings)")
            }
            if "search_name" in columns:
                # SQLite 3.35+ supports DROP COLUMN; Python 3.12 ships 3.4x.
                self._conn.execute("ALTER TABLE listings DROP COLUMN search_name")
```

Add a migration test to `tests/test_store.py`:

```python
def test_v2_database_drops_search_name(tmp_path: Path):
    path = tmp_path / "v2.db"
    with Store(path) as store:
        store.initialize()
    with Store(path) as store:
        store.initialize()
        columns = {r["name"] for r in store._conn.execute("PRAGMA table_info(listings)")}
    assert "search_name" not in columns
    assert {"watchlist_name", "model_name"} <= columns
```

- [ ] **Step 6: Update the README**

In `README.md`, the "What it does" bullet reading "Drops listings that fail cheap gates (price band, title keywords) before loading a single detail page" becomes:

```markdown
- Pools every search query's results, then keeps any listing matching any model
  in your catalog — so a query for one machine still catches another you want.
  Drops the rest before loading a single detail page.
```

Add `marketsearch requeue` to the command list, described as re-testing stored rejections against the current catalog without scraping.

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS, with no references to `SearchConfig`, `prefilter()`, or `search_name` remaining:

```bash
grep -rn "SearchConfig\|search_name\|title_must_match" src/ tests/ config.yaml
```

Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: retire the per-search filter path

config.yaml moves to two watchlists; schema v3 drops listings.search_name.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification

After Task 7, confirm against the real database that the change did what it claimed:

```bash
.venv/Scripts/python.exe -c "
import sqlite3
c = sqlite3.connect('marketsearch.db'); c.row_factory = sqlite3.Row
print('reclaimed and awaiting extraction:')
for r in c.execute(\"SELECT model_name, price_cents, location, title FROM listings WHERE stage='pending' ORDER BY price_cents DESC\"):
    print(f\"  {r['model_name']:<16} \${(r['price_cents'] or 0)/100:>9,.0f}  {(r['location'] or '')[:22]:<22} {r['title'][:46]}\")
"
```

Expected: the six machines from the spec's measurement, each labelled with the model its title actually names rather than the query that found it.

Then run one real sweep and confirm the counters:

```bash
.venv/Scripts/python.exe -m marketsearch.cli run --dry-run --verbose
```

Expected: a single pooled listing count in the log rather than per-search lines, and `prefiltered` well below its previous share of `found`.

## Known follow-up, deliberately out of scope

The 120-result truncation is untouched. `max_listings_per_search` defaults to 100, and `_MAX_SCROLLS = 15` in `sources/facebook.py:53` caps every query at roughly 384 listings regardless of that setting. The spec argues depth should be re-measured after this ships, since pooling extracts more from the same scrape. The 8-week-old Washington, IL T770 that started this investigation is still outside the window and will stay there until depth is addressed.
