# MarketSearch — Handoff

**Updated:** 2026-07-27
**Branch:** `main` at `a913650`
**State:** implementation complete; shakedown not started.

## Where things stand

| Stage | Status |
|---|---|
| Design spec | ✅ `docs/superpowers/specs/2026-07-26-marketsearch-design.md` |
| Implementation plan | ✅ `docs/superpowers/plans/2026-07-26-marketsearch.md` (20 tasks) |
| Tasks 1–20 | ✅ All implemented and committed, one commit per task (`755c6a9`..`07f0669`) |
| Local environment | ✅ Set up on this machine (see below) |
| Verification checklist | ◐ 5 of 8 confirmed |
| README shakedown | ⬜ Not started — blocked on Facebook login |

## Environment on this machine

`C:\Users\bowde\source\repos\MarketSearch`, Python 3.13.5.

```powershell
.\.venv\Scripts\python.exe -m pytest      # 219 passed, 4 skipped, 6 deselected
```

Playwright Chromium 149.0.7827.55 is installed and verified launching.

**The 4 skips are the thing to watch.** All are in `tests/test_parse.py:138` and skip
because `real_search.html`, `real_item.html`, and `real_saved.html` have never been
captured. The parser has only ever been tested against invented fixtures. Those three
files are what tell you whether `src/marketsearch/sources/parse.py` survives contact
with real Facebook markup — **expect it to need fixing** once they exist. The 6
deselected are the `live_api` / `live_fb` marked tests.

## Verification checklist status

Confirmed:

- ✅ `pytest` passes with no network access
- ✅ `marketsearch --help` lists `run`, `login`, `test-search`, `history`, `preview`, `replay`
- ✅ Notifications ship disabled (`notifications.enabled: false`)
- ✅ A parse failure writes to `debug/` — `tests/test_facebook_source.py:80`
- ✅ No stale lock survives a killed run — `tests/test_runstate.py:42-62`

Outstanding:

- ⬜ `marketsearch run --dry-run` leaves the database unchanged (needs a live sweep)
- ⬜ `git log -p` contains no secrets (worth an explicit pass before any push)
- ⬜ `pytest -m live_api` passes — **costs ~15 cents**, needs `ANTHROPIC_API_KEY`

## What's next, in dependency order

1. Fill in real values in `config.yaml` and `.env` — anchor city, radius, searches,
   Anthropic key, SMTP app password. (Currently all placeholders.)
2. `marketsearch login` — opens a browser; log into Facebook and set Marketplace
   location and radius **by hand**. Facebook derives Marketplace location from the
   profile, so this is the only way the tool knows where to look. Creates
   `chrome-profile/`; its absence means this step has not run.
3. `python scripts/capture_pages.py` — un-skips the three real-page parser tests.
4. `pytest` — fix `parse.py` against whatever the real captures reveal.
5. Then the README shakedown, steps 1–6, ending with `notifications.enabled: true`.

## Configuration notes

**The example files are gone, deliberately.** `config.example.yaml` and `.env.example`
were converted into `config.yaml` and `.env` rather than copied (commit-pending
deletions). The README's `copy config.example.yaml config.yaml` line no longer matches
reality and should be updated if the repo is ever shared.

**`config.yaml` is not gitignored.** Only `.env` is. Actual secrets live in `.env` and
`config.yaml` holds only the *names* of the env vars (`password_env`,
`account_sid_env`), so nothing sensitive is exposed today — but `config.yaml` does hold
the anchor city, destination email, and phone numbers, and a `git add -A` would commit
them. Add it to `.gitignore` if that matters.

**Twilio is optional in practice.** The SMS is only a nudge; `notify/delivery.py:176`
catches its failure so a dead Twilio account cannot cost you the email. The `sms:`
block must still *exist* in `config.yaml` (`config.py:65` requires it) — placeholder
values are fine. A carrier email-to-SMS gateway address in the email recipients is a
zero-cost substitute.

## Costs once running

Roughly 2¢ per listing examined at the default `claude-opus-5` / `effort: low`, plus
about a penny per SMS. Switching `extraction.model` to `claude-haiku-4-5` drops it to
about a third of a cent per listing.

## Resolved — no longer live concerns

These were carried in the previous handoff and are now closed:

- **`store._conn` leakage.** Tasks 18 and 19 use the public `Store.recent_runs()` and
  `Store.get_run()` instead of the private attribute, as planned (see `846dc69`).
- **`.gitignore` portability.** The three tooling entries survived every later task.
- **Task 15's `extraction_attempts` column.** Landed with the tests updated.
- **Task 1's missing review.** Task 1 was reviewed and all 19 downstream tasks built
  on it cleanly.
