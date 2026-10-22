"""SQLite storage: current listings, price history, API caches and run log."""

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
    recall_count INTEGER,
    first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1,
    market_price REAL, value_score REAL, deal_score REAL, score_notes TEXT, negotiable INTEGER,
    alerted_price REAL, alerted_at TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
    vin TEXT, price REAL, source TEXT, seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_history_vin ON price_history(vin);
CREATE TABLE IF NOT EXISTS vin_decodes (vin TEXT PRIMARY KEY, data TEXT, decoded_at TEXT);
CREATE TABLE IF NOT EXISTS recalls (
    make TEXT, model TEXT, year INTEGER, data TEXT, fetched_at TEXT,
    PRIMARY KEY (make, model, year)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT,
    fetched INTEGER, unique_cars INTEGER, new_cars INTEGER, price_drops INTEGER, alerts INTEGER,
    errors TEXT
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

    def set_recall_count(self, vin: str, count: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE listings SET recall_count = ? WHERE vin = ?", (count, vin))

    def mark_alerted(self, vins_prices: list[tuple[str, float]]) -> None:
        with self.conn:
            self.conn.executemany("UPDATE listings SET alerted_price = ?, alerted_at = ? WHERE vin = ?",
                                  [(p, now(), v) for v, p in vins_prices])

    def price_history(self, vin: str) -> pd.DataFrame:
        return pd.read_sql_query("SELECT price, source, seen_at FROM price_history WHERE vin = ? ORDER BY seen_at",
                                 self.conn, params=(vin,))

    # --- caches -------------------------------------------------------------

    def cached_decodes(self, vins: list[str]) -> dict[str, dict]:
        if not vins:
            return {}
        marks = ",".join("?" for _ in vins)
        rows = self.conn.execute(f"SELECT vin, data FROM vin_decodes WHERE vin IN ({marks})", vins)
        return {r["vin"]: json.loads(r["data"]) for r in rows}

    def save_decodes(self, decoded: dict[str, dict]) -> None:
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO vin_decodes VALUES (?, ?, ?)",
                                  [(v, json.dumps(d), now()) for v, d in decoded.items()])

    def cached_recalls(self, make: str, model: str, year: int, max_age_days: int = 7) -> list[dict] | None:
        row = self.conn.execute("SELECT data, fetched_at FROM recalls WHERE make = ? AND model = ? AND year = ?",
                                (make, model, year)).fetchone()
        if row is None:
            return None
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(row["fetched_at"])
        return json.loads(row["data"]) if age.days < max_age_days else None

    def save_recalls(self, make: str, model: str, year: int, recalls: list[dict]) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO recalls VALUES (?, ?, ?, ?, ?)",
                              (make, model, year, json.dumps(recalls), now()))

    def recalls_for(self, make: str, model: str, year: int) -> list[dict]:
        row = self.conn.execute("SELECT data FROM recalls WHERE make = ? AND model = ? AND year = ?",
                                (make, model, year)).fetchone()
        return json.loads(row["data"]) if row else []

    # --- runs ---------------------------------------------------------------

    def log_run(self, started_at: str, fetched: int, unique: int, new: int, drops: int,
                alerts: int, errors: list[str]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs (started_at, finished_at, fetched, unique_cars, new_cars, price_drops, alerts, errors) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (started_at, now(), fetched, unique, new, drops, alerts, json.dumps(errors)))

    def runs_frame(self, limit: int = 20) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", self.conn, params=(limit,))


def _num(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)
