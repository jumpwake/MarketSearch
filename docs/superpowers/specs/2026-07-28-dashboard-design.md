# Results Dashboard — Design

**Date:** 2026-07-28
**Status:** Approved, ready for implementation planning

## Purpose

`marketsearch dashboard` renders a self-contained HTML page from the database
and opens it, for browsing every listing the tool has judged and tuning the
criteria that judged them.

The existing `preview` command answers "what would this run have emailed?" — one
run, email-safe markup, 680px single column. This answers a different question:
"across everything judged so far, is the model reading these listings the way I
would?" That question needs the criteria, the verdict, the reasoning, and the
extracted attributes on one screen, which an email cannot do.

## Scope

The corpus is the **32 listings that carry an extraction** — 9 `match`,
9 `no_match`, 14 `unverifiable`. The 718 `prefiltered_out` rows never reached
the model and are a *filter*-tuning problem, already served by
`scripts/review.py`. They are out of scope here.

## Top Picks

A ranked strip at the top of the page, above the browse list.

### What qualifies

Only `match` verdicts, and only from models present in the current
`config.yaml`. Retired models are excluded: 6 of the 9 current matches are
`root-grapple`, a search the user has disabled, and presenting them as things to
go buy would be wrong. They remain visible in the browse list below.

Today that yields **3 machines**, not 10. The section states the real count
rather than padding to a round number.

### Ranking: dollars per remaining hour

`price / max(assumed_life_hours - engine_hours, 1)`.

Interpretable in real units, and stable as the database grows — unlike a
normalized composite score, whose values shift whenever the corpus changes and
mean nothing on their own.

Ranking by extraction `confidence` was considered and **rejected**: confidence
measures the model's certainty, not deal quality. Sorted that way the real data
puts a $980 tree grabber above a 2,200-hour, high-flow, two-speed T770.

### The assumed-life control is the point, not a detail

Measured against the three qualifying machines, the life assumption *inverts*
the ranking:

| assumed life | 1st | 2nd | 3rd |
|---|---|---|---|
| 6,000 hrs | SVL90 · 1,005 hrs · $45,000 · $9.01/hr | T770 · $10.39/hr | SVL95 · $10.61/hr |
| 10,000 hrs | SVL95 · 2,984 hrs · $32,000 · $4.56/hr | SVL90 · $5.00/hr | T770 · $5.06/hr |

Same machines, same data, opposite advice. At a short assumed life remaining
hours are scarce and low hours dominate; at a long one everyone has hours left
and price dominates.

A hardcoded figure would therefore present the author's guess as analysis. So
the assumption is **exposed as a control** in the section header, defaulting to
**6,000 hours**, re-sorting the list live. The user sees the sensitivity instead
of being misled by it — and learns something true about their own shortlist:
the SVL95 is the value play only if these machines run past ~7,000 hours.

The default is 6,000 because compact track loaders work harder than skid steers,
and major undercarriage and final-drive costs typically land well before 10,000.

### Card contents and honesty requirements

Each card shows its own arithmetic — `$10.39/hr · 2,200 hrs · $39,500` — so the
ranking is auditable rather than a black box.

- Listings with **unknown `engine_hours` sort last**, labelled as such. They are
  never treated as zero hours, which would rank them first.
- A machine whose `engine_hours` exceeds the assumed life clamps its remaining
  hours to 1 rather than dividing by zero or going negative.
- The section carries a standing caveat: **the metric is blind to condition.**
  It ranks on hours and price only. Only `match` verdicts qualify, so the
  criteria have already screened stated major failures, but a tired
  undercarriage with good numbers will still rank well.

## Browse list

Every judged listing, including retired models.

Each card: photo, title linking to Marketplace, price, location, model badge,
verdict badge, confidence, the reasoning quote, and the extracted attribute grid
(hours, specs, condition, seller type).

**`unknowns` are called out visually.** This is the highest-value signal for
criteria tuning and is currently invisible: `engine_hours` is unknown on 25 of
32 judged listings, and `quick_attach_plate` on 6. A field the model routinely
cannot determine is a prompt problem, not a listing problem.

Controls, all client-side: verdict chips, model dropdown, text search, and sort
by value / confidence / price / hours / newest.

Sorting by **value** here uses the same dollars-per-remaining-hour metric and
reads the *same* assumed-life control as Top Picks — there is one such control
on the page, not two, and moving it re-sorts both sections together. Listings
with unknown hours sort last under that option, as in Top Picks.

## Criteria panel

The watchlist's `criteria` text, pinned collapsible at the top. Reading the
criteria and the verdicts on one screen is the tuning loop; today it requires
holding `config.yaml` in one window and output in another.

## Structure

| Unit | Responsibility |
|---|---|
| `src/marketsearch/dashboard.py` | Rows + config → HTML string. Pure function, no I/O. |
| `src/marketsearch/cli.py` | `dashboard` command: load, render, write, open browser. |

Command signature, mirroring the existing `preview`:

```
marketsearch dashboard [--config PATH] [--db PATH] [--out dashboard.html]
                       [--since 365d] [--open/--no-open]
```

`--since` defaults to `365d` rather than `preview`'s tighter window: this page
exists to browse everything judged, so the default should not silently hide
history. It is parsed by the existing `shakedown.parse_since`.

**No new store method.** `Store.listings_with_details(None, cutoff)` already
returns `list[tuple[ListingRow, ListingDetail, ExtractionRow | None]]` — the
replay corpus. The dashboard filters to rows whose extraction is not `None`.

Purity is what makes this testable: the renderer takes data and returns a
string, so every test runs without a browser, a database, or a network.

### Interaction

All cards render server-side with their values in `data-*` attributes; the
client script only shows, hides, and reorders existing nodes. No data is
duplicated into a JSON blob, and no build step, framework, or CDN asset is
involved — a strict-CSP-safe single file.

### Escaping is a correctness requirement

Titles come from Facebook and reasoning comes from an LLM. Both are untrusted.
Every interpolated value is HTML-escaped, and a listing titled `<script>` must
render as text. This gets an explicit test.

### Photos

Cards use the stored remote `thumbnail_url`. Facebook CDN URLs expire, so older
listings will show a broken image; cards degrade to a labelled placeholder
rather than a broken-image icon.

Downloading and base64-embedding photos on every regeneration was rejected: it
makes the file multi-megabyte and re-fetches Facebook every time the user looks
at their own stored data.

## Testing

Every test exercises the pure renderer with constructed rows:

- A `<script>` in a title and in reasoning renders escaped.
- Top Picks excludes non-`match` verdicts and models absent from config.
- Ranking order is correct at a given assumed life, and **flips** when the
  assumed life changes — the inversion above, pinned as a test.
- Unknown `engine_hours` sorts last and is labelled, not scored as zero.
- `engine_hours` above the assumed life does not divide by zero.
- A missing `thumbnail_url` renders the placeholder.
- An empty corpus renders a valid page saying so, not a crash.

## Out of scope

- The 718 prefiltered listings (filter tuning — `scripts/review.py`).
- Any server, live reload, or auto-refresh. The page is regenerated by re-running
  the command.
- Charts. The value is in reading listings, not aggregate plots.
- Editing criteria from the page. It reads; `config.yaml` remains the source of
  truth.
