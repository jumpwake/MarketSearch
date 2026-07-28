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

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS listings (
    listing_id     TEXT PRIMARY KEY,
    watchlist_name TEXT,
    model_name     TEXT,
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


_last_now = datetime.min.replace(tzinfo=timezone.utc)


def utcnow() -> str:
    """A UTC timestamp that is always strictly greater than the previous one.

    Windows' clock granularity is around 15 ms — coarse enough that a whole run
    (start, extract, finish) can share a single tick. Several queries here
    compare ISO timestamps as ordered strings, and a run whose start and end
    are equal produces a zero-width window that wrongly captures a neighbouring
    run's rows. Nudging forward by a microsecond keeps event order well defined
    without depending on how fine the platform clock happens to be.
    """
    global _last_now
    now = datetime.now(timezone.utc)
    if now <= _last_now:
        now = _last_now + timedelta(microseconds=1)
    _last_now = now
    return now.isoformat()


@dataclass(frozen=True)
class ListingRow:
    listing_id: str
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
    watchlist_name: str | None = None
    model_name: str | None = None


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
        watchlist_name=row["watchlist_name"],
        model_name=row["model_name"],
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
        row = cur.fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            self._conn.commit()
            return
        self._migrate(row["version"])

    def _migrate(self, version: int) -> None:
        """Bring an existing database up to SCHEMA_VERSION, in place.

        Never rebuild: the extractions in here cost real money to produce.
        """
        if version >= SCHEMA_VERSION:
            return

        if version < 2:
            columns = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(listings)")
            }
            if "watchlist_name" not in columns:
                self._conn.execute(
                    "ALTER TABLE listings ADD COLUMN watchlist_name TEXT"
                )
            if "model_name" not in columns:
                self._conn.execute("ALTER TABLE listings ADD COLUMN model_name TEXT")
            # v1 stored the claiming search's name. That name was always the
            # model in practice; only the grapple search belonged elsewhere.
            # Both columns are filled together, so a row missing either one is
            # half-populated and must be backfilled rather than skipped.
            self._conn.execute(
                """
                UPDATE listings
                   SET model_name = search_name,
                       watchlist_name = CASE WHEN search_name = 'root-grapple'
                                             THEN 'attachments'
                                             ELSE 'track-loaders' END
                 WHERE model_name IS NULL OR watchlist_name IS NULL
                """
            )

        if version < 3:
            columns = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(listings)")
            }
            if "search_name" in columns:
                # SQLite 3.35+ supports DROP COLUMN; Python 3.12 ships 3.4x.
                self._conn.execute("ALTER TABLE listings DROP COLUMN search_name")

        self._conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
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

    def upsert_listing(
        self,
        listing: RawListing,
        fp: str,
        watchlist_name: str | None = None,
        model_name: str | None = None,
    ) -> None:
        now = utcnow()
        self._conn.execute(
            """
            INSERT INTO listings (listing_id, watchlist_name, model_name,
                                  title, price_cents, location,
                                  url, thumbnail_url, seller_name, fingerprint,
                                  first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_id) DO UPDATE SET
                title = excluded.title,
                price_cents = excluded.price_cents,
                location = excluded.location,
                thumbnail_url = excluded.thumbnail_url,
                seller_name = excluded.seller_name,
                last_seen_at = excluded.last_seen_at
            """,
            (
                listing.listing_id, watchlist_name, model_name,
                listing.title, listing.price_cents,
                listing.location, listing.url, listing.thumbnail_url,
                listing.seller_name, fp, now, now,
            ),
        )
        self._conn.commit()

    def reassign(
        self, listing_id: str, watchlist_name: str, model_name: str
    ) -> None:
        """Move a listing to the watchlist and model that now accept it."""
        self._conn.execute(
            "UPDATE listings SET watchlist_name = ?, model_name = ? WHERE listing_id = ?",
            (watchlist_name, model_name, listing_id),
        )
        self._conn.commit()

    def prefiltered_listings(self) -> list[ListingRow]:
        """Every listing rejected before extraction — the requeue corpus."""
        cur = self._conn.execute(
            "SELECT * FROM listings WHERE stage = 'prefiltered_out'"
            " ORDER BY first_seen_at"
        )
        return [_row_to_listing(row) for row in cur.fetchall()]

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

    def pending_listings(self) -> list[RawListing]:
        """Listings awaiting a retry. Excludes 'failed', so a listing that has
        exhausted its attempts is never picked up again.

        Not scoped by watchlist: assignment is recomputed on every pass, so the
        stored one is not an input to the retry decision.
        """
        cur = self._conn.execute("SELECT * FROM listings WHERE stage = 'pending'")
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

    def recent_runs(self, limit: int) -> list[sqlite3.Row]:
        """Most recent runs, newest first. Public so the CLI does not have to
        reach into the connection."""
        cur = self._conn.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return cur.fetchone()

    def latest_run_id(self) -> int | None:
        cur = self._conn.execute("SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1")
        row = cur.fetchone()
        return int(row["run_id"]) if row else None

    def extractions_between(
        self, start: str, end: str
    ) -> list[tuple[ListingRow, ExtractionRow]]:
        """Every extraction produced inside a run's window, newest first."""
        cur = self._conn.execute(
            """
            SELECT e.id AS extraction_id, l.listing_id AS lid
            FROM extractions e JOIN listings l ON l.listing_id = e.listing_id
            WHERE e.created_at >= ? AND e.created_at <= ?
            ORDER BY e.id DESC
            """,
            (start, end),
        )
        out: list[tuple[ListingRow, ExtractionRow]] = []
        seen: set[str] = set()
        for row in cur.fetchall():
            listing_id = row["lid"]
            if listing_id in seen:
                continue
            seen.add(listing_id)
            listing = self.get_listing(listing_id)
            extraction = self.latest_extraction(listing_id)
            if listing is not None and extraction is not None:
                out.append((listing, extraction))
        return out

    def listings_with_details(
        self, model_name: str | None, since: str
    ) -> list[tuple[ListingRow, ListingDetail, ExtractionRow | None]]:
        """Stored listings that have a saved detail — the replay corpus."""
        sql = (
            "SELECT l.listing_id AS lid FROM listings l"
            " JOIN listing_details d ON d.listing_id = l.listing_id"
            " WHERE l.first_seen_at >= ?"
        )
        params: list[object] = [since]
        if model_name is not None:
            sql += " AND l.model_name = ?"
            params.append(model_name)
        sql += " ORDER BY l.first_seen_at DESC"

        out = []
        for row in self._conn.execute(sql, params).fetchall():
            listing = self.get_listing(row["lid"])
            detail = self.get_detail(row["lid"])
            if listing is not None and detail is not None:
                out.append((listing, detail, self.latest_extraction(row["lid"])))
        return out

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
