"""The results dashboard: judged listings in, one HTML page out.

Everything here is pure. The renderer never reads a file, a clock, or the
database — the CLI does that and passes the results in — which is what lets
every test run without a browser or a database.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass

from marketsearch.config import Config, ModelConfig
from marketsearch.format import attribute_rows, dollars, short_location
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

    Discarded listings are excluded for the stronger reason: the user has
    already looked at that machine and rejected it, which outranks any value
    ranking this function could compute.
    """
    picks: list[Pick] = []
    for row in rows:
        if row.extraction.verdict != "match":
            continue
        if row.listing.model_name not in live_models:
            continue
        if row.listing.dismissed:
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


def _models_by_name(config: Config) -> dict[str, ModelConfig]:
    return {m.name: m for w in config.watchlists for m in w.models}


def _spec_text(model: ModelConfig | None) -> str:
    """'92 hp · 10,900 lb', omitting whichever figure the config leaves out.

    Attachments have neither, so they render nothing rather than a stub.
    """
    if model is None:
        return ""
    parts = []
    if model.hp is not None:
        parts.append(f"{model.hp} hp")
    if model.weight_lb is not None:
        parts.append(f"{model.weight_lb:,} lb")
    return " · ".join(parts)


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

    # `data-dismissed` carries only what the database says. The page layers the
    # user's un-saved clicks on top of it client-side, so the two states stay
    # distinguishable — that difference is what the "make permanent" tray needs.
    discard_note = ""
    if listing.dismissed:
        discard_note = f'<span class="chip chip--discarded">discarded{_esc(": " + listing.dismiss_reason if listing.dismiss_reason else "")}</span>'

    return f"""
<article class="card" data-listing-id="{_esc(listing.listing_id)}"
         data-verdict="{_esc(extraction.verdict)}"
         data-model="{_esc(listing.model_name)}"
         data-price-cents="{'' if listing.price_cents is None else listing.price_cents}"
         data-hours="{'' if hours is None else hours}"
         data-confidence="{extraction.confidence}"
         data-dismissed="{'1' if listing.dismissed else ''}"
         data-seen="{_esc(listing.first_seen_at)}">
  {_photo(row)}
  <div class="body">
    <h3><a href="{_esc(listing.url)}" target="_blank" rel="noopener">{_esc(listing.title)}</a></h3>
    <p class="meta">
      <span class="badge badge--new" hidden>NEW</span>
      <span class="price">{_esc(dollars(listing.price_cents))}</span>
      <span class="badge badge--{_esc(extraction.verdict)}">{_esc(extraction.verdict)}</span>
      {discard_note}
      <span class="chip">{_esc(listing.model_name)}</span>
      <span class="chip">{_esc(listing.location)}</span>
      <span class="chip value-chip">{_esc(value_text)}</span>
      <span class="chip">confidence {extraction.confidence:.2f}</span>
    </p>
    {unknowns_html}
    <dl class="attrs">{rows_html}</dl>
    <blockquote>{_esc(extraction.reasoning)}</blockquote>
  </div>
  <button class="discard" type="button" title="Discard this listing">&#10005;</button>
</article>"""


