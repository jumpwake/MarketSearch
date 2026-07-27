# MarketSearch — Handoff

**Paused:** 2026-07-26
**Branch:** `feat/marketsearch` (implementation) — branched from `main` at `dbebe6a`
**Reason:** moving the work to a different machine.

## Where things stand

| Stage | Status |
|---|---|
| Design spec | ✅ Complete and approved — `docs/superpowers/specs/2026-07-26-marketsearch-design.md` |
| Implementation plan | ✅ Complete, self-reviewed — `docs/superpowers/plans/2026-07-26-marketsearch.md` (20 tasks) |
| Task 1 — scaffolding and models | ⚠️ **Code written and passing, but never reviewed** (see below) |
| Tasks 2–20 | ⬜ Not started |

### Task 1 is committed but NOT reviewed

Task 1 was dispatched to a subagent which created all seven files and then was
interrupted before it installed dependencies, ran the tests, committed, or
wrote its report. On pause, the controller:

- verified the files match the task brief exactly (`models.py`, `.gitignore`,
  `pyproject.toml`, `.env.example`, `__init__.py`, `conftest.py`,
  `test_models.py`)
- installed `pytest` and `pydantic` only, and ran the suite: **3 passed**
- committed the work so it would survive the move

**What has NOT happened for Task 1:** the full `pip install -e ".[dev]"`, the
implementer's self-review, the SDD task review (spec compliance + quality), and
the ledger completion entry. **Treat Task 1 as needing its review before Task 2
starts.**

## Resuming on the new machine

```bash
git clone https://github.com/jumpwake/MarketSearch.git
cd MarketSearch
git checkout feat/marketsearch

python -m venv .venv                       # Python 3.12+ required
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # macOS/Linux

.venv/Scripts/python.exe -m pytest         # expect 3 passed
```

Then tell Claude:

> Resume executing `docs/superpowers/plans/2026-07-26-marketsearch.md` with
> subagent-driven development. Task 1's code is committed but was never
> reviewed — start by reviewing it, then continue from Task 2.

## Decisions made during setup that are NOT in the plan

The SDD ledger lives in `.superpowers/` which is git-ignored and does **not**
travel. These are the decisions it held:

1. **Workspace.** Implementation happens in an isolated git worktree on
   `feat/marketsearch`, created from local `HEAD` rather than `origin/main`
   (the remote had no branches at the time). On the new machine a plain
   checkout of the branch is fine — the worktree was machine-local.

2. **Pre-flight conflict resolution — `store._conn`.** The plan mandates
   reaching into the private `Store._conn` attribute from *production* code in
   two places: Task 18's `cli.py history` and Task 19's
   `shakedown.collect_run_cards`. A code-quality reviewer will flag this every
   time. **Resolution: those two tasks should add public
   `Store.recent_runs(limit) -> list[sqlite3.Row]` and
   `Store.get_run(run_id) -> sqlite3.Row | None` methods and call those
   instead.** Test code may keep using `_conn` — that is normal test practice.
   Apply this when Tasks 18 and 19 are dispatched.

3. **`.gitignore` portability.** `.claude/worktrees/`, `.superpowers/`, and
   `.claude/settings.local.json` were added to the committed `.gitignore`
   beyond what Task 1's brief specifies, because `.git/info/exclude` does not
   survive a clone. If a later task rewrites `.gitignore`, **merge rather than
   overwrite** — do not drop these three lines.

## Known gotchas ahead

- **Task 9** (`pytest -m live_api`) costs real money — roughly 15 cents per
  run against the Anthropic API. Needs `ANTHROPIC_API_KEY`.
- **Task 11** requires `playwright install chromium`, a real Facebook login,
  and a manual page-capture step. Its synthetic HTML fixtures are structurally
  faithful but invented — **expect the parser to need fixing** when real
  captures replace them. That step is designed to make the discovery cheap.
- **Task 15** adds an `extraction_attempts` column to `listings` and a fifth
  `stage` value, `"failed"`. Every existing `ListingRow(...)` construction in
  the tests must gain `extraction_attempts=0` at that point — the task's
  Step 3 says so explicitly.
- The remote `origin` was empty when this was pushed; `main` and
  `feat/marketsearch` are the first two branches on it.

## Costs to expect once running

Roughly 2¢ per listing examined at the default `claude-opus-5` / `effort: low`,
plus about a penny per SMS. Switching `extraction.model` to `claude-haiku-4-5`
drops it to about a third of a cent per listing.
