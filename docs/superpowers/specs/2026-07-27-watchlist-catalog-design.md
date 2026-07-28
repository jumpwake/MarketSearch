# Watchlist Catalog — Design

**Date:** 2026-07-27
**Status:** Approved, ready for implementation planning
**Supersedes:** the per-search `title_must_match` model in
[`2026-07-26-marketsearch-design.md`](2026-07-26-marketsearch-design.md)

## Purpose

Today each configured search owns its own model keywords, price band, criteria,
and junk list. That coupling throws away machines the tool has already paid to
scrape, and it does so silently.

This design pools the model keywords into a shared catalog per watchlist, so
every query's results are checked against every model the user cares about.
Queries become pure discovery — fishing rods, not filters.

## The problem

Two defects, discovered while investigating a 2019 Bobcat T770 at $39,000 in
Washington, IL that the tool never reported.

### Facebook's fuzzy matching returns other models

A query for "Bobcat T86" returns T770s, T750s, and S-series wheeled machines.
Measured across the 757-listing database as of 2026-07-27, only ~4% of listings
harvested by a search matched that search's own model tokens. The other 96% are
discarded — including machines that match a *different* configured model.

### Whichever search sees a listing first owns it forever

`Store.known_listing_ids` selects by `listing_id` with no `search_name` filter,
and `upsert_listing`'s `ON CONFLICT` clause never updates `search_name`. So the
first search to reach a listing claims it and applies its own filters. If those
filters reject it, the row is written `prefiltered_out` and every later search
drops it at the dedupe step, before any filter runs.

Listing `1917658885567966` — "2019 Bobcat T770", Cameron MO, $42,000 — is a
concrete case. The `bobcat-t770` search was truncated at 120 results and never
saw it; the `bobcat-t86` search ran two minutes later, did see it, and rejected
it with `"title matched none of: 't86', 't-86', 't 86'"`. The price is inside
the T770 band and the title contains `t770`. It would have matched.

The two defects compound: truncation decides *which* search gets there first,
so the ownership assignment is effectively arbitrary.

Nothing recovers these. `pending_listings` only rescues `stage='pending'`, and
a relist under a new `listing_id` is caught by `fingerprint_seen_before` as a
repost within 60 days.

### Measured impact

Simulating a pooled catalog against the stored listings:

| | listings |
|---|---|
| harvested, non-attachment | 721 |
| survive all gates today (own search only) | 13 |
| survive all gates with pooled catalog | **19** |

A 1.5× lift from identical scraping volume, recovering two T770s, two T750s, a
T76, and a $49,500 John Deere 333G. This is strictly better than buying the same
coverage by scrolling deeper, which costs proportionally more Facebook exposure.

## Design

### Configuration

`searches` is replaced by `watchlists`. Each watchlist owns the criteria, the
junk list, and a catalog of models; queries are listed separately and carry no
filtering behaviour.

```yaml
watchlists:
  - name: track-loaders
    criteria: *standard_criteria
    exclude: *junk_terms
    on_unknown: alert
    queries:
      - "Bobcat T770"
      - "Bobcat T750"
      - "Takeuchi TL12 track loader"
      # ...one line each
    models:
      - {name: bobcat-t770, keywords: ["t770", "t-770", "t 770"], price: {min: 15000, max: 53000}}
      - {name: bobcat-t750, keywords: ["t750", "t-750", "t 750"], price: {min: 15000, max: 50000}}
      # ...

  - name: attachments
    criteria: *grapple_criteria
    exclude: ["mini", "wanted", "looking for", "tractor bucket"]
    queries: ["skid steer root grapple"]
    models:
      - {name: root-grapple, keywords: ["grapple"], price: {min: 800, max: 6000}}
```

`criteria` and `exclude` collapse from 15 repetitions to 2. Adding a query is one
line and immediately benefits every model in the watchlist.

### Filter sequence

Run every watchlist's queries and pool all results. Then offer each listing to
each watchlist in config order, and for the watchlist under consideration —

1. **Exclude** — watchlist `exclude` terms, whole-word match. Unchanged
   semantics from today's `title_must_not_match`.
2. **Identify** — find the first model whose keywords appear in the title
   (substring match, as today: `299d` must keep matching `299d3xe`).
3. **Price** — apply the *identified model's* band, not a watchlist-wide one.
   A missing price is still not a rejection.

Failing any of the three means this watchlist declines the listing, and it is
offered to the next one. A listing declined by every watchlist is dropped with
reason `"matched no watched model"`. A listing accepted by a watchlist is then —

4. **Deduped** — by `listing_id`. Now correct: every query applies identical
   filters, so there is no disagreement left to arbitrate.
5. **Extracted** — against the accepting watchlist's criteria.

Step 2 before step 3 is load-bearing: the price band is a property of the model,
so the model must be known first. This is also why pooling cannot be done by
concatenating every search's `title_must_match` into one flat list.

The rejection reason improves as a side effect. Today a T770 rejected by the T86
search reports `"title matched none of: 't86'..."`, which reads as a tool bug.
It becomes `"matched no watched model"`, which is true.

