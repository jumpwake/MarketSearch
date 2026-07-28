"""The results dashboard: judged listings in, one HTML page out.

Everything here is pure. The renderer never reads a file, a clock, or the
database — the CLI does that and passes the results in — which is what lets
every test run without a browser or a database.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass

from marketsearch.config import Config
from marketsearch.format import attribute_rows, dollars
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


def _value_label(price_cents: int | None, hours: int | None, life_hours: int) -> str:
    """Mirrors valueLabel() in _JS — keep the two in step.

    Missing hours and missing price are different failures and must read
    differently. `value_per_remaining_hour` collapses both into a single
    None, which is right for sorting but wrong for display: a listing with
    known hours and no price is not the same unknown as one with a price
    and no stated hours.
    """
    if hours is None:
        return "hours unknown"
    if price_cents is None:
        return "price unknown"
    value = value_per_remaining_hour(price_cents, hours, life_hours)
    assert value is not None  # both inputs present, per the checks above
    return f"${value:,.2f}/hr"


def _slider_bounds(life_hours: int) -> tuple[int, int]:
    """Slider min/max that always contain the supplied life_hours.

    `--life-hours` accepts any integer, but the slider previously had
    hardcoded bounds (3000-12000). A value outside that range rendered Top
    Picks at the CLI's life_hours while the browser silently clamped the
    slider on first use, desyncing the two numbers the moment the user
    touched it.
    """
    return min(3000, life_hours), max(12000, life_hours)


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

    value_text = _value_label(listing.price_cents, hours, life_hours)

    return f"""
<article class="card" data-listing-id="{_esc(listing.listing_id)}"
         data-verdict="{_esc(extraction.verdict)}"
         data-model="{_esc(listing.model_name)}"
         data-price-cents="{'' if listing.price_cents is None else listing.price_cents}"
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
            f'<li data-pick="{_esc(p.row.listing.listing_id)}"'
            f' data-hours="{"" if p.hours is None else p.hours}"'
            f' data-price-cents="{"" if p.row.listing.price_cents is None else p.row.listing.price_cents}">'
            f'<a href="{_esc(p.row.listing.url)}" target="_blank" rel="noopener">'
            f"{_esc(p.row.listing.title)}</a>"
            f'<span class="pick-value">'
            f"{_esc(_value_label(p.row.listing.price_cents, p.hours, life_hours))}"
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

    life_min, life_max = _slider_bounds(life_hours)

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
    <input id="life" type="range" min="{life_min}" max="{life_max}" step="500" value="{life_hours}">
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


_JS = """
const cards = [...document.querySelectorAll('.card')];
const picks = [...document.querySelectorAll('[data-pick]')];
const picksHost = document.querySelector('.picks');
const life = document.getElementById('life');
const lifeOut = document.getElementById('lifeOut');
const q = document.getElementById('q');
const model = document.getElementById('model');
const sort = document.getElementById('sort');
const off = new Set();

// Pure, DOM-free mirrors of value_per_remaining_hour / top_picks in
// dashboard.py. Kept free of document/card so both the browse cards and the
// Top Picks strip call the exact same ranking logic, and so a test runner
// (e.g. Node) can exercise them directly on plain {hours, priceCents}
// objects without a browser. `hours` and `priceCents` here are numbers or
// null — never the empty string the dataset attributes carry.
function computeValue(hours, priceCents, lifeHours) {
  if (hours === null || priceCents === null) return null;
  return (priceCents / 100) / Math.max(lifeHours - hours, 1);
}

function valueLabel(hours, priceCents, lifeHours) {
  // Missing hours and missing price are different failures and must read
  // differently — collapsing both into "hours unknown" contradicts a card
  // that plainly states its own hours.
  if (hours === null) return 'hours unknown';
  if (priceCents === null) return 'price unknown';
  const v = computeValue(hours, priceCents, lifeHours);
  return '$' + v.toLocaleString(undefined, {minimumFractionDigits: 2,
                                             maximumFractionDigits: 2}) + '/hr';
}

// Ascending dollars-per-remaining-hour; unknown value sorts last. Matches
// top_picks()'s sort key in dashboard.py exactly.
function compareByValue(a, b, lifeHours) {
  const av = computeValue(a.hours, a.priceCents, lifeHours);
  const bv = computeValue(b.hours, b.priceCents, lifeHours);
  if (av === null) return bv === null ? 0 : 1;
  if (bv === null) return -1;
  return av - bv;
}

function readNum(raw) {
  return raw === '' ? null : Number(raw);
}

function factsOf(el) {
  return {hours: readNum(el.dataset.hours), priceCents: readNum(el.dataset.priceCents)};
}

// Re-labels and re-sorts the Top Picks strip. Without this the strip kept
// the server-side order and server-side $/hr text no matter where the
// slider moved, contradicting the browse list it sits next to.
function applyPicks(lifeHours) {
  for (const li of picks) {
    const valueEl = li.querySelector('.pick-value');
    if (valueEl) {
      const {hours, priceCents} = factsOf(li);
      valueEl.textContent = valueLabel(hours, priceCents, lifeHours);
    }
  }
  if (!picksHost) return;
  const ranked = [...picks].sort((a, b) => compareByValue(factsOf(a), factsOf(b), lifeHours));
  for (const li of ranked) picksHost.appendChild(li);
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
    const chip = card.querySelector('.value-chip');
    if (chip) {
      const {hours, priceCents} = factsOf(card);
      chip.textContent = valueLabel(hours, priceCents, lifeHours);
    }
  }

  const key = sort.value;
  const shown = cards.filter(c => !c.hidden);
  shown.sort((a, b) => {
    if (key === 'value') return compareByValue(factsOf(a), factsOf(b), lifeHours);
    if (key === 'confidence') return b.dataset.confidence - a.dataset.confidence;
    if (key === 'price') {
      if (a.dataset.priceCents === '') return b.dataset.priceCents === '' ? 0 : 1;
      if (b.dataset.priceCents === '') return -1;
      return a.dataset.priceCents - b.dataset.priceCents;
    }
    if (key === 'hours') {
      if (a.dataset.hours === '') return b.dataset.hours === '' ? 0 : 1;
      if (b.dataset.hours === '') return -1;
      return a.dataset.hours - b.dataset.hours;
    }
    return b.dataset.seen.localeCompare(a.dataset.seen);
  });
  const host = document.getElementById('cards');
  for (const card of shown) host.appendChild(card);

  applyPicks(lifeHours);
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
