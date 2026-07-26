# MarketSearch — Design

**Date:** 2026-07-26
**Status:** Approved, ready for implementation planning

## Purpose

Automatically watch Facebook Marketplace for specific pieces of heavy equipment (Bobcat T300, T770, and similar), evaluate each new listing against criteria that go beyond price and keywords — engine hours, machine specs, stated condition — and alert by email and SMS only when something genuinely matches. Track listings the user has saved on Marketplace and report changes to them.

The problem it solves is noise. Marketplace's own alerts fire on every listing matching a keyword, which for "Bobcat T770" means constant notifications for machines with 6,000 hours, wrong-model parts listings, and wanted-ads. MarketSearch only interrupts when a machine is worth looking at.

## Scope note

Automated access to Facebook Marketplace violates Facebook's terms of service. There is no public Marketplace API. This is a personal-use tool operating at low volume against a residential IP; the realistic worst case is the Facebook account being checkpointed or restricted. The user has chosen to accept that risk on their primary account (see Decisions).

## Runtime environment

- A dedicated, always-on **Windows 11** machine on a residential connection (not the user's development machine).
- Invoked by **Windows Task Scheduler** on a jittered 30–60 minute interval during waking hours; idle overnight.
- Each invocation is a **short-lived process** that performs a full sweep and exits. There is no daemon and no listening service. All state lives in SQLite.

That last property is deliberate: a crash cannot leave a zombie process, and "is it working?" is answered by reading a log file rather than inspecting a running service.

## Architecture

### Run sequence

```
config.yaml ──► for each search:
                  1. SEARCH      source.search(query)     ──► [RawListing]
                  2. DEDUPE      drop ids already in SQLite ──► new only
                  3. PREFILTER   price band, title must/must-not words
                  4. DETAIL      source.fetch_detail(id) for survivors
                  5. EXTRACT     Claude ──► attributes + verdict + reasoning
                  6. RECORD      write every listing + verdict to SQLite

            ──► saved items:
                  7. SYNC        source.fetch_saved() ──► watched listing ids
                  8. RECHECK     re-fetch each watched listing, diff vs stored
                                 (extract on first sight to establish a baseline)

            ──► 9. NOTIFY        one batched email + one SMS ping, if anything to say
```

The ordering is load-bearing. Dedupe before prefilter means a listing is examined once in its life. Prefilter before detail means no detail page — the most bot-visible action — is loaded for a machine outside the price band. Extraction runs only on listings that already cleared the cheap gates. On a typical run every search yields zero new listings and the sweep costs nothing.

### Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `config` | Load and validate YAML into typed objects | — |
| `sources/base` | `RawListing`, `ListingDetail`, `ListingSource` interface | — |
| `sources/facebook` | The only module that knows Facebook exists | Playwright |
| `store` | SQLite: listings, extractions, notifications, runs | — |
| `prefilter` | Pure functions: listing + criteria → keep/drop + reason | `config` |
| `extract` | Listing detail + criteria → attributes + verdict | Anthropic SDK |
| `notify` | Verdicts and changes → email and SMS | SMTP, Twilio |
| `pipeline` | Wires the above in order | all |
| `cli` | `run`, `login`, `test-search`, `history`, `preview`, `replay` | `pipeline` |

The critical boundary is `sources/facebook`: it is the only file that knows what a Facebook page looks like. Everything downstream speaks `RawListing` and `ListingDetail`. When Facebook changes its markup, one file changes, and every test for prefilter, extraction, storage, and email keeps passing because none of them touch a browser.

## Listing acquisition

**Approach: drive a real logged-in Chrome via Playwright**, using a *persistent* browser profile stored on the Windows box. The user logs into Facebook by hand once; the session cookie survives across runs.

Parsing reads the **JSON payload Facebook embeds in the page's `<script>` tags** rather than matching CSS selectors. Facebook rotates class names constantly but keeps the payload shape comparatively stable, so this is materially more durable.

Costs of this approach, accepted knowingly: it breaks when Facebook restructures its payload (expect maintenance every few months), and security checkpoints occasionally need clearing by hand.

This sits behind a narrow `ListingSource` interface with three operations — `search(query)`, `fetch_detail(id)`, `fetch_saved()` — so the fragile part is quarantined in one small module. Switching to a paid scraping service later (Apify, Bright Data) means writing one new class, not a rewrite. A fallback that automatically switches to a paid service on block was considered and rejected as unnecessary complexity for a problem that may never arise.

## Geography and cadence

- **One anchor location, wide radius.** A single anchor city with a large radius (configurable, up to Facebook's ~500 mile ceiling). One search per item per run — fastest runs, least chance of tripping rate limits.
- **Jittered 30–60 minute interval** during waking hours, idle overnight. Fast enough to reach a fresh listing before most buyers, human-shaped enough to stay unremarkable.

## Configuration

A single `config.yaml` beside the database. Criteria are written in plain English and evaluated by Claude, so a new requirement is a sentence rather than a code change.

```yaml
account:
  profile_dir: C:\MarketSearch\chrome-profile   # persistent Chrome profile

location:
  anchor: "Kansas City, MO"     # EXAMPLE — set to the user's actual anchor city.
                                # Resolved to a Facebook location id once, then cached.
  radius_miles: 250

schedule:
  active_hours: "07:00-22:00"   # informational; real cadence set in Task Scheduler

extraction:
  model: claude-opus-5
  effort: low
  max_extractions_per_run: 25   # safety valve against a runaway run

notifications:
  enabled: false                # master switch — ships false; see Shakedown and rollout
  email:
    to: kbowsher@gmail.com
    from: marketsearch@example.com
    smtp_host: smtp.gmail.com
    smtp_port: 587
    username: marketsearch@example.com
    password_env: MARKETSEARCH_SMTP_PASSWORD
  sms:
    to: "+1XXXXXXXXXX"
    twilio_from: "+1XXXXXXXXXX"
    account_sid_env: TWILIO_ACCOUNT_SID
    auth_token_env: TWILIO_AUTH_TOKEN

searches:
  - name: bobcat-t770
    query: "Bobcat T770"
    price: { min: 15000, max: 60000 }
    title_must_match:     ["t770"]
    title_must_not_match: ["wanted", "parts only", "looking for", "rental"]
    on_unknown: alert            # alert | skip
    criteria: |
      Under 3000 engine hours.
      2-speed required. Enclosed cab with A/C strongly preferred.
      Reject anything describing major engine or hydraulic problems,
      or an undercarriage described as worn out or needing replacement.

  - name: bobcat-t300
    query: "Bobcat T300"
    price: { min: 10000, max: 40000 }
    title_must_match:     ["t300"]
    title_must_not_match: ["wanted", "parts only", "looking for"]
    on_unknown: alert
    criteria: |
      Under 3000 engine hours. Must run and drive.
      Prefer enclosed cab. Reject listings mentioning blown engines,
      seized hydraulics, or missing major components.
```

**Secrets never appear in this file.** It holds env-var *names*; the values live in a gitignored `.env` alongside it (SMTP app password, Twilio token, Anthropic API key). The config is therefore safe to commit and diff.

## Matching

### Prefilter (free, deterministic)

Price band and title must-match / must-not-match substrings, case-insensitive. This kills wanted-ads, parts listings, and out-of-budget machines before any detail page is fetched.

### Extraction (one Claude call per survivor)

`extraction.model` defaults to `claude-opus-5` at `effort: low`, roughly two cents per listing examined. At expected volume — a handful of genuinely new listings per day — that is a few dollars a month, and it buys reliable judgment on vague seller prose. `claude-haiku-4-5` is a supported alternative at roughly a third of a cent per listing, changeable in config with no code change.

Output is constrained by a JSON schema via `output_config.format` and parsed into a Pydantic model, so a malformed response is a retry rather than a crash.

Extracted fields:

- **Core:** `year`, `make_model`, `engine_hours`, `asking_price`, `location`
- **Machine specs:** `cab_enclosed`, `has_ac`, `two_speed`, `high_flow`, `tracks_or_tires`, `undercarriage_condition`, `aux_hydraulics`
- **Condition and history:** `runs`, `stated_issues[]`, `recent_service[]`, `damage_notes`, `one_owner_claim`
- **Deal context:** `attachments[]`, `seller_type` (private/dealer), `financing_or_trade`, `price_vs_market_note`
- **Verdict:** `verdict` (`match` | `no_match` | `unverifiable`), `confidence`, `reasoning`, `unknowns[]`

Any field the listing does not state is `null`. The model is instructed not to guess.

Distance is **not** extracted by the model — it is read from the distance Facebook displays on the listing when present, and left null otherwise. Asking a language model to compute geography invites confident wrong answers.

### Unverifiable listings

Many Marketplace listings state no hours at all — *"Bobcat T770, runs great, $38,000."* Silently dropping those would miss exactly the underpriced private-seller listings the tool exists to find; alerting on all of them is noise.

**Default (`on_unknown: alert`):** a listing that clears the price and keyword gates but has criteria that cannot be verified from its text is alerted under a separate "unverified" heading, with the specific gap named (*"hours not stated"*). Set `on_unknown: skip` per search if a particular query proves noisy.

## State and deduplication

SQLite file beside the config. **Every listing ID ever seen is recorded, matched or not** — this ledger is what guarantees a listing is examined once and alerted on at most once. Nothing is ever pruned; full history costs a few megabytes after years and is worth more than the disk.

### `listings`

`listing_id` (Facebook's, primary key), `search_name`, `first_seen_at`, `last_seen_at`, `title`, `price_cents`, `location`, `url`, `thumbnail_url`, `fingerprint`, `stage` (`prefiltered_out` | `extracted` | `matched` | `pending`), `reject_reason`, `watched` (bool), `last_change_check_at`.

Step 2 of the run is a single `WHERE listing_id NOT IN (...)` against this table, so a rejected listing costs nothing forever after — no detail fetch, no Claude call, no email.

### `listing_details`

`listing_id`, `description` (raw text), `structured_fields` (JSON — whatever Facebook's payload exposes: condition, category, seller info), `photo_urls` (JSON), `fetched_at`, `content_hash`.

The raw detail is stored, not just the attributes derived from it. This is what makes `replay` possible (see Shakedown and rollout): criteria can be re-evaluated against real historical listings without touching Facebook again. It also gives the description diffing needed for watched-listing change detection. Cost is a kilobyte or two per listing — a few megabytes over years.

### `extractions`

`listing_id`, `attributes` (JSON), `verdict`, `confidence`, `reasoning`, `model`, `input_tokens`, `output_tokens`, `cost_cents`, `created_at`.

This is the table to read when the tool alerts on something dumb, or stays silent on a machine found manually — it records exactly what it concluded and why.

### `notifications`

`listing_id`, `channel`, `kind` (`match` | `unverified` | `price_change` | `removed`), `sent_at`, `status`. Checked before sending, so a crash between "email sent" and "run finished" cannot duplicate an alert.

### `runs`

`run_id`, `started_at`, `ended_at`, per-stage counters (found / new / prefiltered / extracted / matched / errors), and timing. Makes "has this worked all week?" a one-line query.

### Relist suppression

Sellers routinely delete and repost an unsold machine, and Facebook assigns a **new listing ID**. Pure ID dedupe would re-alert on the same Bobcat every few weeks.

Alongside the ID, each listing stores a **content fingerprint** — normalized title + price + seller + location, hashed. A new listing ID whose fingerprint matches one seen in the last **60 days** is recorded but not alerted on.

A repost *with a price drop* produces a different fingerprint and therefore does alert. That is intended: a price drop on a machine in this market is news. The accepted consequence is that a seller who relists $1,000 cheaper every two weeks will ping every two weeks.

## Favorites

The user saves listings using **Facebook Marketplace's own Save feature**, from the app or the web. Each run, the scanner reads the saved-items page and treats every saved listing ID as watched; un-saving removes it. Facebook's saved list is the source of truth and the database mirrors it, so there is no separate state to keep in sync.

This requires no inbound channel, no web service, and no additional process. It also covers listings the searches never found — a machine spotted while browsing manually can be saved and is then tracked identically.

**Constraint:** saving only registers if it happens on the same Facebook account the scraper logs in as. The user has chosen to run the tool on their primary account for this reason (see Decisions).

For each watched listing, every run re-fetches the detail page and compares against the stored record, alerting on:

- **Price change** (either direction)
- **Description edited**
- **Listing gone** — 404 or removed, reported as *likely sold*

A watched listing the tool has never seen before gets a full extraction on first sight, so change alerts carry the same attribute table as everything else and there is a baseline to diff against.

## Notifications

**One email per run, never one per listing.** Three matches produce one message with three cards. A run that finds nothing sends nothing — silence is the normal state, and that is what makes an alert worth reading.

Each match card contains: photos, title, price, location (and distance where Facebook states it), a compact table of extracted attributes (hours, year, cab/AC, 2-speed, undercarriage, attachments, seller type), the two-line reasoning from the extractor, and a button through to the listing.

**Photos are embedded, not hotlinked.** Facebook image URLs are signed and expire within days, so a linked email decays into broken-image boxes. Up to three photos per match are downloaded during the detail fetch and attached inline as CID attachments, keeping the email complete indefinitely.

Sections within the single email:

1. **Matches** — full cards
2. **Unverified** — same cards, with the specific unknown called out
3. **Watched: changes** — *"Price drop: 2019 T770, $41,000 → $38,000"*, *"Listing removed (likely sold): 2017 T300"*

**SMS is a nudge, not a report.** One message per run, only when there is something to say: `MarketSearch: 3 new matches (T770 x2, T300) — check email.` No detail, no links. It exists so the user knows to look on a day when a good machine will be gone by lunch. Via Twilio, roughly a penny each.

Both channels write to `notifications` only on a confirmed send, so a failed alert is re-sent on the next run rather than lost, and a successful one is never repeated.

## Error handling

Four failures are expected, in rough order of likelihood.

**Session expiry or Facebook checkpoint.** The scraper detects a login wall or challenge page and stops immediately — no retries. Retrying a checkpoint is how a soft flag becomes a hard one. It records a `needs_login` state, emails once, and skips every subsequent run without touching Facebook until cleared. `marketsearch login` opens a visible browser on the box for manual login; the session then persists in the Chrome profile.

**Facebook changes page structure.** The parser distinguishes *"found zero listings"* from *"could not locate the data at all"* — conflating those is how a scraper silently reports nothing for three weeks. A parse failure writes the offending HTML to a debug folder and emails once, so the actual page is available to fix against.

**Claude API failure or rate limiting.** The SDK retries with backoff. If extraction still fails, the listing is recorded `stage='pending'` rather than processed and is retried next run, up to three attempts, then flagged and left alone. Nothing is silently dropped.

**Email or SMS delivery failure.** No notification row is written, so the alert is re-sent next run.

Two cross-cutting rules:

- **Operational alerts are rate-limited to one per day per problem**, with a single "back to normal" message when it clears. A tool that emails every 45 minutes about being broken gets filtered to spam, and then the real matches go unseen too.
- **A lockfile prevents overlapping runs**, so a slow sweep cannot collide with the next scheduled one.

All runs write to a rotating log: one summary line per run, full detail on errors. Each listing is committed in its own SQLite transaction, so a crash loses at most the in-flight listing.

Photo download failures degrade to a card without photos rather than failing the alert.

## Testing

The parts worth testing are the parts that encode judgment, not the parts that talk to Facebook.

**Unit tests** — prefilter rules (table-driven), fingerprint normalization, config validation, dedupe and notification-idempotency against an in-memory SQLite.

**Parser fixture tests** — saved copies of real Marketplace search, detail, and saved-items pages, checked into the repo with expected parse output. When Facebook changes something these fail first and identify what moved.

**Extraction golden files** — roughly a dozen real listing descriptions paired with the attributes and verdict they should produce: a clean listing, hours buried mid-paragraph, hours only in the title, no hours stated, a wrong-model machine, and one written in all caps without punctuation. Run against the live API in a marked test group (a few cents per run), with recorded responses for fast CI. This is the suite that catches a tightened criterion breaking something else.

**Email rendering** — output written to a file openable in a browser, plus an HTML snapshot test so formatting does not drift.

**Two manual commands cover what tests cannot:**

- `marketsearch test-search "Bobcat T770"` — live fetch, prints what was parsed. The smoke test after any Facebook breakage.
- `marketsearch run --dry-run` — a complete real sweep that writes nothing and prints exactly what it would have alerted on. Used for the very first runs, before there is any stored history to `replay` against.

## Shakedown and rollout

Nothing gets emailed until the tool has demonstrably earned it. Three capabilities support that, addressing three distinct needs.

### Alerts off by default

`notifications.enabled` ships as `false`. The tool runs its full schedule — scraping, extracting, writing every decision to the database — and simply does not send. It is therefore impossible to accidentally email during setup, and the shakedown period accumulates genuine history rather than throwaway output.

### `marketsearch preview`

Renders the last run's email — the real HTML, produced by the same code path that would send it, photos embedded — to a file and opens it in a browser. Not a terminal summary that approximates the email; the actual email. `--run <id>` opens any earlier run instead.

### `marketsearch replay`

Re-runs extraction over stored listings using the *current* criteria, and prints a verdict diff:

```
$ marketsearch replay --search bobcat-t770 --since 30d

bobcat-t770 — 34 listings replayed
  → match        12  (was 9)   +3
  → unverifiable  4  (was 7)   -3
  → no_match     18  (was 18)

  CHANGED:
  1094… 2019 T770, 2,400 hrs, $38,000   unverifiable → match   (2-speed now confirmed)
  1102… 2016 T770, 2,900 hrs, $31,500   match → no_match       (undercarriage "needs work")
```

This is what makes criteria tuning practical. Edit a sentence in the YAML, replay, and see exactly which listings moved and why — without touching Facebook once. Two things follow from that: every replay carries zero detection risk, and tuning happens against real listings already reviewed rather than hypotheticals. Cost is a couple of cents per replayed listing. `replay` reads `listing_details` and writes new rows to `extractions`; it never re-scrapes.

### The rollout sequence

1. `marketsearch run --dry-run` — confirm scraping and parsing work at all
2. Run on schedule with `notifications.enabled: false` for several days
3. `marketsearch preview` — read what it would have sent
4. Edit criteria → `marketsearch replay` → repeat until the verdicts look right
5. Set `notifications.enabled: true`

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Runtime | Dedicated always-on Windows 11 box | Residential IP and a real browser profile; best case against anti-bot |
| Acquisition | Playwright + persistent Chrome profile, JSON payload parsing | Free, full fidelity, quarantined behind one interface |
| Matching | Deterministic prefilter, then Claude extraction on survivors | Best accuracy per dollar; free gates cut the volume first |
| Model | `claude-opus-5` at `effort: low` | ~2¢/listing; judgment over vague prose is the whole value. Haiku 4.5 available as a config swap |
| Stack | Python — Playwright, SQLite, Pydantic, Anthropic SDK | Readable, best ecosystem for scrape-and-parse |
| Geography | One anchor, wide radius | Simplest, fastest, lowest rate-limit risk |
| Cadence | Jittered 30–60 min, waking hours only | Beats most buyers to a fresh listing without a machine-like pattern |
| Config | YAML with free-text criteria | Editable in any editor, version-controllable, no UI to build |
| Alerts | Batched email with inline photos, plus an SMS nudge | Rich detail where it belongs, urgency where it's needed |
| Unverifiable listings | Alert with the gap named (`on_unknown: alert`) | No-hours listings are often the best finds; per-search override available |
| Relists | Fingerprint suppression, 60-day window; price drops alert | Kills repost spam without hiding genuine price movement |
| Favorites | Read Facebook's own saved-items list | Zero new infrastructure; works from the phone; un-save is the unfollow |
| FB account | User's primary account | Required for saved-items sync; risk accepted, mitigated by conservative defaults |
| Rollout | Alerts off by default; `preview` and `replay` for tuning | Trust is earned against real listings before anything reaches the inbox |
| Raw detail storage | Store description and fields, not just derived attributes | Enables `replay` tuning with no re-scraping, and description diffing for watched listings |

## Out of scope

- A web UI for editing searches or browsing history (the CLI and SQLite cover it)
- Multiple anchor cities or nationwide coverage
- Automated messaging of sellers
- Any marketplace other than Facebook
- Automatic failover to a paid scraping API
- Click-to-follow links in email — superseded by saved-items sync, which is strictly better here

Each of these can be added on top of this design without disturbing it.