def render_dashboard(
    rows: list[JudgedListing],
    config: Config,
    generated_at: str,
    life_hours: int = DEFAULT_LIFE_HOURS,
) -> str:
    """Judged listings and config in, one self-contained HTML page out."""
    live = _live_models(config)
    models = _models_by_name(config)
    picks = top_picks(rows, live, life_hours)

    if picks:
        noun = "match" if len(picks) == 1 else "matches"
        picks_head = f"{len(picks)} {noun} from models in your config"
        picks_html = "".join(
            f'<li data-pick="{_esc(p.row.listing.listing_id)}"'
            f' data-hours="{"" if p.hours is None else p.hours}"'
            f' data-seen="{_esc(p.row.listing.first_seen_at)}"'
            f' data-price-cents="{"" if p.row.listing.price_cents is None else p.row.listing.price_cents}">'
            f'<span class="pick-title">'
            f'<a href="{_esc(p.row.listing.url)}" target="_blank" rel="noopener">'
            f"{_esc(p.row.listing.title)}</a>"
            f'<span class="badge badge--new" hidden>NEW</span></span>'
            f'<span class="pick-value">'
            f"{_esc(_value_label(p.row.listing.price_cents, p.hours, life_hours))}"
            f"</span>"
            f'<span class="pick-spec">'
            f"{_esc(_spec_text(models.get(p.row.listing.model_name or '')))}"
            f"</span>"
            f'<span class="pick-facts">'
            f"{'—' if p.hours is None else format(p.hours, ',')} hrs · "
            f"{_esc(dollars(p.row.listing.price_cents))} · "
            f"{_esc(short_location(p.row.listing.location))}</span></li>"
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

    n_discarded = sum(1 for r in rows if r.listing.dismissed)
    discarded_note = (
        f'<span class="sub">{n_discarded} discarded and hidden</span>'
        if n_discarded else ""
    )

    life_min, life_max = _slider_bounds(life_hours)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MarketSearch results</title>
<style>{_CSS}</style></head>
<body data-generated="{_esc(generated_at)}">
<header>
  <h1>MarketSearch results</h1>
  <p class="sub">{len(rows)} judged · generated {_esc(generated_at)}<span id="newCount"></span></p>
</header>

<section class="criteria"><h2>Criteria</h2>{criteria_html}</section>

<section class="top">
  <h2>Top picks <span class="sub" id="picksHead">{_esc(picks_head)}</span></h2>
  <label class="life">Assumed usable life
    <input id="life" type="range" min="{life_min}" max="{life_max}" step="500" value="{life_hours}">
    <output id="lifeOut">{life_hours:,}</output> hrs
  </label>
  <p class="caveat">Ranked on hours and price only — blind to condition.
     Only matches your criteria already accepted appear here.</p>
  {picks_html}
</section>

<section class="browse">
  <h2>All judged listings{discarded_note}</h2>
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
    <button id="showDiscarded" class="toggle" type="button">show discarded</button>
  </div>
  <div id="tray" class="tray" hidden>
    <p class="tray-head"><span id="trayText"></span> — this browser remembers it,
       but the database does not yet. Run this to make it permanent:</p>
    <textarea id="trayCmd" class="tray-cmd" readonly rows="2"></textarea>
    <span class="tray-buttons">
      <button id="trayCopy" type="button">copy</button>
      <button id="trayClear" type="button">clear</button>
    </span>
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
let showDiscarded = false;

// ---- browser-side memory -----------------------------------------------
// Two things the page remembers between visits: which listings you have
// discarded but not yet written to the database, and when you last looked.
// Both are best-effort. localStorage is absent under the Node test harness
// and throws outright in a browser with site data blocked; either way the
// page must still work, just without memory.
const DISCARD_KEY = 'ms.discards';
const VIEWED_KEY = 'ms.lastViewed';

function readStored(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

function writeStored(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* no memory, no harm */ }
}

// What the database already knows, straight off the server-rendered markup.
const dbDiscarded = new Set();
for (const card of cards) {
  if (card.dataset.dismissed === '1') dbDiscarded.add(card.dataset.listingId);
}

// Clicks not yet written to the database, in both directions.
const toDiscard = new Set();
const toRestore = new Set();

// Reconcile against the database on every load: an id we were holding as
// "to discard" that now comes back marked discarded means the CLI command
// was run, so the reminder retires itself rather than nagging forever.
(function loadDiscards() {
  let saved = {};
  try { saved = JSON.parse(readStored(DISCARD_KEY) || '{}') || {}; } catch (e) { saved = {}; }
  for (const id of saved.discard || []) if (!dbDiscarded.has(id)) toDiscard.add(id);
  for (const id of saved.restore || []) if (dbDiscarded.has(id)) toRestore.add(id);
  saveDiscards();
})();

function saveDiscards() {
  writeStored(DISCARD_KEY,
              JSON.stringify({discard: [...toDiscard], restore: [...toRestore]}));
}

function isDiscarded(id) {
  if (toRestore.has(id)) return false;
  return dbDiscarded.has(id) || toDiscard.has(id);
}

function toggleDiscard(card) {
  const id = card.dataset.listingId;
  if (!id) return;
  if (isDiscarded(id)) {
    // Undoing an unsaved discard just drops it; undoing a saved one needs a
    // command of its own, so it moves to the restore list instead.
    if (toDiscard.has(id)) toDiscard.delete(id); else toRestore.add(id);
  } else {
    if (toRestore.has(id)) toRestore.delete(id); else toDiscard.add(id);
  }
  saveDiscards();
  apply();
}

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
    // top_picks() already dropped everything the database has discarded; this
    // catches the ones discarded in this browser since the page was built.
    li.hidden = isDiscarded(li.dataset.pick);
    const valueEl = li.querySelector('.pick-value');
    if (valueEl) {
      const {hours, priceCents} = factsOf(li);
      valueEl.textContent = valueLabel(hours, priceCents, lifeHours);
    }
  }
  // Discarding a pick in the browser has to move the count with it, or the
  // heading goes on advertising matches that are no longer on screen.
  const remaining = picks.filter(li => !li.hidden).length;
  const head = document.getElementById('picksHead');
  if (head) {
    // Mirrors picks_head in render_dashboard — keep the two in step.
    head.textContent = remaining === 0
      ? 'no matches from models in your config'
      : remaining + (remaining === 1 ? ' match' : ' matches') + ' from models in your config';
  }
  if (!picksHost) return;
  picksHost.hidden = remaining === 0;
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
    const discarded = isDiscarded(card.dataset.listingId);
    // An attribute rather than a class: the shim's classList has no remove(),
    // and this has to be able to go back off when a discard is undone.
    card.dataset.discarded = discarded ? '1' : '';
    card.hidden = !(hit && modelOk && verdictOk && (showDiscarded || !discarded));
    const btn = card.querySelector('.discard');
    if (btn) {
      btn.textContent = discarded ? '\\u21A9' : '\\u2715';
      btn.title = discarded ? 'Restore this listing' : 'Discard this listing';
    }
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
  updateTray();
}

