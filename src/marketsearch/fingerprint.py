"""Content fingerprinting for relist suppression.

Sellers routinely delete and repost an unsold machine, and Facebook assigns a
brand-new listing id each time. Pure id-based dedupe would therefore re-alert
on the same Bobcat every few weeks.

Price is deliberately part of the digest: a repost at a lower price produces a
different fingerprint and *does* alert, because a price drop is news.
"""

from __future__ import annotations

import hashlib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise(value: str | None) -> str:
    if not value:
        return ""
    return _NON_ALNUM.sub(" ", value.lower()).strip()


def fingerprint(
    title: str,
    price_cents: int | None,
    seller_name: str | None,
    location: str | None,
) -> str:
    """Return a stable 32-char digest identifying this machine-at-this-price."""
    parts = [
        _normalise(title),
        "" if price_cents is None else str(price_cents),
        _normalise(seller_name),
        _normalise(location),
    ]
    joined = "\x1f".join(parts).encode("utf-8")
    return hashlib.blake2b(joined, digest_size=16).hexdigest()
