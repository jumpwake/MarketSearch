# Results Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `marketsearch dashboard` renders a self-contained HTML page of every judged listing, ranked by dollars per remaining engine hour, for browsing results and tuning criteria.

**Architecture:** A pure renderer (`dashboard.py`) turns rows plus config into an HTML string; the CLI does all I/O around it. Presentation formatters shared with the email renderer move to `format.py` so neither copy drifts. All interaction is vanilla JS over server-rendered nodes carrying `data-*` values — no build step, no dependencies, no external assets.

**Tech Stack:** Python 3.12+, Typer, SQLite (stdlib), pytest. No frontend framework.

Spec: [`docs/superpowers/specs/2026-07-28-dashboard-design.md`](../specs/2026-07-28-dashboard-design.md)

## Global Constraints

- Python 3.12+. Run tests with `.venv/Scripts/python.exe -m pytest` from the repo root.
- **`render_dashboard` must stay pure** — no file, network, clock, or database access. Timestamps are passed in. This is what makes every test run without a browser or database.
- **Every interpolated value must be HTML-escaped.** Titles come from Facebook, reasoning from an LLM; both are untrusted.
- The page must be a **single self-contained file**: inline CSS and JS, no CDN, no build step, no external assets other than remote photo URLs.
- Money is integer cents everywhere in Python; only formatters render dollars.
- Default assumed machine life is **6,000 hours**.
- Listings with unknown `engine_hours` **sort last and are labelled** — never treated as zero.
- Do not touch `marketsearch.db`, `marketsearch.db-wal`, `marketsearch.db-shm`, or any `marketsearch.db.bak-*` file. Tests use `tmp_path`.
- End every commit message with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

### Task 1: Extract shared presentation formatters

`notify/render.py` already has the exact attribute-flattening the dashboard needs. Move it to a shared module so the two renderers cannot drift, rather than copying eleven rows of formatting.