// ---- the "make it permanent" tray --------------------------------------
// The page is a file:// document with no server behind it, so a click here
// cannot write to marketsearch.db. It hides the listing immediately and hands
// back the exact command that makes the same decision stick in the database,
// where the pipeline and the notifier can both honour it.
const tray = document.getElementById('tray');
const trayText = document.getElementById('trayText');
const trayCmd = document.getElementById('trayCmd');

function updateTray() {
  if (!tray) return;
  const lines = [];
  if (toDiscard.size) lines.push('marketsearch dismiss ' + [...toDiscard].join(' '));
  if (toRestore.size) lines.push('marketsearch dismiss --undo ' + [...toRestore].join(' '));
  tray.hidden = lines.length === 0;
  if (!lines.length) return;
  const parts = [];
  if (toDiscard.size) parts.push(toDiscard.size + ' discarded');
  if (toRestore.size) parts.push(toRestore.size + ' restored');
  if (trayText) trayText.textContent = parts.join(', ');
  if (trayCmd) trayCmd.value = lines.join('\\n');
}

// ---- new since you last looked ------------------------------------------
function markNew() {
  const generated = (document.body && document.body.dataset.generated) || '';
  const last = readStored(VIEWED_KEY);
  let count = 0;
  // first_seen_at and the generated stamp are both UTC ISO-8601 carrying the
  // same +00:00 offset, so comparing them as strings compares them in time.
  if (last) {
    for (const card of cards) {
      if (card.dataset.seen > last) { showNewBadge(card); count++; }
    }
    for (const li of picks) {
      if (li.dataset.seen > last) showNewBadge(li);
    }
  }
  const out = document.getElementById('newCount');
  if (out) out.textContent = count ? ' \\u00b7 ' + count + ' new since you last looked' : '';
  // Stamped with the page's own generated time, not the clock: a listing found
  // after this page was built is genuinely still unseen and has to stay NEW on
  // the next build. On a first visit there is no watermark and nothing is
  // badged — everything being NEW is the same as nothing being NEW.
  if (generated) writeStored(VIEWED_KEY, generated);
}

