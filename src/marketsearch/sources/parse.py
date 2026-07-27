"""Pure functions over Facebook page HTML.

No browser, no network — every rule here is testable against a saved page.
This is the module that breaks when Facebook changes, so it needs the fastest
possible test cycle.

Strategy: walk every embedded JSON payload looking for *nodes carrying the
right keys*, never for a fixed path. Facebook restructures its payload tree
often; the leaf key names change far more slowly.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from marketsearch.models import ListingDetail, RawListing
from marketsearch.sources.base import ParseError

_SCRIPT_JSON = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)

_LOGIN_MARKERS = ('name="pass"', 'action="/login/', "login_form")

# Markers must be phrases a human would *see* on a real interstitial. A bare
# "checkpoint" substring is not usable: Facebook embeds a URL routing table
# containing "\/checkpoint\/block\/" in the script payload of every page, so it
# matched on ordinary Marketplace results and paused every run.
_CHECKPOINT_MARKERS = ("confirm it's you", "security check", 'id="checkpoint"')

ITEM_URL = "https://www.facebook.com/marketplace/item/{listing_id}/"


def extract_json_blobs(html: str) -> list[Any]:
    """Every parseable JSON payload embedded in the page. Unparseable script
    bodies are skipped rather than fatal — Facebook ships several formats."""
    blobs: list[Any] = []
    for match in _SCRIPT_JSON.finditer(html):
        try:
            blobs.append(json.loads(match.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
    return blobs


def iter_dicts(obj: Any) -> Iterator[dict]:
    """Yield every dict anywhere in a nested structure, including the root."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_dicts(item)


def _all_dicts(html: str) -> list[dict]:
    blobs = extract_json_blobs(html)
    if not blobs:
        raise ParseError(
            "no JSON payload found in page — Facebook's markup has probably changed"
        )
    return [d for blob in blobs for d in iter_dicts(blob)]


def _looks_like_listing(node: dict) -> bool:
    return "id" in node and "marketplace_listing_title" in node


def _price_cents(node: dict) -> int | None:
    price = node.get("listing_price")
    if not isinstance(price, dict):
        return None
    amount = price.get("amount")
    if amount is None:
        return None
    try:
        return int(round(float(amount) * 100))
    except (TypeError, ValueError):
        return None


def _location(node: dict) -> str | None:
    loc = node.get("location")
    if isinstance(loc, dict):
        geo = loc.get("reverse_geocode")
        if isinstance(geo, dict):
            page = geo.get("city_page")
            if isinstance(page, dict) and page.get("display_name"):
                return str(page["display_name"])
            city, state = geo.get("city"), geo.get("state")
            if city and state:
                return f"{city}, {state}"
    text = node.get("location_text")
    if isinstance(text, dict) and text.get("text"):
        return str(text["text"])
    return None


def _thumbnail(node: dict) -> str | None:
    photo = node.get("primary_listing_photo")
    if isinstance(photo, dict):
        image = photo.get("image")
        if isinstance(image, dict) and image.get("uri"):
            return str(image["uri"])
    return None


def _seller(node: dict) -> str | None:
    seller = node.get("marketplace_listing_seller")
    if isinstance(seller, dict) and seller.get("name"):
        return str(seller["name"])
    return None


def _to_raw_listing(node: dict) -> RawListing:
    listing_id = str(node["id"])
    return RawListing(
        listing_id=listing_id,
        title=str(node["marketplace_listing_title"]),
        price_cents=_price_cents(node),
        location=_location(node),
        url=ITEM_URL.format(listing_id=listing_id),
        thumbnail_url=_thumbnail(node),
        seller_name=_seller(node),
    )


def parse_search_results(html: str) -> list[RawListing]:
    """All listings on a search page, in payload order, deduplicated by id.

    An empty list means Facebook returned no results. A ParseError means the
    page could not be understood at all. Conflating those is how a scraper
    silently reports nothing for three weeks.
    """
    listings: dict[str, RawListing] = {}
    for node in _all_dicts(html):
        if _looks_like_listing(node):
            listing = _to_raw_listing(node)
            listings.setdefault(listing.listing_id, listing)
    return list(listings.values())