**Files:**
- Create: `src/marketsearch/format.py`
- Modify: `src/marketsearch/notify/render.py:48-82`
- Test: `tests/test_format.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `dollars(cents: int | None) -> str`
  - `yes_no(value: object) -> str`
  - `hours_text(attributes: dict) -> str`
  - `attribute_rows(attributes: dict) -> list[tuple[str, str]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_format.py`:

```python
from __future__ import annotations

from marketsearch.format import attribute_rows, dollars, hours_text, yes_no

ATTRS = {
    "core": {"year": 2021, "engine_hours": 2200},
    "specs": {"cab_enclosed": True, "has_ac": False, "two_speed": True,
              "high_flow": True, "undercarriage_condition": "good"},
    "condition": {"runs": True, "stated_issues": ["ac intermittent"]},
    "deal": {"attachments": ["bucket"], "seller_type": "private"},
}


def test_dollars_formats_cents_with_separators():
    assert dollars(3_950_000) == "$39,500"


def test_dollars_renders_a_dash_for_missing_price():
    assert dollars(None) == "—"


def test_yes_no_maps_booleans_and_passes_through_strings():
    assert yes_no(True) == "yes"
    assert yes_no(False) == "no"
    assert yes_no(None) == "—"
    assert yes_no("good") == "good"


def test_hours_text_formats_with_a_separator():
    assert hours_text(ATTRS) == "2,200"


def test_hours_text_handles_a_missing_value():
    assert hours_text({"core": {}}) == "—"


def test_attribute_rows_flattens_every_section():
    rows = dict(attribute_rows(ATTRS))
    assert rows["Hours"] == "2,200"
    assert rows["Year"] == "2021"
    assert rows["Cab"] == "yes"
    assert rows["A/C"] == "no"
    assert rows["High flow"] == "yes"
    assert rows["Undercarriage"] == "good"
    assert rows["Issues"] == "ac intermittent"
    assert rows["Seller"] == "private"


def test_attribute_rows_says_none_stated_for_empty_lists():
    rows = dict(attribute_rows({"core": {}, "specs": {}, "condition": {},
                                "deal": {}}))
    assert rows["Issues"] == "none stated"
    assert rows["Attachments"] == "none stated"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_format.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.format'`.

- [ ] **Step 3: Create the shared module**

Create `src/marketsearch/format.py` by moving the bodies of `_dollars`, `_yes_no`, `_hours`, and `_attribute_rows` out of `notify/render.py` unchanged apart from their names:

```python
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
```

- [ ] **Step 4: Point render.py at the shared module**

In `src/marketsearch/notify/render.py`, delete the four moved functions and add near the top:

```python
from marketsearch.format import attribute_rows, dollars, hours_text, yes_no
```

Then update every call site in that file: `_dollars(` → `dollars(`, `_yes_no(` → `yes_no(`, `_hours(` → `hours_text(`, `_attribute_rows(` → `attribute_rows(`. Find them all with:

```bash
grep -n "_dollars\|_yes_no\|_hours(\|_attribute_rows" src/marketsearch/notify/render.py
```

Expected after the edit: no matches.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS. This is a behaviour-preserving move — every pre-existing `tests/test_render.py` test must still pass untouched. If any needs changing, the move was not behaviour-preserving; revert and redo.

- [ ] **Step 6: Commit**

```bash
git add src/marketsearch/format.py src/marketsearch/notify/render.py tests/test_format.py
git commit -m "refactor: share presentation formatters between renderers

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Value ranking

The heart of the feature. Pure arithmetic, no HTML.

**Files:**
- Create: `src/marketsearch/dashboard.py`
- Test: `tests/test_dashboard_ranking.py`

**Interfaces:**
- Consumes: `ListingRow`, `ExtractionRow` from `marketsearch.store`; `ListingDetail` from `marketsearch.models`.
- Produces:
  - `DEFAULT_LIFE_HOURS = 6000`
  - `JudgedListing(listing: ListingRow, detail: ListingDetail, extraction: ExtractionRow)` — frozen dataclass
  - `Pick(row: JudgedListing, hours: int | None, value_per_hour: float | None)` — frozen dataclass
  - `engine_hours(extraction: ExtractionRow) -> int | None`
  - `value_per_remaining_hour(price_cents: int | None, hours: int | None, life_hours: int) -> float | None`
  - `top_picks(rows: list[JudgedListing], live_models: set[str], life_hours: int, limit: int = 10) -> list[Pick]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_ranking.py`:

```python
from __future__ import annotations

from marketsearch.dashboard import (
    JudgedListing,
    engine_hours,
    top_picks,
    value_per_remaining_hour,
)
from marketsearch.models import ListingDetail
from marketsearch.store import ExtractionRow, ListingRow

LIVE = {"bobcat-t770", "kubota-svl95"}


def judged(listing_id, model_name, price_cents, hours, verdict="match",
           title="a machine", reasoning="because", unknowns=None,
           thumbnail_url="https://example.com/a.jpg") -> JudgedListing:
    """One judged listing. `ListingRow` and `ExtractionRow` are frozen, so every
    varying field is a parameter here rather than assigned after construction."""
    return JudgedListing(
        listing=ListingRow(
            listing_id=listing_id, title=title, price_cents=price_cents,
            location="Peoria, IL", url=f"https://example.com/{listing_id}",
            thumbnail_url=thumbnail_url, seller_name="Dale",
            fingerprint="fp", stage="matched", reject_reason=None, watched=False,
            first_seen_at="2026-07-27T10:00:00+00:00",
            last_seen_at="2026-07-27T10:00:00+00:00",
            last_change_check_at=None, extraction_attempts=0,
            watchlist_name="track-loaders", model_name=model_name,
        ),
        detail=ListingDetail(
            listing_id=listing_id, description="runs strong",
            structured_fields={}, photo_urls=[], distance_miles=None,
        ),
        extraction=ExtractionRow(
            listing_id=listing_id,
            attributes={"core": {"engine_hours": hours}},
            verdict=verdict, confidence=0.8, reasoning=reasoning,
            unknowns=unknowns or [], model="claude-opus-5",
            created_at="2026-07-27T10:00:00+00:00",
        ),
    )


def test_engine_hours_reads_the_core_attribute():
    assert engine_hours(judged("1", "bobcat-t770", 100, 2200).extraction) == 2200


def test_engine_hours_is_none_when_absent():
    assert engine_hours(judged("1", "bobcat-t770", 100, None).extraction) is None


def test_value_is_price_over_remaining_life():
    # $30,000 with 1,000 hours used of an assumed 6,000 -> 5,000 remaining
    assert value_per_remaining_hour(3_000_000, 1000, 6000) == 6.0


def test_value_is_none_without_hours():
    assert value_per_remaining_hour(3_000_000, None, 6000) is None


def test_value_is_none_without_a_price():
    assert value_per_remaining_hour(None, 1000, 6000) is None


def test_hours_beyond_assumed_life_do_not_divide_by_zero():
    """Clamped to 1 remaining hour rather than raising or going negative."""
    assert value_per_remaining_hour(3_000_000, 6000, 6000) == 30_000.0
    assert value_per_remaining_hour(3_000_000, 9999, 6000) == 30_000.0


def test_top_picks_excludes_non_match_verdicts():
    rows = [
        judged("1", "bobcat-t770", 3_950_000, 2200, verdict="unverifiable"),
        judged("2", "kubota-svl95", 3_200_000, 2984, verdict="no_match"),
    ]
    assert top_picks(rows, LIVE, 6000) == []


def test_top_picks_excludes_models_not_in_the_live_catalog():
    """A retired search's matches must not be presented as things to go buy."""
    rows = [judged("1", "root-grapple", 300_000, None)]
    assert top_picks(rows, LIVE, 6000) == []


def test_ranking_at_six_thousand_hours_prefers_low_hours():
    rows = [
        judged("svl95", "kubota-svl95", 3_200_000, 2984),
        judged("svl90", "kubota-svl95", 4_500_000, 1005),
        judged("t770", "bobcat-t770", 3_950_000, 2200),
    ]
    assert [p.row.listing.listing_id for p in top_picks(rows, LIVE, 6000)] == [
        "svl90", "t770", "svl95",
    ]


def test_ranking_inverts_at_ten_thousand_hours():
    """The assumed life reorders the list rather than merely adjusting it.

    This is why the figure is a user-facing control and not a constant: the
    same three machines rank in exactly the opposite order.
    """
    rows = [
        judged("svl95", "kubota-svl95", 3_200_000, 2984),
        judged("svl90", "kubota-svl95", 4_500_000, 1005),
        judged("t770", "bobcat-t770", 3_950_000, 2200),
    ]
    assert [p.row.listing.listing_id for p in top_picks(rows, LIVE, 10000)] == [
        "svl95", "svl90", "t770",
    ]


def test_unknown_hours_sort_last_rather_than_first():
    """Treating unknown hours as zero would rank them best. It must not."""
    rows = [
        judged("unknown", "bobcat-t770", 100_000, None),
        judged("known", "bobcat-t770", 4_500_000, 2200),
    ]
    picks = top_picks(rows, LIVE, 6000)
    assert [p.row.listing.listing_id for p in picks] == ["known", "unknown"]
    assert picks[-1].value_per_hour is None


def test_top_picks_honours_the_limit():
    rows = [judged(str(i), "bobcat-t770", 3_000_000 + i, 2000) for i in range(15)]
    assert len(top_picks(rows, LIVE, 6000, limit=10)) == 10


def test_pick_carries_the_numbers_it_was_ranked_on():
    rows = [judged("t770", "bobcat-t770", 3_950_000, 2200)]
    pick = top_picks(rows, LIVE, 6000)[0]
    assert pick.hours == 2200
    assert round(pick.value_per_hour, 2) == 10.39
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_ranking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketsearch.dashboard'`.

- [ ] **Step 3: Implement the ranking**

Create `src/marketsearch/dashboard.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_ranking.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add src/marketsearch/dashboard.py tests/test_dashboard_ranking.py
git commit -m "feat: rank matches by dollars per remaining engine hour

The assumed machine life inverts the ranking rather than adjusting it,
which is why it becomes a control rather than a constant.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: HTML rendering

**Files:**
- Modify: `src/marketsearch/dashboard.py`
- Test: `tests/test_dashboard_render.py`

**Interfaces:**
- Consumes: everything from Task 2; `attribute_rows`, `dollars` from `marketsearch.format`; `Config` from `marketsearch.config`.
- Produces:
  - `render_dashboard(rows: list[JudgedListing], config: Config, generated_at: str, life_hours: int = DEFAULT_LIFE_HOURS) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_render.py`. Import `judged` from the ranking test module so the fixture is defined once:

```python
from __future__ import annotations

from marketsearch.dashboard import render_dashboard
from tests.test_dashboard_ranking import judged
from tests.test_pipeline_scan import watchlist_config

STAMP = "2026-07-28T09:00:00+00:00"


def render(rows, life_hours=6000):
    return render_dashboard(rows, watchlist_config(), STAMP, life_hours)


def test_a_script_tag_in_a_title_is_escaped():
    """Titles come from Facebook. They are not trusted."""
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200,
                          title="<script>alert(1)</script>")])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_script_tag_in_reasoning_is_escaped():
    """Reasoning comes from an LLM. Also not trusted."""
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200,
                          reasoning="<img src=x onerror=alert(1)>")])
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_the_page_is_self_contained():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200)])
    for forbidden in ("<script src=", "<link rel=\"stylesheet\"", "cdn.", "https://unpkg"):
        assert forbidden not in html