function showNewBadge(el) {
  const badge = el.querySelector('.badge--new');
  if (badge) badge.hidden = false;
}

for (const el of [q, model, sort, life]) el.addEventListener('input', apply);
for (const b of document.querySelectorAll('.v')) {
  b.addEventListener('click', () => {
    b.classList.toggle('on');
    if (off.has(b.dataset.v)) off.delete(b.dataset.v); else off.add(b.dataset.v);
    apply();
  });
}

for (const card of cards) {
  const btn = card.querySelector('.discard');
  if (btn) btn.addEventListener('click', () => toggleDiscard(card));
}

const showDiscardedBtn = document.getElementById('showDiscarded');
if (showDiscardedBtn) {
  showDiscardedBtn.addEventListener('click', () => {
    showDiscarded = !showDiscarded;
    showDiscardedBtn.classList.toggle('on');
    apply();
  });
}

const trayCopy = document.getElementById('trayCopy');
if (trayCopy) {
  trayCopy.addEventListener('click', () => {
    if (!trayCmd) return;
    // navigator.clipboard needs a secure context, which a file:// page is not
    // guaranteed to be. Selecting the textarea first means that even if every
    // copy path fails, the command is sitting there highlighted for a manual
    // Ctrl+C rather than lost.
    trayCmd.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (e) { copied = false; }
    if (copied) { trayCopy.textContent = 'copied'; return; }
    try {
      navigator.clipboard.writeText(trayCmd.value).then(
        () => { trayCopy.textContent = 'copied'; },
        () => { trayCopy.textContent = 'press Ctrl+C'; });
    } catch (e) { trayCopy.textContent = 'press Ctrl+C'; }
  });
}

const trayClear = document.getElementById('trayClear');
if (trayClear) {
  trayClear.addEventListener('click', () => {
    toDiscard.clear();
    toRestore.clear();
    saveDiscards();
    apply();
  });
}

