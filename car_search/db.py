"""SQLite storage: current listings, price history and a log of browser captures."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pandas as pd

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    vin TEXT PRIMARY KEY,
    source TEXT, source_id TEXT, sources TEXT,
    price REAL, year INTEGER, make TEXT, model TEXT, trim TEXT, miles INTEGER,
    drivetrain TEXT, engine TEXT, body_type TEXT, exterior_color TEXT,
    url TEXT, other_urls TEXT,
    dealer_name TEXT, city TEXT, state TEXT, zip TEXT,
    latitude REAL, longitude REAL, distance_miles REAL, days_on_market INTEGER,
    accident_reported INTEGER, salvage_or_rebuilt INTEGER, repossession INTEGER,
    rental_history INTEGER, owners INTEGER, service_records INTEGER, certified INTEGER,
    first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1,
    market_price REAL, value_score REAL, deal_score REAL, score_notes TEXT, negotiable INTEGER,
    alerted_price REAL, alerted_at TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
    vin TEXT, price REAL, source TEXT, seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_history_vin ON price_history(vin);
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT, url TEXT, found INTEGER, saved INTEGER, new_cars INTEGER, alerts INTEGER
);
"""

LISTING_COLUMNS = [
    "vin", "source", "source_id", "price", "year", "make", "model", "trim", "miles",
    "drivetrain", "engine", "body_type", "exterior_color", "url", "dealer_name", "city",
    "state", "zip", "latitude", "longitude", "distance_miles", "days_on_market",
    "accident_reported", "salvage_or_rebuilt", "repossession", "rental_history", "owners",
    "service_records", "certified",
]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- listings -----------------------------------------------------------

    def upsert_listings(self, listings: list[Listing], sources_by_vin: dict[str, list[str]],
                        seen_at: str | None = None) -> tuple[int, int]:
        """Insert or refresh listings. Returns (new cars, price drops)."""
        seen_at = seen_at or now()
        new = drops = 0
        with self.conn:
            for l in listings:
                row = self.conn.execute("SELECT price FROM listings WHERE vin = ?", (l.vin,)).fetchone()
                values = {c: getattr(l, c) for c in LISTING_COLUMNS}
                values["sources"] = ",".join(sorted(set(sources_by_vin.get(l.vin, [l.source]))))
                values["other_urls"] = json.dumps(l.other_urls)
                values["last_seen"] = seen_at
                if row is None:
                    new += 1
                    values["first_seen"] = seen_at
                    cols = ", ".join(values)
                    marks = ", ".join("?" for _ in values)
                    self.conn.execute(f"INSERT INTO listings ({cols}) VALUES ({marks})", list(values.values()))
                else:
                    if l.price < row["price"]:
                        drops += 1
                    sets = ", ".join(f"{c} = ?" for c in values)
                    self.conn.execute(f"UPDATE listings SET {sets}, active = 1 WHERE vin = ?",
                                      [*values.values(), l.vin])
                last = self.conn.execute(
                    "SELECT price FROM price_history WHERE vin = ? ORDER BY seen_at DESC, rowid DESC LIMIT 1",
                    (l.vin,)).fetchone()
                if last is None or last["price"] != l.price:
                    self.conn.execute("INSERT INTO price_history VALUES (?, ?, ?, ?)",
                                      (l.vin, l.price, l.source, seen_at))
        return new, drops

    def mark_inactive(self, active_vins: set[str]) -> int:
        with self.conn:
            rows = self.conn.execute("SELECT vin FROM listings WHERE active = 1").fetchall()
            gone = [r["vin"] for r in rows if r["vin"] not in active_vins]
            self.conn.executemany("UPDATE listings SET active = 0 WHERE vin = ?", [(v,) for v in gone])
        return len(gone)

    def mark_stale(self, days: int) -> int:
        """Retire active cars not seen for `days` days."""
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")
        with self.conn:
            cur = self.conn.execute("UPDATE listings SET active = 0 WHERE active = 1 AND last_seen < ?", (cutoff,))
        return cur.rowcount

    def listings_frame(self, active_only: bool = False) -> pd.DataFrame:
        sql = "SELECT * FROM listings" + (" WHERE active = 1" if active_only else "")
        return pd.read_sql_query(sql, self.conn)

    def save_scores(self, scores: pd.DataFrame) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE listings SET market_price = ?, value_score = ?, deal_score = ?, "
                "score_notes = ?, negotiable = ? WHERE vin = ?",
                [(_num(r.market_price), _num(r.value_score), _num(r.deal_score),
                  json.dumps(r.score_notes), int(bool(r.negotiable)), r.vin)
                 for r in scores.itertuples()],
            )

    def mark_alerted(self, vins_prices: list[tuple[str, float]]) -> None:
        with self.conn:
            self.conn.executemany("UPDATE listings SET alerted_price = ?, alerted_at = ? WHERE vin = ?",
                                  [(p, now(), v) for v, p in vins_prices])

    def price_history(self, vin: str) -> pd.DataFrame:
        return pd.read_sql_query("SELECT price, source, seen_at FROM price_history WHERE vin = ? ORDER BY seen_at",
                                 self.conn, params=(vin,))

    # --- captures -----------------------------------------------------------

    def log_capture(self, url: str, found: int, saved: int, new: int, alerts: int) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO captures (captured_at, url, found, saved, new_cars, alerts) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (now(), url, found, saved, new, alerts))

    def captures_frame(self, limit: int = 20) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM captures ORDER BY id DESC LIMIT ?", self.conn, params=(limit,))


def _num(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)