def test_every_judged_listing_appears_in_the_browse_list():
    rows = [
        judged("1", "bobcat-t770", 3_000_000, 2200),
        judged("2", "root-grapple", 300_000, None, verdict="no_match"),
        judged("3", "kubota-svl95", 3_200_000, 2984, verdict="unverifiable"),
    ]
    html = render(rows)
    for listing_id in ("1", "2", "3"):
        assert f'data-listing-id="{listing_id}"' in html


def test_top_picks_states_the_real_count_rather_than_padding():
    rows = [judged("1", "bobcat-t770", 3_000_000, 2200)]
    assert "1 match" in render(rows)


def test_top_picks_omits_retired_models_but_browse_keeps_them():
    rows = [judged("g", "root-grapple", 300_000, None)]
    html = render(rows)
    assert 'data-pick="g"' not in html
    assert 'data-listing-id="g"' in html


def test_cards_carry_the_data_the_client_script_sorts_on():
    html = render([judged("1", "bobcat-t770", 3_950_000, 2200)])
    assert 'data-price-cents="3950000"' in html
    assert 'data-hours="2200"' in html
    assert 'data-verdict="match"' in html
    assert 'data-model="bobcat-t770"' in html


def test_unknown_hours_are_labelled_not_zero():
    html = render([judged("1", "bobcat-t770", 3_000_000, None)])
    assert 'data-hours=""' in html
    assert "hours unknown" in html.lower()