markNew();
apply();
"""


_CSS = """
:root {
  --bg:#f4f4f2; --fg:#1c1e21; --muted:#5b6472; --line:#e0e2e6; --card:#fff;
  --match:#0f7b4f; --unver:#8a5a00; --nomatch:#7c828c; --accent:#2c5aa0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#141619; --fg:#e6e8eb; --muted:#a3aab5; --line:#2c3037; --card:#1e2126;
    --match:#4ade80; --unver:#fbbf24; --nomatch:#8a8f98; --accent:#8fb3f5;
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
/* A disclosure control, not a hyperlink — it should not read as blue body text. */
.criteria summary {
  cursor:pointer; color:var(--fg); font-weight:500;
  padding:2px 0; user-select:none;
}
.criteria summary:hover { color:var(--accent) }
.life { display:flex; gap:8px; align-items:center; font-size:13px; margin-bottom:6px }
.life input { flex:0 1 240px }
/* Top picks is a ranked table, not a bullet list: it sits on the same card
   surface as everything else, and the rank numbers come from a CSS counter so
   the markup stays a plain <ol>. */
.picks {
  margin:14px 0 0; padding:0; list-style:none; counter-reset:pick;
  background:var(--card); border:1px solid var(--line); border-radius:12px;
  overflow:hidden;
}
.picks li {
  counter-increment:pick; margin:0; padding:11px 16px;
  display:grid; grid-template-columns:1.4em 1fr auto auto auto; gap:14px;
  align-items:baseline; border-top:1px solid var(--line);
}
/* `display:grid` above beats the user-agent [hidden] rule, exactly as it does
   for .card — without this a pick discarded in the browser stays on screen. */
.picks li[hidden] { display:none }
.picks li:first-child { border-top:none }
.pick-title { display:flex; gap:8px; align-items:baseline; min-width:0 }
.pick-title a { overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.picks li:hover { background:color-mix(in srgb, var(--accent) 6%, transparent) }
.picks li::before {
  content:counter(pick); color:var(--muted); font-size:12px;
  font-variant-numeric:tabular-nums; text-align:right;
}
/* Without this the links fall through to browser-default blue, and go purple
   once visited — the one place on the page that was not styled. */
.picks li a { color:var(--fg); text-decoration:none; font-weight:500 }
.picks li a:hover { color:var(--accent); text-decoration:underline }
/* Tabular figures so the $/hr column lines up and can be scanned vertically. */
.pick-value {
  font-weight:600; text-align:right; white-space:nowrap;
  font-variant-numeric:tabular-nums;
}
.pick-spec {
  color:var(--muted); font-size:13px; white-space:nowrap;
  font-variant-numeric:tabular-nums;
}
.pick-facts {
  color:var(--muted); font-size:13px; white-space:nowrap;
  font-variant-numeric:tabular-nums;
}
@media (max-width:640px) {
  .picks li { grid-template-columns:1.4em 1fr auto; row-gap:2px }
  .pick-spec, .pick-facts { grid-column:2 / -1; text-align:left }
}
.controls { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px }
.controls input, .controls select, .v, .toggle {
  padding:6px 10px; border:1px solid var(--line); border-radius:8px;
  background:var(--card); color:var(--fg); font:inherit; font-size:13px;
}
.controls input[type=search] { flex:1 1 220px }
.v, .toggle { cursor:pointer; opacity:.4 }
.v.on, .toggle.on { opacity:1; border-color:var(--accent) }
/* The bridge between a click here and the database. Deliberately loud: an
   unsaved discard is a decision the rest of the system cannot see yet. */
.tray {
  display:flex; flex-wrap:wrap; gap:8px 12px; align-items:center;
  margin-bottom:14px; padding:10px 12px; border-radius:10px;
  border:1px solid var(--accent); background:var(--card);
}
.tray[hidden] { display:none }
.tray-head { margin:0; flex:1 1 100%; font-size:13px; color:var(--muted) }
.tray-head #trayText { color:var(--fg); font-weight:600 }
.tray-cmd {
  flex:1 1 320px; resize:vertical; padding:6px 8px; border-radius:8px;
  border:1px solid var(--line); background:var(--bg); color:var(--fg);
  font:12px/1.5 ui-monospace, Consolas, monospace; white-space:pre;
}
.tray-buttons { display:flex; gap:8px }
.tray button {
  padding:6px 12px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); font:inherit; font-size:13px;
  cursor:pointer;
}
.tray button:hover { border-color:var(--accent) }
#cards { display:grid; gap:14px }
.card {
  display:flex; gap:14px; background:var(--card); border:1px solid var(--line);
  border-radius:12px; padding:14px; position:relative;
}
.card[hidden] { display:none }
/* Only visible at all while "show discarded" is on, so it has to read as
   struck-off at a glance rather than merely a little dimmer. */
.card[data-discarded="1"] { opacity:.5; border-style:dashed }
.discard {
  position:absolute; top:8px; right:8px; width:26px; height:26px; padding:0;
  border:1px solid transparent; border-radius:8px; background:transparent;
  color:var(--muted); font:inherit; font-size:13px; line-height:1;
  cursor:pointer; opacity:0; transition:opacity .12s;
}
.card:hover .discard, .discard:focus { opacity:1 }
.discard:hover { border-color:var(--line); color:var(--fg); background:var(--bg) }
/* Keyboard and touch users get no hover, so never hide it from them outright. */
@media (hover:none) { .discard { opacity:.6 } }
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
.badge--new { background:var(--accent) }
.badge[hidden] { display:none }
.chip--discarded { border-color:var(--nomatch); color:var(--nomatch) }
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