### Data model

`listings.search_name` currently means "which search claimed this row". Under
pooling the meaningful label is the model, determined by the title rather than by
which query happened to surface it.

Replace it with two columns:

| column | meaning |
|---|---|
| `watchlist_name` | which watchlist's criteria judged it |
| `model_name` | which model's keywords it matched (null if none) |

Migrated in place rather than rebuilt — the database holds ~30 real extractions
that cost money to produce. Existing rows map `search_name` → `model_name`, with
`watchlist_name` derived as `attachments` for `root-grapple` and `track-loaders`
otherwise. Rows whose `search_name` names a search no longer in `config.yaml`
(`bobcat-t300`, `newholland-c`) keep their `model_name` and are re-evaluated by
`requeue` like any other.

Consumers to update: `store.pending_listings`, `store.listings_with_details`,
`shakedown._search_by_name` (must now resolve model → watchlist to find criteria),
and `notify/render.py:240`, which groups alerts by search name and should group
by model.

### Requeue

A one-time offline pass is needed, because reclaiming a stranded listing during a
normal run requires it to reappear in live results — and with the 120-result
truncation, several will not.

`marketsearch requeue` re-tests every stored `prefiltered_out` row against the
current catalog using the database only, with no scraping, and resets the ones
that now pass to `pending` so the next run extracts them. This is worth keeping
permanently: it is the correct response to any edit of `config.yaml`, which
currently has no way to rescue previously-rejected listings.

### Cross-watchlist overlap

Pooling eliminates disagreement *within* a watchlist but not *between* them. A
title like "Root grapple for Bobcat T770" matches `t770` in track-loaders and
`grapple` in attachments — a 2-way conflict, versus today's 15-way one.

**Resolution: fall-through in config order.** A listing is offered to each
watchlist in turn and assigned to the first one that accepts it outright —
passing that watchlist's exclude terms, matching one of its models, and falling
inside that model's price band. It is rejected only when *no* watchlist accepts
it. This is the same invariant applied at the watchlist level that pooling
applies within one: a rejection by one filter never ends the listing's life while
another filter would take it.

The price band participating in the fall-through decision is what makes this
work, and is why a simple "machines first, attachments second" precedence rule is
not sufficient:

| title | price | outcome |
|---|---|---|
| "2019 Bobcat T770 with root grapple" | $42,000 | track-loaders: `t770` matches, in band → **machine** |
| "Root grapple for Bobcat T770" | $3,000 | track-loaders: `t770` matches, below $15,000 min → fall through. attachments: `grapple` matches, in band → **grapple** |
| "Eterra skidsteer Grapple" | $5,495 | track-loaders: no model matches → fall through → **grapple** |

Under bare precedence the middle row would be identified as a track loader,
rejected on price, and lost. Under fall-through it lands correctly.

No `"grapple"` junk term is added. Adding one was considered and rejected: it
would discard a machine sold with the attachment, which is a listing the user
actively wants.

A listing accepted by two watchlists is assigned to the first in config order.
With the current configuration this cannot arise — the price bands are disjoint
($15,000–$53,000 versus $800–$6,000) — so config order is a tie-break that is
never exercised, not a behaviour to depend on.

## Testing

- Prefilter is already pure functions over `RawListing` + config; the new
  identify-then-price sequence is tested the same way, no browser involved.
- A regression test built from listing `1917658885567966`: a T770 surfaced by
  the T86 query is kept and attributed to `model_name='bobcat-t770'`.
- A dedupe test asserting that a listing rejected by one query is still
  evaluated when another query in the same watchlist surfaces it.
- A migration test: a database written in the old shape opens, migrates, and
  preserves its extractions.
- `requeue` tested against a fixture database with no source attached, proving
  it performs no scraping.

## Consequences

**Scroll depth is deprioritised.** The pooled catalog extracts materially more
value from listings already being pulled, at no additional exposure. The earlier
question of per-search scroll depth remains open but is now a smaller lever, and
should be re-measured after this ships rather than designed against today's
numbers.

**Extraction budget pressure rises.** More listings survive the gates, so
`max_extractions_per_run: 40` will be hit more often, especially on the first
runs after `requeue`. Overflow already sets `stage='pending'` and drains over
subsequent runs via `pending_listings`, so this needs no new mechanism — but the
first few runs after this ships will be slower and more expensive than steady
state.

**Recency-sorted search is retained.** Relevance sorting was considered and
rejected earlier in this investigation: it would reach further back but forfeits
the guarantee that every newly-listed machine is seen.

## Out of scope

- The 120-result truncation itself (`max_listings_per_search`, and the
  `_MAX_SCROLLS = 15` ceiling that caps every search at ~384 listings
  regardless). Documented, not fixed here.
- Per-model criteria. All track loaders share `*standard_criteria`; nothing
  currently needs otherwise.
- Multi-watchlist alerting for one listing. Fall-through assigns each listing to
  exactly one watchlist, which is sufficient while the price bands stay disjoint.