def test_a_missing_thumbnail_renders_a_placeholder():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200,
                          thumbnail_url=None)])
    assert "no photo" in html.lower()


def test_the_criteria_panel_shows_the_watchlist_criteria():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200)])
    assert "Under 3000 engine hours." in html


def test_the_assumed_life_control_reflects_the_value_passed_in():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200)], life_hours=8000)
    assert 'value="8000"' in html


def test_an_empty_corpus_renders_a_valid_page():
    html = render([])
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    assert "nothing judged yet" in html.lower()


def test_unknowns_are_surfaced_on_the_card():
    html = render([judged("1", "bobcat-t770", 3_000_000, 2200,
                          unknowns=["engine_hours", "quick_attach_plate"])])
    assert "quick_attach_plate" in html
```

Note `watchlist_config()` is imported from `tests/test_pipeline_scan.py`, where
it is a plain factory function, not a pytest fixture — call it, do not take it
as a test parameter.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_render.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_dashboard'`.

- [ ] **Step 3: Implement the renderer**

Append to `src/marketsearch/dashboard.py`. Escape with `html.escape` on every
interpolated value:

```python
import html as _html
import json

from marketsearch.config import Config
from marketsearch.format import attribute_rows, dollars


def _esc(value: object) -> str:
    return _html.escape("" if value is None else str(value), quote=True)


def _live_models(config: Config) -> set[str]:
    return {m.name for w in config.watchlists for m in w.models}


def _criteria_blocks(config: Config) -> list[tuple[str, str]]:
    return [(w.name, w.criteria) for w in config.watchlists]


def _photo(row: JudgedListing) -> str:
    url = row.listing.thumbnail_url
    if not url:
        return '<div class="photo photo--empty">no photo</div>'
    return (
        f'<img class="photo" loading="lazy" alt="" src="{_esc(url)}" '
        f"onerror=\"this.outerHTML='<div class=&quot;photo photo--empty&quot;>"
        f"photo expired</div>'\">"
    )


def _card(row: JudgedListing, life_hours: int) -> str:
    listing, extraction = row.listing, row.extraction
    hours = engine_hours(extraction)
    value = value_per_remaining_hour(listing.price_cents, hours, life_hours)

    rows_html = "".join(
        f"<div class=\"attr\"><dt>{_esc(label)}</dt><dd>{_esc(text)}</dd></div>"
        for label, text in attribute_rows(extraction.attributes)
    )
    unknowns_html = ""
    if extraction.unknowns:
        chips = "".join(
            f'<span class="chip chip--unknown">{_esc(u)}</span>'
            for u in extraction.unknowns
        )
        unknowns_html = f'<div class="unknowns"><span>could not determine</span>{chips}</div>'

    value_text = "hours unknown" if value is None else f"${value:,.2f}/hr"

    return f"""
<article class="card" data-listing-id="{_esc(listing.listing_id)}"
         data-verdict="{_esc(extraction.verdict)}"
         data-model="{_esc(listing.model_name)}"
         data-price-cents="{listing.price_cents or 0}"
         data-hours="{'' if hours is None else hours}"
         data-confidence="{extraction.confidence}"
         data-seen="{_esc(listing.first_seen_at)}">
  {_photo(row)}
  <div class="body">
    <h3><a href="{_esc(listing.url)}" target="_blank" rel="noopener">{_esc(listing.title)}</a></h3>
    <p class="meta">
      <span class="price">{_esc(dollars(listing.price_cents))}</span>
      <span class="badge badge--{_esc(extraction.verdict)}">{_esc(extraction.verdict)}</span>
      <span class="chip">{_esc(listing.model_name)}</span>
      <span class="chip">{_esc(listing.location)}</span>
      <span class="chip value-chip">{_esc(value_text)}</span>
      <span class="chip">confidence {extraction.confidence:.2f}</span>
    </p>
    {unknowns_html}
    <dl class="attrs">{rows_html}</dl>
    <blockquote>{_esc(extraction.reasoning)}</blockquote>
  </div>
</article>"""


def render_dashboard(
    rows: list[JudgedListing],
    config: Config,
    generated_at: str,
    life_hours: int = DEFAULT_LIFE_HOURS,
) -> str:
    """Judged listings and config in, one self-contained HTML page out."""
    live = _live_models(config)
    picks = top_picks(rows, live, life_hours)

    if picks:
        noun = "match" if len(picks) == 1 else "matches"
        picks_head = f"{len(picks)} {noun} from models in your config"
        picks_html = "".join(
            f'<li data-pick="{_esc(p.row.listing.listing_id)}">'
            f'<a href="{_esc(p.row.listing.url)}" target="_blank" rel="noopener">'
            f"{_esc(p.row.listing.title)}</a>"
            f'<span class="pick-value">'
            f"{'hours unknown' if p.value_per_hour is None else f'${p.value_per_hour:,.2f}/hr'}"
            f"</span>"
            f'<span class="pick-facts">'
            f"{'—' if p.hours is None else format(p.hours, ',')} hrs · "
            f"{_esc(dollars(p.row.listing.price_cents))}</span></li>"
            for p in picks
        )
        picks_html = f"<ol class='picks'>{picks_html}</ol>"
    else:
        picks_head = "no matches from models in your config"
        picks_html = ""

    models = sorted({r.listing.model_name or "" for r in rows} - {""})
    model_options = "".join(
        f'<option value="{_esc(m)}">{_esc(m)}</option>' for m in models
    )

    criteria_html = "".join(
        f"<details><summary>{_esc(name)}</summary><pre>{_esc(text)}</pre></details>"
        for name, text in _criteria_blocks(config)
    )

    cards = "".join(_card(r, life_hours) for r in rows) or (
        '<p class="empty">Nothing judged yet — run a sweep first.</p>'
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MarketSearch results</title>
<style>{_CSS}</style></head>
<body>
<header>
  <h1>MarketSearch results</h1>
  <p class="sub">{len(rows)} judged · generated {_esc(generated_at)}</p>
</header>

<section class="criteria"><h2>Criteria</h2>{criteria_html}</section>

<section class="top">
  <h2>Top picks <span class="sub">{_esc(picks_head)}</span></h2>
  <label class="life">Assumed usable life
    <input id="life" type="range" min="3000" max="12000" step="500" value="{life_hours}">
    <output id="lifeOut">{life_hours:,}</output> hrs
  </label>
  <p class="caveat">Ranked on hours and price only — blind to condition.
     Only matches your criteria already accepted appear here.</p>
  {picks_html}
</section>

<section class="browse">
  <h2>All judged listings</h2>
  <div class="controls">
    <input id="q" type="search" placeholder="search title, location, reasoning">
    <select id="model"><option value="">all models</option>{model_options}</select>
    <span class="verdicts">
      <button class="v on" data-v="match">match</button>
      <button class="v on" data-v="unverifiable">unverifiable</button>
      <button class="v on" data-v="no_match">no match</button>
    </span>
    <select id="sort">
      <option value="value">best value</option>
      <option value="confidence">confidence</option>
      <option value="price">price</option>
      <option value="hours">hours</option>
      <option value="seen">newest</option>
    </select>
  </div>
  <div id="cards">{cards}</div>
</section>

<script>{_JS}</script>
</body></html>"""
```