def parse_graphql_payload(body: str) -> list[RawListing]:
    """Listings from a Marketplace GraphQL response.

    Scrolling a search page fetches more results over GraphQL rather than
    re-rendering the page's JSON script tags, so these bodies are the only
    place results past the first screenful appear. Facebook frames them as one
    JSON document per line, which is why a whole-body parse fails.

    Unlike the page parser, a body with no listings is not an error: most
    GraphQL traffic on the page has nothing to do with search results.
    """
    listings: dict[str, RawListing] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            blob = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        for node in iter_dicts(blob):
            if _looks_like_listing(node):
                listing = _to_raw_listing(node)
                listings.setdefault(listing.listing_id, listing)
    return list(listings.values())


def parse_saved_listings(html: str) -> list[RawListing]:
    """Listings from the saved-items page.

    The saved page ships the same listing nodes as search, so this is the same
    walk. It returns full listings rather than ids because a machine saved
    while browsing may never have appeared in a search — the watched-listing
    pipeline needs its title and price to build a card without a second fetch.
    """
    return parse_search_results(html)


def _description(node: dict) -> str:
    for key in ("redacted_description", "description"):
        value = node.get(key)
        if isinstance(value, dict) and value.get("text"):
            return str(value["text"])
        if isinstance(value, str):
            return value
    return ""


def _structured_fields(node: dict) -> dict[str, object]:
    fields: dict[str, object] = {}
    attributes = node.get("attribute_data")
    if isinstance(attributes, list):
        for attribute in attributes:
            if isinstance(attribute, dict) and attribute.get("label"):
                fields[str(attribute["label"])] = attribute.get("value")
    for key in ("delivery_types", "creation_time", "is_sold"):
        if key in node:
            fields[key] = node[key]
    return fields


def _photo_urls(node: dict) -> list[str]:
    urls: list[str] = []
    photos = node.get("listing_photos")
    if isinstance(photos, list):
        for photo in photos:
            if isinstance(photo, dict):
                image = photo.get("image")
                if isinstance(image, dict) and image.get("uri"):
                    uri = str(image["uri"])
                    if uri not in urls:
                        urls.append(uri)
    return urls


def parse_item_detail(html: str, listing_id: str) -> ListingDetail:
    """The detail page for one listing.

    Prefers the node whose id matches; falls back to the richest listing-shaped
    node, since Facebook occasionally omits the id on the detail payload.
    """
    candidates = [n for n in _all_dicts(html) if _looks_like_listing(n)]
    exact = [n for n in candidates if str(n.get("id")) == str(listing_id)]
    pool = exact or [n for n in candidates if "redacted_description" in n or "description" in n]
    if not pool:
        raise ParseError(f"no listing node found for {listing_id} on the detail page")

    node = max(pool, key=lambda n: len(_description(n)))
    return ListingDetail(
        listing_id=str(listing_id),
        description=_description(node),
        structured_fields=_structured_fields(node),
        photo_urls=_photo_urls(node),
        distance_miles=None,
    )


_UNAVAILABLE_MARKERS = (
    "this listing isn't available",
    "this listing is no longer available",
    "content isn't available",
    "sorry, this content isn't available",
)


def detect_unavailable(html: str) -> bool:
    """True when Facebook says the listing is gone."""
    lowered = html.lower()
    return any(marker in lowered for marker in _UNAVAILABLE_MARKERS)


def strip_scripts(html: str) -> str:
    """Drop <script> and <style> bodies.

    Interstitial detection must read only what a human would see. Facebook ships
    a URL routing table inside its script payloads that mentions login and
    checkpoint endpoints on every page, including ordinary search results.
    """
    return _SCRIPT_OR_STYLE.sub(" ", html)


def detect_login_wall(html: str) -> str | None:
    """Return 'login', 'checkpoint', or None.

    Checked before parsing on every page. A non-None result must stop the run
    immediately — never retry into a checkpoint.
    """
    lowered = strip_scripts(html).lower()
    if any(marker in lowered for marker in _CHECKPOINT_MARKERS):
        return "checkpoint"
    if any(marker.lower() in lowered for marker in _LOGIN_MARKERS):
        return "login"
    return None
