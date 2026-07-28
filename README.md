# MarketSearch

Watches Facebook Marketplace for specific heavy equipment, judges each new
listing against plain-English criteria, and emails you only when something is
worth looking at.

Design: [`docs/superpowers/specs/2026-07-26-marketsearch-design.md`](docs/superpowers/specs/2026-07-26-marketsearch-design.md)

## What it does

- Searches Marketplace on a jittered 30–60 minute cadence during waking hours.
- Pools every search query's results, then keeps any listing matching any model
  in your catalog — so a query for one machine still catches another you want.
  Drops the rest before loading a single detail page.
- Reads the survivors' descriptions with Claude, extracting engine hours,
  specs, stated condition, and attachments, then judging them against your
  criteria.
- Sends one email per run with photos embedded, plus a short SMS nudge.
- Tracks anything you save on Marketplace and reports price drops, edited
  descriptions, and listings that disappear.

**A note on terms of service.** Automated access to Marketplace violates
Facebook's ToS. This is a personal-use tool at low volume from a residential
IP. The realistic worst case is the account being checkpointed or restricted.

## Requirements

- An always-on Windows 11 machine on a residential connection
- Python 3.12+, Google Chrome
- An Anthropic API key
- A Gmail account with an app password (for sending), and a Twilio account

## Setup

```powershell
git clone <repo> C:\MarketSearch
cd C:\MarketSearch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium

copy config.example.yaml config.yaml
copy .env.example .env
```

Edit `.env` with your real secrets — it is gitignored and must never be
committed. Edit `config.yaml`: set your anchor city, radius, and watchlists.
**Leave `notifications.enabled: false` for now.**

Then log in and set your Marketplace location:

```powershell
marketsearch login
```

A browser opens. Log into Facebook, open Marketplace, and set your location and
search radius by hand. Facebook derives Marketplace location from your profile,
so this is how the tool knows where to look. Close the window when done.

**Set Windows to never sleep** — a sleeping machine runs no sweeps.

## Shakedown

Nothing is emailed until the tool has earned it. Work through this in order.

**1. Confirm scraping works.**

```powershell
marketsearch test-search "Bobcat T770"
```

You should see a list of parsed listings. Zero listings with no error means
Facebook returned nothing; an error means the parser needs attention.

**2. Do a dry run.**

```powershell
marketsearch run --dry-run
```

A complete real sweep that writes nothing and sends nothing.

**3. Let it run with alerts off.**

```powershell
.\scripts\install_task.ps1
```

Leave it for several days. It scrapes, extracts, and records every decision
without sending anything.

**4. Read what it would have sent.**

```powershell
marketsearch preview
```

Renders the actual email — same code path, photos and all — and opens it in
your browser.

**5. Tune the criteria.**

Edit a criteria block in `config.yaml`, then:

```powershell
marketsearch replay --search bobcat-t770 --since 30d
```

This re-judges stored listings with your new criteria and prints which verdicts
moved and why. It never touches Facebook, so iterate freely. When you like the
result, `--save` persists the new verdicts.

**6. Turn on alerts.**

Set `notifications.enabled: true` in `config.yaml`. That's it.

## Day-to-day

| Command | What it does |
|---|---|
| `marketsearch run` | One sweep. Task Scheduler calls this. |
| `marketsearch run --dry-run` | Sweep that writes and sends nothing. |
| `marketsearch history` | Recent runs and their counters. |
| `marketsearch preview` | Re-render the last run's email. |
| `marketsearch replay --search NAME --since 30d` | Re-judge stored listings. |
| `marketsearch requeue` | Re-test stored rejections against the current catalog. No scraping. |
| `marketsearch test-search "QUERY"` | Live search, prints parsed results. |
| `marketsearch login` | Re-authenticate after a checkpoint. |

**Favourites.** Tap Save on any Marketplace listing, from any device signed
into the same Facebook account. The next run picks it up and starts reporting
price drops, description edits, and removals. Un-save to stop.

## When something breaks

**"MarketSearch needs you to log in again."** Facebook presented a login wall
or a security checkpoint. All scraping is paused — deliberately, because
retrying into a checkpoint is how a soft flag becomes a hard one. Run
`marketsearch login` on the box, clear it by hand, and runs resume.

**"MarketSearch could not parse a Facebook page."** Facebook changed its
markup. The offending page is saved under `debug/`. Fix
`src/marketsearch/sources/parse.py` against it — `tests/test_parse.py` is the
fastest loop for that — and everything downstream keeps working untouched.

**Silence for days.** Check `marketsearch history`. Runs with zero found
listings usually mean a session or location problem; runs that are absent
entirely mean Task Scheduler is not firing.

Logs are in `logs/marketsearch.log`, rotated at 2 MB.

## Costs

Roughly two cents per listing examined at the default `claude-opus-5` /
`effort: low`, plus about a penny per SMS. At a handful of genuinely new
listings a day that is a few dollars a month. Switching
`extraction.model` to `claude-haiku-4-5` drops it to about a third of a cent
per listing.

## Development

```powershell
pytest                      # full suite, no network
pytest -m live_api          # golden-file extraction against the real API
```