Define `_CSS` and `_JS` as module-level string constants in the same file. The
JS must recompute value with the **same formula as Python** —
`price / max(life - hours, 1)` — reading `data-price-cents` and `data-hours`,
placing cards with empty `data-hours` last under the value and hours sorts:

```python
_JS = """
const cards = [...document.querySelectorAll('.card')];
const life = document.getElementById('life');
const lifeOut = document.getElementById('lifeOut');
const q = document.getElementById('q');
const model = document.getElementById('model');
const sort = document.getElementById('sort');
const off = new Set();

// Same formula as value_per_remaining_hour in dashboard.py. Keep in step.
function value(card, lifeHours) {
  const h = card.dataset.hours;
  if (h === '') return null;
  const cents = Number(card.dataset.priceCents);
  if (!cents) return null;
  return (cents / 100) / Math.max(lifeHours - Number(h), 1);
}

function apply() {
  const lifeHours = Number(life.value);
  lifeOut.textContent = lifeHours.toLocaleString();
  const needle = q.value.trim().toLowerCase();
  const wanted = model.value;

  for (const card of cards) {
    const hit = !needle || card.textContent.toLowerCase().includes(needle);
    const modelOk = !wanted || card.dataset.model === wanted;
    const verdictOk = !off.has(card.dataset.verdict);
    card.hidden = !(hit && modelOk && verdictOk);
    const v = value(card, lifeHours);
    const chip = card.querySelector('.value-chip');
    if (chip) chip.textContent = v === null ? 'hours unknown'
      : '$' + v.toLocaleString(undefined, {minimumFractionDigits: 2,
                                           maximumFractionDigits: 2}) + '/hr';
  }

  const key = sort.value;
  const shown = cards.filter(c => !c.hidden);
  shown.sort((a, b) => {
    if (key === 'value') {
      const av = value(a, lifeHours), bv = value(b, lifeHours);
      if (av === null) return bv === null ? 0 : 1;   // unknown hours sort last
      if (bv === null) return -1;
      return av - bv;
    }
    if (key === 'confidence') return b.dataset.confidence - a.dataset.confidence;
    if (key === 'price') return a.dataset.priceCents - b.dataset.priceCents;
    if (key === 'hours') {
      if (a.dataset.hours === '') return b.dataset.hours === '' ? 0 : 1;
      if (b.dataset.hours === '') return -1;
      return a.dataset.hours - b.dataset.hours;
    }
    return b.dataset.seen.localeCompare(a.dataset.seen);
  });
  const host = document.getElementById('cards');
  for (const card of shown) host.appendChild(card);
}

for (const el of [q, model, sort, life]) el.addEventListener('input', apply);
for (const b of document.querySelectorAll('.v')) {
  b.addEventListener('click', () => {
    b.classList.toggle('on');
    if (off.has(b.dataset.v)) off.delete(b.dataset.v); else off.add(b.dataset.v);
    apply();
  });
}
apply();
"""
```

