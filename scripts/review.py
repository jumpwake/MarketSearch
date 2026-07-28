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
    catalog = [(w, m) for w in cfg.watchlists for m in w.models]

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
            f"- **Model:** `{r['model_name']}` · **Listing ID:** `{r['listing_id']}`",
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

    def dropped_table(rows) -> list[str]:
        if not rows:
            return ["_Nothing dropped._", ""]
        lines = ["| Price | Title | Dropped because |", "|---|---|---|"]
        for r in rows:
            url = r["url"] or ITEM_URL.format(listing_id=r["listing_id"])
            title = (r["title"] or "").replace("|", "\\|")
            lines.append(
                f"| {money(r['price_cents'])} | [{title}]({url}) | {r['reject_reason']} |"
            )
        lines.append("")
        return lines

    for watchlist, model in catalog:
        rows = conn.execute(
            "select * from listings where model_name=? and stage='prefiltered_out' "
            "order by price_cents desc",
            (model.name,),
        ).fetchall()
        kept = conn.execute(
            "select count(*) from listings where model_name=? and stage!='prefiltered_out'",
            (model.name,),
        ).fetchone()[0]
        out += [
            f"### `{model.name}` — watchlist `{watchlist.name}`",
            "",
            f"- **Price band:** {money(model.price_min_cents)} – "
            f"{money(model.price_max_cents)}",
            f"- **Keywords:** `{list(model.keywords)}` · "
            f"**Excluded:** `{list(watchlist.exclude)}`",
            f"- **{kept} kept, {len(rows)} dropped**",
            "",
        ]
        out += dropped_table(rows)

    unassigned = conn.execute(
        "select * from listings where model_name is null and stage='prefiltered_out' "
        "order by price_cents desc"
    ).fetchall()
    out += [
        "### Matched no model at all",
        "",
        "Pooled from some query, offered to every model, accepted by none.",
        f"**{len(unassigned)} dropped.**",
        "",
    ]
    out += dropped_table(unassigned)

    # The check that matters: did the catalog throw away a real match? Under
    # pooling a dropped listing belongs to no model, so scan every dropped row
    # for every model's number rather than scoping by the row's label.
    out += ["---", "", "## Near-miss check", "", ""]
    all_dropped = conn.execute(
        "select title,price_cents,reject_reason,model_name from listings "
        "where stage='prefiltered_out'"
    ).fetchall()
    any_near = False
    for _watchlist, model in catalog:
        numbers = sorted({
            digits for keyword in model.keywords
            if (digits := "".join(ch for ch in keyword if ch.isdigit()))
        })
        if not numbers:
            continue
        # One entry per listing, not per keyword — the same machine matching
        # six spellings of one model number is one near-miss, not six.
        near = [
            r for r in all_dropped
            if r["model_name"] != model.name
            and any(d in (r["title"] or "").lower() for d in numbers)
        ]
        if near:
            any_near = True
            out += [
                f"**`{model.name}`: {len(near)} dropped listing(s) mention "
                f"{' or '.join(f'`{d}`' for d in numbers)} but were not kept as "
                f"`{model.name}`** — the catalog may be too strict:",
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
            "No dropped listing contained a model number from any model's `keywords`,",
            "so nothing real was filtered out. The catalog is precise.",
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
        "Adjusting a model's `price`, its `keywords`, or the watchlist's `exclude`",
        "changes what gets extracted on the *next* sweep. `marketsearch requeue` also",
        "re-tests everything already dropped against the edited catalog, with no",
        "scraping — re-run this report afterwards to compare.",
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
