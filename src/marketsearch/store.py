"""SQLite persistence.

Every listing id ever seen is recorded, matched or not. That ledger is what
guarantees a listing is examined once and alerted on at most once. Nothing is
ever pruned — full history costs a few megabytes after years and is worth more
than the disk.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from marketsearch.models import ListingDetail, RawListing

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS listings (
    listing_id     TEXT PRIMARY KEY,
    search_name    TEXT NOT NULL,
    title          TEXT NOT NULL,
    price_cents    INTEGER,
    location       TEXT,
    url            TEXT NOT NULL,
    thumbnail_url  TEXT,
    seller_name    TEXT,
    fingerprint    TEXT NOT NULL,
    stage          TEXT NOT NULL DEFAULT 'pending',
    reject_reason  TEXT,
    watched        INTEGER NOT NULL DEFAULT 0,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    last_change_check_at TEXT,
    extraction_attempts INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_listings_fingerprint ON listings(fingerprint);
CREATE INDEX IF NOT EXISTS idx_listings_watched ON listings(watched);

CREATE TABLE IF NOT EXISTS listing_details (
    listing_id        TEXT PRIMARY KEY REFERENCES listings(listing_id),
    description       TEXT NOT NULL,
    structured_fields TEXT NOT NULL,
    photo_urls        TEXT NOT NULL,
    distance_miles    REAL,
    content_hash      TEXT NOT NULL,
    fetched_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id    TEXT NOT NULL REFERENCES listings(listing_id),
    attributes    TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    confidence    REAL NOT NULL,
    reasoning     TEXT NOT NULL,
    unknowns      TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_cents    REAL NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_extractions_listing ON extractions(listing_id, id DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL,
    channel    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    status     TEXT NOT NULL,
    sent_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_lookup
    ON notifications(listing_id, channel, kind, status);

CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    found       INTEGER NOT NULL DEFAULT 0,
    new         INTEGER NOT NULL DEFAULT 0,
    prefiltered INTEGER NOT NULL DEFAULT 0,
    extracted   INTEGER NOT NULL DEFAULT 0,
    matched     INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ListingRow:
    listing_id: str
    search_name: str
    title: str
    price_cents: int | None
    location: str | None
    url: str
    thumbnail_url: str | None
    seller_name: str | None
    fingerprint: str
    stage: str
    reject_reason: str | None
    watched: bool
    first_seen_at: str
    last_seen_at: str
    last_change_check_at: str | None
    extraction_attempts: int


@dataclass(frozen=True)
class ExtractionRow:
    listing_id: str
    attributes: dict
    verdict: str
    confidence: float
    reasoning: str
    unknowns: list[str]
    model: str
    created_at: str


def _row_to_listing(row: sqlite3.Row) -> ListingRow:
    return ListingRow(
        listing_id=row["listing_id"],
        search_name=row["search_name"],
        title=row["title"],
        price_cents=row["price_cents"],
        location=row["location"],
        url=row["url"],
        thumbnail_url=row["thumbnail_url"],
        seller_name=row["seller_name"],
        fingerprint=row["fingerprint"],
        stage=row["stage"],
        reject_reason=row["reject_reason"],
        watched=bool(row["watched"]),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        last_change_check_at=row["last_change_check_at"],
        extraction_attempts=row["extraction_attempts"],
    )


class Store:
    """Owns the SQLite connection. One instance per process run."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def initialize(self) -> None:
        self._conn.executescript(_SCHEMA)
        cur = self._conn.execute("SELECT version FROM schema_version")
        if cur.fetchone() is None:
            self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        self._conn.commit()

    # ---- listing ledger -------------------------------------------------

    def known_listing_ids(self, ids: Iterable[str]) -> set[str]:
        """Return the subset of `ids` already present. Chunked to stay under
        SQLite's variable limit (default 999)."""
        ids = list(ids)
        found: set[str] = set()
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = self._conn.execute(
                f"SELECT listing_id FROM listings WHERE listing_id IN ({placeholders})",
                chunk,
            )
            found.update(r["listing_id"] for r in cur.fetchall())
        return found

    def upsert_listing(self, listing: RawListing, search_name: str, fp: str) -> None:
        now = utcnow()
        self._conn.execute(
            """
            INSERT INTO listings (listing_id, search_name, title, price_cents, location,
                                  url, thumbnail_url, seller_name, fingerprint,
                                  first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                title = excluded.title,
                price_cents = excluded.price_cents,
                location = excluded.location,
                thumbnail_url = excluded.thumbnail_url,
                seller_name = excluded.seller_name,
                last_seen_at = excluded.last_seen_at
            """,
            (
                listing.listing_id, search_name, listing.title, listing.price_cents,
                listing.location, listing.url, listing.thumbnail_url,
                listing.seller_name, fp, now, now,
            ),
        )
        self._conn.commit()

    def set_stage(self, listing_id: str, stage: str, reject_reason: str | None = None) -> None:
        self._conn.execute(
            "UPDATE listings SET stage = ?, reject_reason = ? WHERE listing_id = ?",
            (stage, reject_reason, listing_id),
        )
        self._conn.commit()

    def update_price(self, listing_id: str, price_cents: int | None) -> None:
        self._conn.execute(
            "UPDATE listings SET price_cents = ?, last_seen_at = ? WHERE listing_id = ?",
            (price_cents, utcnow(), listing_id),
        )
        self._conn.commit()

    def bump_attempts(self, listing_id: str) -> int:
        self._conn.execute(
            "UPDATE listings SET extraction_attempts = extraction_attempts + 1"
            " WHERE listing_id = ?",
            (listing_id,),
        )
        self._conn.commit()
        return self.attempts(listing_id)

    def attempts(self, listing_id: str) -> int:
        cur = self._conn.execute(
            "SELECT extraction_attempts FROM listings WHERE listing_id = ?", (listing_id,)
        )
        row = cur.fetchone()
        return int(row["extraction_attempts"]) if row else 0

    def pending_listings(self, search_name: str) -> list[RawListing]:
        """Listings awaiting a retry. Excludes 'failed', so a listing that has
        exhausted its attempts is never picked up again."""
        cur = self._conn.execute(
            "SELECT * FROM listings WHERE search_name = ? AND stage = 'pending'",
            (search_name,),
        )
        return [
            RawListing(
                listing_id=row["listing_id"], title=row["title"],
                price_cents=row["price_cents"], location=row["location"],
                url=row["url"], thumbnail_url=row["thumbnail_url"],
                seller_name=row["seller_name"],
            )
            for row in cur.fetchall()
        ]

    def get_listing(self, listing_id: str) -> ListingRow | None:
        cur = self._conn.execute("SELECT * FROM listings WHERE listing_id = ?", (listing_id,))
        row = cur.fetchone()
        return _row_to_listing(row) if row else None

    def fingerprint_seen_before(
        self, fp: str, exclude_listing_id: str, within_days: int
    ) -> bool:
        """True if some *other* listing with this fingerprint was first seen
        inside the window — i.e. this is a repost we should not re-alert on."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
        cur = self._conn.execute(
            """
            SELECT 1 FROM listings
            WHERE fingerprint = ? AND listing_id != ? AND first_seen_at >= ?
            LIMIT 1
            """,
            (fp, exclude_listing_id, cutoff),
        )
        return cur.fetchone() is not None

    # ---- listing details -----------------------------------------------

    def save_detail(self, detail: ListingDetail, content_hash: str) -> None:
        self._conn.execute(
            """
            INSERT INTO listing_details (listing_id, description, structured_fields,
                                         photo_urls, distance_miles, content_hash, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                description = excluded.description,
                structured_fields = excluded.structured_fields,
                photo_urls = excluded.photo_urls,
                distance_miles = excluded.distance_miles,
                content_hash = excluded.content_hash,
                fetched_at = excluded.fetched_at
            """,
            (
                detail.listing_id, detail.description,
                json.dumps(detail.structured_fields), json.dumps(detail.photo_urls),
                detail.distance_miles, content_hash, utcnow(),
            ),
        )
        self._conn.commit()

    def get_detail(self, listing_id: str) -> ListingDetail | None:
        cur = self._conn.execute(
            "SELECT * FROM listing_details WHERE listing_id = ?", (listing_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ListingDetail(
            listing_id=row["listing_id"],
            description=row["description"],
            structured_fields=json.loads(row["structured_fields"]),
            photo_urls=json.loads(row["photo_urls"]),
            distance_miles=row["distance_miles"],
        )

    def get_detail_content_hash(self, listing_id: str) -> str | None:
        cur = self._conn.execute(
            "SELECT content_hash FROM listing_details WHERE listing_id = ?", (listing_id,)
        )
        row = cur.fetchone()
        return row["content_hash"] if row else None

    # ---- extractions ----------------------------------------------------

    def save_extraction(
        self, listing_id: str, attributes: dict, verdict: str, confidence: float,
        reasoning: str, unknowns: list[str], model: str, input_tokens: int,
        output_tokens: int, cost_cents: float,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO extractions (listing_id, attributes, verdict, confidence, reasoning,
                                     unknowns, model, input_tokens, output_tokens,
                                     cost_cents, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing_id, json.dumps(attributes), verdict, confidence, reasoning,
                json.dumps(unknowns), model, input_tokens, output_tokens,
                cost_cents, utcnow(),
            ),
        )
        self._conn.commit()

    def latest_extraction(self, listing_id: str) -> ExtractionRow | None:
        cur = self._conn.execute(
            "SELECT * FROM extractions WHERE listing_id = ? ORDER BY id DESC LIMIT 1",
            (listing_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ExtractionRow(
            listing_id=row["listing_id"],
            attributes=json.loads(row["attributes"]),
            verdict=row["verdict"],
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            unknowns=json.loads(row["unknowns"]),
            model=row["model"],
            created_at=row["created_at"],
        )

    # ---- watched (mirrors Facebook's saved list) -------------------------

    def set_watched_ids(self, ids: set[str]) -> None:
        """Make the DB reflect Facebook's saved list exactly. Facebook is the
        source of truth, so un-saving there clears the flag here."""
        self._conn.execute("UPDATE listings SET watched = 0 WHERE watched = 1")
        for listing_id in ids:
            self._conn.execute(
                "UPDATE listings SET watched = 1 WHERE listing_id = ?", (listing_id,)
            )
        self._conn.commit()

    def watched_listing_ids(self) -> set[str]:
        cur = self._conn.execute("SELECT listing_id FROM listings WHERE watched = 1")
        return {r["listing_id"] for r in cur.fetchall()}

    # ---- notifications --------------------------------------------------

    def already_notified(self, listing_id: str, channel: str, kind: str) -> bool:
        cur = self._conn.execute(
            """
            SELECT 1 FROM notifications
            WHERE listing_id = ? AND channel = ? AND kind = ? AND status = 'sent'
            LIMIT 1
            """,
            (listing_id, channel, kind),
        )
        return cur.fetchone() is not None

    def record_notification(
        self, listing_id: str, channel: str, kind: str, status: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO notifications (listing_id, channel, kind, status, sent_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (listing_id, channel, kind, status, utcnow()),
        )
        self._conn.commit()

    # ---- runs ------------------------------------------------------------

    def start_run(self) -> int:
        cur = self._conn.execute("INSERT INTO runs (started_at) VALUES (?)", (utcnow(),))
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, counters: dict[str, int]) -> None:
        self._conn.execute(
            """
            UPDATE runs SET ended_at = ?, found = ?, new = ?, prefiltered = ?,
                            extracted = ?, matched = ?, errors = ?
            WHERE run_id = ?
            """,
            (
                utcnow(), counters.get("found", 0), counters.get("new", 0),
                counters.get("prefiltered", 0), counters.get("extracted", 0),
                counters.get("matched", 0), counters.get("errors", 0), run_id,
            ),
        )
        self._conn.commit()

    # ---- key/value state -------------------------------------------------

    def get_state(self, key: str) -> str | None:
        cur = self._conn.execute("SELECT value FROM app_state WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()