And `_CSS` as:

```python
_CSS = """
:root {
  --bg:#fbfbfa; --fg:#1c1e21; --muted:#6b7280; --line:#e3e5e8; --card:#fff;
  --match:#0f7b4f; --unver:#9a6700; --nomatch:#8a8f98; --accent:#1d4ed8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#16181c; --fg:#e6e8eb; --muted:#9aa1ab; --line:#2a2e35; --card:#1e2126;
    --match:#4ade80; --unver:#fbbf24; --nomatch:#8a8f98; --accent:#7aa2ff;
  }
}
* { box-sizing:border-box }
body {
  margin:0; padding:24px; background:var(--bg); color:var(--fg);
  font:15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
  max-width:1100px; margin-inline:auto;
}
h1 { font-size:22px; margin:0 }
h2 { font-size:16px; margin:0 0 12px; display:flex; gap:10px; align-items:baseline }
.sub, .caveat { color:var(--muted); font-size:13px; font-weight:400 }
section { margin:28px 0 }
.criteria pre {
  white-space:pre-wrap; font:12px/1.5 ui-monospace, Consolas, monospace;
  background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:12px; overflow-x:auto;
}
.criteria summary { cursor:pointer; color:var(--accent) }
.life { display:flex; gap:8px; align-items:center; font-size:13px; margin-bottom:6px }
.life input { flex:0 1 240px }
.picks { margin:12px 0 0; padding-left:22px }
.picks li { margin-bottom:6px }
.pick-value { font-weight:600; margin-left:8px }
.pick-facts { color:var(--muted); margin-left:8px; font-size:13px }
.controls { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px }
.controls input, .controls select, .v {
  padding:6px 10px; border:1px solid var(--line); border-radius:8px;
  background:var(--card); color:var(--fg); font:inherit; font-size:13px;
}
.controls input[type=search] { flex:1 1 220px }
.v { cursor:pointer; opacity:.4 }
.v.on { opacity:1; border-color:var(--accent) }
#cards { display:grid; gap:14px }
.card {
  display:flex; gap:14px; background:var(--card); border:1px solid var(--line);
  border-radius:12px; padding:14px;
}
.card[hidden] { display:none }
.photo {
  width:150px; height:112px; object-fit:cover; border-radius:8px; flex:none;
  background:var(--bg);
}
.photo--empty {
  display:grid; place-items:center; color:var(--muted); font-size:12px;
  border:1px dashed var(--line);
}
.body { min-width:0; flex:1 }
.card h3 { margin:0 0 6px; font-size:15px }
.card h3 a { color:inherit; text-decoration:none }
.card h3 a:hover { text-decoration:underline }
.meta { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin:0 0 8px }
.price { font-weight:600 }
.chip {
  font-size:12px; color:var(--muted); border:1px solid var(--line);
  border-radius:999px; padding:1px 8px;
}
.value-chip { color:var(--fg); font-weight:600 }
.badge {
  font-size:12px; font-weight:600; border-radius:999px; padding:1px 8px;
  color:#fff; background:var(--nomatch);
}
.badge--match { background:var(--match) }
.badge--unverifiable { background:var(--unver) }
.unknowns { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-bottom:8px }
.unknowns > span:first-child { font-size:12px; color:var(--unver); font-weight:600 }
.chip--unknown { border-color:var(--unver); color:var(--unver) }
.attrs { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
         gap:2px 12px; margin:0 0 8px }
.attr { display:flex; gap:6px; font-size:13px; min-width:0 }
.attr dt { color:var(--muted); flex:none }
.attr dd { margin:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
blockquote {
  margin:0; padding-left:10px; border-left:3px solid var(--line);
  color:var(--muted); font-size:13px;
}
.empty { color:var(--muted) }
@media (max-width:640px) {
  .card { flex-direction:column }
  .photo { width:100%; height:180px }
}
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_render.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/marketsearch/dashboard.py tests/test_dashboard_render.py tests/test_dashboard_ranking.py
git commit -m "feat: render the results dashboard page

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: CLI command

**Files:**
- Modify: `src/marketsearch/cli.py`, `README.md`
- Test: `tests/test_dashboard_cli.py`

**Interfaces:**
- Consumes: `render_dashboard`, `JudgedListing`, `DEFAULT_LIFE_HOURS` (Task 3); `Store.listings_with_details` and `shakedown.parse_since` (existing).
- Produces: the `marketsearch dashboard` command.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_cli.py`:

