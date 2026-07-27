from __future__ import annotations

from pathlib import Path

from marketsearch.sources.facebook import (
    SAVED_URL,
    FacebookSource,
    build_item_url,
    build_search_url,
)

OUT = Path("tests/fixtures/pages")
OUT.mkdir(parents=True, exist_ok=True)

with FacebookSource(Path("chrome-profile"), headless=False) as src:
    for name, url in [
        ("real_search.html", build_search_url("Bobcat T770", 250)),
        ("real_saved.html", SAVED_URL),
    ]:
        (OUT / name).write_text(src._load(url, name), encoding="utf-8")
        print("wrote", name)

    listings = src.search("Bobcat T770", "anchor", 250)
    if listings:
        html = src._load(build_item_url(listings[0].listing_id), "item")
        (OUT / "real_item.html").write_text(html, encoding="utf-8")
        print("wrote real_item.html for", listings[0].listing_id)
