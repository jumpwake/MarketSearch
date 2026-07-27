"""Turn verdicts and changes into one email and one short SMS.

Photos are embedded as CID attachments rather than linked. Facebook's image
URLs are signed and expire within days, so a linked email decays into broken
image boxes — including months later, when you are trying to remember what the
machine looked like.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Callable

from jinja2 import Environment, select_autoescape

from marketsearch.store import ExtractionRow, ListingRow

log = logging.getLogger(__name__)

PHOTO_LIMIT = 3
_PHOTO_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class MatchCard:
    listing: ListingRow
    extraction: ExtractionRow
    photos: list[bytes]


@dataclass(frozen=True)
class ChangeCard:
    listing: ListingRow
    kind: str  # "price_change" | "removed"
    old_price_cents: int | None
    new_price_cents: int | None


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    images: list[tuple[str, bytes]]


def _dollars(cents: int | None) -> str:
    return "—" if cents is None else f"${cents / 100:,.0f}"


def _yes_no(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _hours(attributes: dict) -> str:
    hours = attributes.get("core", {}).get("engine_hours")
    return "—" if hours is None else f"{hours:,}"


def _attribute_rows(attributes: dict) -> list[tuple[str, str]]:
    core = attributes.get("core", {})
    specs = attributes.get("specs", {})
    condition = attributes.get("condition", {})
    deal = attributes.get("deal", {})
    return [
        ("Hours", _hours(attributes)),
        ("Year", _yes_no(core.get("year"))),
        ("Cab", _yes_no(specs.get("cab_enclosed"))),
        ("A/C", _yes_no(specs.get("has_ac"))),
        ("2-speed", _yes_no(specs.get("two_speed"))),
        ("High flow", _yes_no(specs.get("high_flow"))),
        ("Undercarriage", _yes_no(specs.get("undercarriage_condition"))),
        ("Runs", _yes_no(condition.get("runs"))),
        ("Issues", ", ".join(condition.get("stated_issues") or []) or "none stated"),
        ("Attachments", ", ".join(deal.get("attachments") or []) or "none stated"),
        ("Seller", _yes_no(deal.get("seller_type"))),
    ]


_TEMPLATE = """\
<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;
                   color:#1c1e21;max-width:680px;margin:0 auto;padding:16px">
{% macro card(item) %}
  <div style="border:1px solid #dddfe2;border-radius:8px;padding:16px;margin-bottom:20px">
    <h2 style="margin:0 0 4px;font-size:18px">{{ item.title }}</h2>
    <div style="font-size:22px;font-weight:600;margin-bottom:2px">{{ item.price }}</div>
    <div style="color:#65676b;font-size:13px;margin-bottom:12px">{{ item.location }}</div>
    {% if item.unknowns %}
      <div style="background:#fff3cd;border-radius:6px;padding:8px 10px;margin-bottom:12px;
                  font-size:13px">
        Unverified — not stated in the listing: {{ item.unknowns }}
      </div>
    {% endif %}
    {% if item.cids %}
      <div style="margin-bottom:12px">
        {% for cid in item.cids %}
          <img src="cid:{{ cid }}" style="max-width:200px;border-radius:6px;
                                          margin-right:6px;vertical-align:top">
        {% endfor %}
      </div>
    {% endif %}
    <table style="border-collapse:collapse;font-size:13px;margin-bottom:12px">
      {% for label, value in item.rows %}
        <tr>
          <td style="padding:3px 14px 3px 0;color:#65676b;white-space:nowrap">{{ label }}</td>
          <td style="padding:3px 0">{{ value }}</td>
        </tr>
      {% endfor %}
    </table>
    <div style="font-size:13px;font-style:italic;color:#65676b;margin-bottom:14px">
      {{ item.reasoning }}
    </div>
    <a href="{{ item.url }}"
       style="display:inline-block;background:#1877f2;color:#fff;text-decoration:none;
              padding:9px 18px;border-radius:6px;font-size:14px">View on Marketplace</a>
  </div>
{% endmacro %}

{% if matches %}
  <h1 style="font-size:20px">{{ matches|length }} new
    match{{ '' if matches|length == 1 else 'es' }}</h1>
  {% for item in matches %}{{ card(item) }}{% endfor %}
{% endif %}

{% if unverified %}
  <h1 style="font-size:20px">Unverified</h1>
  <p style="font-size:13px;color:#65676b">
    These cleared price and keyword filters, but the listing does not state
    everything your criteria ask about.
  </p>
  {% for item in unverified %}{{ card(item) }}{% endfor %}
{% endif %}