```python
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marketsearch.cli import app
from marketsearch.models import ListingDetail, RawListing
from marketsearch.pipeline import content_hash
from marketsearch.store import Store

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Same shape as the fixture in tests/test_cli.py: a tmp dir holding a
    real config.yaml copied from tests/fixtures/."""
    shutil.copy(
        Path(__file__).parent / "fixtures" / "config.yaml",
        tmp_path / "config.yaml",
    )
    return tmp_path


def seed(db: Path) -> None:
    listing = RawListing(
        listing_id="1", title="2021 Bobcat T770", price_cents=3_950_000,
        location="Peoria, IL", url="https://example.com/1",
        thumbnail_url=None, seller_name="Dale",
    )
    with Store(db) as store:
        store.initialize()
        store.upsert_listing(listing, "fp", watchlist_name="track-loaders",
                             model_name="bobcat-t770")
        detail = ListingDetail(listing_id="1", description="runs strong",
                               structured_fields={}, photo_urls=[],
                               distance_miles=None)
        store.save_detail(detail, content_hash(detail))
        store.save_extraction(
            listing_id="1", attributes={"core": {"engine_hours": 2200}},
            verdict="match", confidence=0.72, reasoning="low hours",
            unknowns=[], model="claude-opus-5",
            input_tokens=1, output_tokens=1, cost_cents=0.1,
        )
        store.set_stage("1", "matched")


def run_dashboard(project: Path, db: Path, out: Path, *extra: str):
    return runner.invoke(app, [
        "dashboard", "--db", str(db), "--config", str(project / "config.yaml"),
        "--out", str(out), "--no-open", *extra,
    ])


def test_dashboard_writes_a_page(project: Path):
    db, out = project / "d.db", project / "dash.html"
    seed(db)
    result = run_dashboard(project, db, out)
    assert result.exit_code == 0, result.output
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert 'data-listing-id="1"' in html


def test_dashboard_reports_an_empty_database_without_crashing(project: Path):
    db, out = project / "empty.db", project / "dash.html"
    with Store(db) as store:
        store.initialize()
    result = run_dashboard(project, db, out)
    assert result.exit_code == 0, result.output
    assert "nothing judged yet" in out.read_text(encoding="utf-8").lower()


def test_life_hours_flag_is_passed_through(project: Path):
    db, out = project / "l.db", project / "dash.html"
    seed(db)
    result = run_dashboard(project, db, out, "--life-hours", "9000")
    assert result.exit_code == 0, result.output
    assert 'value="9000"' in out.read_text(encoding="utf-8")


def test_dashboard_appears_in_help():
    result = runner.invoke(app, ["--help"])
    assert "dashboard" in result.stdout
```

