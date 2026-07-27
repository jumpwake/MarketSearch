"""Write a criteria-tuning report from the stored run history.

Reads the database only — no Facebook, no API calls, no cost. Run it whenever
you want to see what the current config did to real listings:

    python scripts/review.py

The report shows every listing Claude actually read (with its verdict and
reasoning), everything the prefilter dropped and why, and the near-miss check
that tells you whether the title filter is too tight.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from marketsearch.config import load_config
from marketsearch.sources.parse import ITEM_URL


def money(cents: int | None) -> str:
    return "—" if cents is None else f"${cents / 100:,.0f}"


def build(db_path: Path, config_path: Path) -> str:
    cfg = load_config(config_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    runs = conn.execute(
        "select run_id,started_at,found,new,matched,errors "
        "from runs order by run_id desc limit 10"
    ).fetchall()
    extractions = {r["listing_id"]: r for r in conn.execute("select * from extractions")}
    spend = conn.execute("select coalesce(sum(cost_cents),0) from extractions").fetchone()[0]
    searches = {s.name: s for s in cfg.searches}

    out: list[str] = [
        "# Listings review — MarketSearch",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from "
        f"`{db_path.name}`. Regenerate with `python scripts/review.py`.",
        "",
        f"**Anchor:** `{cfg.location.anchor}` · **Radius:** {cfg.location.radius_miles} mi "
        f"· **Model:** `{cfg.extraction.model}` (effort `{cfg.extraction.effort}`) "
        f"· **Spent to date:** {spend:.1f}¢",
        "",
        "## Runs",
        "",
        "| Run | Started (UTC) | Found | New | Matched | Errors |",
        "|---|---|---|---|---|---|",
    ]
    for r in runs:
        out.append(
            f"| {r['run_id']} | {r['started_at'][:19]} | {r['found']} | {r['new']} "
            f"| {r['matched']} | {r['errors']} |"
        )

    out += [
        "",
        "---",
        "",
        "## Listings Claude actually read",
        "",
        "These survived the prefilter and cost real money. **This is where `criteria`",
        "gets tuned** — the quoted reasoning is Claude judging the listing description",
        "against your criteria text.",
        "",
    ]

    extracted = conn.execute(
        "select * from listings where stage='extracted' order by price_cents"
    ).fetchall()
    if not extracted:
        out += ["_Nothing extracted yet._", ""]

    for r in extracted:
        e = extractions.get(r["listing_id"])
        url = r["url"] or ITEM_URL.format(listing_id=r["listing_id"])
        out += [
            f"### [{r['title']}]({url})",
            "",
            f"- **Price:** {money(r['price_cents'])}",
            f"- **Search:** `{r['search_name']}` · **Listing ID:** `{r['listing_id']}`",
        ]
        if e is None:
            out += ["- _No extraction row._", ""]
            continue

        unknowns = json.loads(e["unknowns"] or "[]")
        out += [
            f"- **Verdict:** `{e['verdict']}` (confidence {e['confidence']})",
            f"- **Cost:** {e['cost_cents']:.2f}¢ · {e['input_tokens']} in / "
            f"{e['output_tokens']} out",
            "- **Could not determine:** "
            + (", ".join(f"`{u}`" for u in unknowns) or "_nothing_"),
            "",
            "> " + (e["reasoning"] or "").strip().strip('"').replace("\n", "\n> "),
            "",
        ]
        attrs = json.loads(e["attributes"] or "{}")
        if attrs:
            out += [
                "<details><summary>Extracted attributes</summary>",
                "",
                "```json",
                json.dumps(attrs, indent=2),
                "```",
                "",
                "</details>",
                "",
            ]

    out += [
        "---",
        "",
        "## Dropped by the prefilter (free)",
        "",
        "Rejected before any detail page or API call. Scan for anything that *should*",
        "have survived — that means the filter is too tight.",
        "",
    ]

    for name, search in searches.items():
        rows = conn.execute(
            "select * from listings where search_name=? and stage='prefiltered_out' "
            "order by price_cents desc",
            (name,),
        ).fetchall()
        kept = conn.execute(
            "select count(*) from listings where search_name=? and stage!='prefiltered_out'",
            (name,),
        ).fetchone()[0]
        out += [
            f"### `{name}` — query `{search.query}`",
            "",
            f"- **Price band:** {money(search.price_min_cents)} – "
            f"{money(search.price_max_cents)}",
            f"- **Must match:** `{list(search.title_must_match)}` · "
            f"**Must not match:** `{list(search.title_must_not_match)}`",
            f"- **{kept} kept, {len(rows)} dropped**",
            "",
        ]
        if not rows:
            out += ["_Nothing dropped._", ""]
            continue
        out += ["| Price | Title | Dropped because |", "|---|---|---|"]
        for r in rows:
            url = r["url"] or ITEM_URL.format(listing_id=r["listing_id"])
            title = (r["title"] or "").replace("|", "\\|")
            out.append(
                f"| {money(r['price_cents'])} | [{title}]({url}) | {r['reject_reason']} |"
            )
        out.append("")

    # The check that matters: did the title filter throw away a real match?
    out += ["---", "", "## Near-miss check", "", ""]
    any_near = False
    for name, search in searches.items():
        for token in search.title_must_match:
            digits = "".join(ch for ch in token if ch.isdigit())
            if not digits:
                continue
            rows = conn.execute(
                "select title,price_cents,reject_reason from listings "
                "where search_name=? and stage='prefiltered_out'",
                (name,),
            ).fetchall()
            near = [r for r in rows if digits in (r["title"] or "").lower()]
            if near:
                any_near = True
                out += [
                    f"**`{name}`: {len(near)} dropped listing(s) mention `{digits}` "
                    f"but failed `{token}`** — the filter may be too strict:",
                    "",
                ]
                for r in near:
                    out.append(
                        f"- {money(r['price_cents'])} — {r['title']} "
                        f"_({r['reject_reason']})_"
                    )
                out.append("")
    if not any_near:
        out += [
            "No dropped listing contained the model number from `title_must_match`, so",
            "nothing real was filtered out. The title filter is precise.",
            "",
        ]

    out += [
        "---",
        "",
        "## Tuning loop",
        "",
        "Edit `criteria` in `config.yaml`, then re-judge the stored listings without",
        "touching Facebook:",
        "",
        "```powershell",
        ".\\.venv\\Scripts\\marketsearch.exe replay --search <name> --since 30d",
        "```",
        "",
        "It prints which verdicts moved and why. Add `--save` to persist them.",
        "To see the email these would produce: `marketsearch preview`.",
        "",
        "Adjusting `price`, `title_must_match`, or `title_must_not_match` changes what",
        "gets extracted on the *next* sweep — re-run this report afterwards to compare.",
        "",
    ]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("marketsearch.db"))
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--out", type=Path, default=Path("listings-review.md"))
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"{args.db} not found — run `marketsearch run` first.")

    args.out.write_text(build(args.db, args.config), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