{% if changes %}
  <h1 style="font-size:20px">Watched listings</h1>
  {% for change in changes %}
    <div style="border-left:3px solid #1877f2;padding:8px 0 8px 12px;margin-bottom:14px">
      <div style="font-weight:600">{{ change.headline }}</div>
      <div style="font-size:13px;color:#65676b;margin-bottom:6px">{{ change.detail }}</div>
      <a href="{{ change.url }}" style="font-size:13px;color:#1877f2">View on Marketplace</a>
    </div>
  {% endfor %}
{% endif %}
</body></html>
"""

_ENV = Environment(autoescape=select_autoescape(default=True, default_for_string=True))


def _card_context(card: MatchCard) -> tuple[dict, list[tuple[str, bytes]]]:
    images: list[tuple[str, bytes]] = []
    cids: list[str] = []
    for photo in card.photos:
        cid = f"photo-{uuid.uuid4().hex}"
        cids.append(cid)
        images.append((cid, photo))

    context = {
        "title": card.listing.title,
        "price": _dollars(card.listing.price_cents),
        "location": card.listing.location or "location not stated",
        "url": card.listing.url,
        "rows": _attribute_rows(card.extraction.attributes),
        "reasoning": card.extraction.reasoning,
        "unknowns": ", ".join(card.extraction.unknowns) if card.extraction.unknowns else "",
        "cids": cids,
    }
    return context, images


def _change_context(change: ChangeCard) -> dict:
    if change.kind == "removed":
        return {
            "headline": f"Listing removed (likely sold): {change.listing.title}",
            "detail": f"Last seen at {_dollars(change.old_price_cents)}.",
            "url": change.listing.url,
        }
    direction = "Price drop" if (
        change.old_price_cents is not None
        and change.new_price_cents is not None
        and change.new_price_cents < change.old_price_cents
    ) else "Price change"
    return {
        "headline": f"{direction}: {change.listing.title}",
        "detail": f"{_dollars(change.old_price_cents)} → {_dollars(change.new_price_cents)}",
        "url": change.listing.url,
    }


def _subject(n_matches: int, n_unverified: int, n_changes: int) -> str:
    parts: list[str] = []
    if n_matches:
        parts.append(f"{n_matches} new match{'' if n_matches == 1 else 'es'}")
    if n_unverified:
        parts.append(f"{n_unverified} unverified")
    if n_changes:
        parts.append(f"{n_changes} price change{'' if n_changes == 1 else 's'}")
    return "MarketSearch: " + (", ".join(parts) if parts else "nothing new")


def render_email(
    matches: list[MatchCard], unverified: list[MatchCard], changes: list[ChangeCard]
) -> RenderedEmail:
    images: list[tuple[str, bytes]] = []

    match_ctx = []
    for card in matches:
        context, card_images = _card_context(card)
        match_ctx.append(context)
        images.extend(card_images)

    unverified_ctx = []
    for card in unverified:
        context, card_images = _card_context(card)
        unverified_ctx.append(context)
        images.extend(card_images)

    html = _ENV.from_string(_TEMPLATE).render(
        matches=match_ctx,
        unverified=unverified_ctx,
        changes=[_change_context(c) for c in changes],
    )
    return RenderedEmail(
        subject=_subject(len(matches), len(unverified), len(changes)),
        html=html,
        images=images,
    )


def render_sms(
    matches: list[MatchCard], unverified: list[MatchCard], changes: list[ChangeCard]
) -> str:
    parts: list[str] = []
    if matches:
        names = sorted({c.listing.search_name for c in matches})
        parts.append(f"{len(matches)} new match{'' if len(matches) == 1 else 'es'} "
                     f"({', '.join(names)})")
    if unverified:
        parts.append(f"{len(unverified)} unverified")
    if changes:
        parts.append(f"{len(changes)} change{'' if len(changes) == 1 else 's'}")
    body = ", ".join(parts) if parts else "activity"
    return f"MarketSearch: {body} — check email."[:160]


def download_photos(
    urls: list[str], limit: int = PHOTO_LIMIT, get: Callable[[str], bytes] | None = None
) -> list[bytes]:
    """Fetch up to `limit` photos. A failure drops that photo rather than the
    whole alert — a card without pictures still tells you what you need."""
    if get is None:
        import httpx

        def get(url: str) -> bytes:  # type: ignore[misc]
            response = httpx.get(url, timeout=_PHOTO_TIMEOUT_S, follow_redirects=True)
            response.raise_for_status()
            return response.content

    photos: list[bytes] = []
    for url in urls[:limit]:
        try:
            photos.append(get(url))
        except Exception as exc:
            log.warning("photo download failed for %s: %s", url, exc)
    return photos