`tests/fixtures/config.yaml` already carries a `track-loaders` watchlist with a
`bobcat-t770` model, so the seeded listing lands in Top Picks.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_cli.py -v`
Expected: FAIL — no such command `dashboard`.

- [ ] **Step 3: Add the command**

Append to `src/marketsearch/cli.py`:

```python
@app.command()
def dashboard(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config"),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    out: Path = typer.Option(Path("dashboard.html"), "--out"),
    since: str = typer.Option("365d", "--since", help="e.g. 90d, 36h."),
    life_hours: int = typer.Option(
        DEFAULT_LIFE_HOURS, "--life-hours",
        help="Assumed usable machine life, for the value ranking.",
    ),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Browse every judged listing, ranked by value, for tuning criteria."""
    import webbrowser
    from datetime import datetime, timezone

    from marketsearch.dashboard import JudgedListing, render_dashboard
    from marketsearch.shakedown import parse_since

    cfg = _load(config)
    cutoff = parse_since(since).isoformat()

    with Store(db) as store:
        store.initialize()
        rows = [
            JudgedListing(listing=listing, detail=detail, extraction=extraction)
            for listing, detail, extraction in store.listings_with_details(
                None, cutoff
            )
            if extraction is not None
        ]

    html = render_dashboard(
        rows, cfg, datetime.now(timezone.utc).isoformat(timespec="seconds"),
        life_hours,
    )
    out.write_text(html, encoding="utf-8")
    typer.echo(f"{len(rows)} judged listing(s) — wrote {out}")
    if open_browser:
        webbrowser.open(out.resolve().as_uri())
```

Add `DEFAULT_LIFE_HOURS` to the imports at the top of `cli.py`:

```python
from marketsearch.dashboard import DEFAULT_LIFE_HOURS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Update the README**

In `README.md`, add to the command list:

```markdown
`marketsearch dashboard` — browse every judged listing in a local HTML page,
ranked by dollars per remaining engine hour. Slide the assumed-life control to
see how sensitive the ranking is to that assumption. Read-only; regenerate by
re-running it.
```

- [ ] **Step 6: Run the full suite and generate a real page**

```bash
.venv/Scripts/python.exe -m pytest -v
.venv/Scripts/python.exe -m marketsearch.cli dashboard --no-open
```

Expected: suite green; `dashboard.html` written reporting 32 judged listings.
Open it by hand and confirm Top Picks shows 3 machines, moving the assumed-life
slider from 6,000 to 10,000 reverses their order, and the verdict chips filter.

- [ ] **Step 7: Commit**

```bash
git add src/marketsearch/cli.py README.md tests/test_dashboard_cli.py
git commit -m "feat: marketsearch dashboard command

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification

```bash
.venv/Scripts/python.exe -m marketsearch.cli dashboard --no-open --out dashboard.html
```

Then confirm by hand in a browser:

| Check | Expected |
|---|---|
| Top Picks count | "3 matches from models in your config" — not 10, not padded |
| Slider at 6,000 | SVL90 · SVL95 · T770 (low hours wins) |
| Slider at 10,000 | order **reverses** — SVL95 first (price wins) |
| Grapples | absent from Top Picks, present in the browse list |
| Verdict chips | toggling `no_match` hides those cards |
| Unknowns | visible on cards where the model could not determine a field |

Add `dashboard.html` to `.gitignore` alongside the existing `preview.html` entry.

## Out of scope

The 718 prefiltered listings (filter tuning — `scripts/review.py`), any server or
auto-refresh, charts, and editing criteria from the page.
