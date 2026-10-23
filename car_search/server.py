"""Local server the browser extension sends pages to: car-search serve.

Listens on 127.0.0.1 only. POSTs are accepted from browser extensions (and
local tools that send no Origin), never from ordinary web pages, so a site you
visit can't plant fake listings.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

from .alerts import text_or_blank
from .config import Config
from .db import Database
from .extract import extract
from .pipeline import ingest

log = logging.getLogger(__name__)

MAX_BODY = 5 * 1024 * 1024
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
DASHBOARD_URL = "http://localhost:8501"


def _car_summary(row) -> dict:
    score = row.get("deal_score")
    market = row.get("market_price")
    return {
        "vin": row["vin"],
        "title": " ".join(x for x in (str(row["year"]), row["make"], row["model"], text_or_blank(row.get("trim"))) if x),
        "price": float(row["price"]),
        "market_price": None if pd.isna(market) else float(market),
        "deal_score": None if pd.isna(score) else round(float(score), 4),
        "miles": int(row["miles"]),
        "url": text_or_blank(row.get("url")) or None,
        "negotiable": bool(row.get("negotiable") or False),
    }


def handle_capture(config: Config, page: dict, lock: threading.Lock) -> dict:
    listings = extract(page)
    with lock:
        db = Database(config.database)
        try:
            r = ingest(config, db, listings)
            frame = db.listings_frame()
            db.log_capture(page.get("url", ""), len(listings), len(r.saved_vins), r.new, len(r.alerts))
        finally:
            db.close()
    on_page = frame[frame["vin"].isin(r.saved_vins)]
    active = frame[frame["active"] == 1]
    return {
        "found": len(listings),
        "saved": len(r.saved_vins),
        "skipped": r.skipped,
        "new": r.new,
        "price_drops": r.price_drops,
        "cars": [_car_summary(row) for _, row in on_page.iterrows()],
        "deals": [_car_summary(row) for _, row in r.alerts.iterrows()],
        "total_active": int(len(active)),
        "scored": int(active["deal_score"].notna().sum()),
        "min_training_rows": config.scoring.min_training_rows,
    }


def status(config: Config) -> dict:
    db = Database(config.database)
    try:
        frame = db.listings_frame(active_only=True)
    finally:
        db.close()
    return {
        "ok": True,
        "total_active": int(len(frame)),
        "scored": int(frame["deal_score"].notna().sum()) if len(frame) else 0,
        "threshold": config.alerts.threshold,
        "dashboard": DASHBOARD_URL,
    }


def make_handler(config: Config, lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        def _allowed(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin.startswith(EXTENSION_ORIGINS)

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            origin = self.headers.get("Origin")
            if origin and origin.startswith(EXTENSION_ORIGINS):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):  # CORS preflight from the extension
            if not self._allowed():
                return self._send(403, {"error": "forbidden"})
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            if not self._allowed():
                return self._send(403, {"error": "forbidden"})
            if self.path == "/status":
                return self._send(200, status(config))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._allowed():
                return self._send(403, {"error": "only the browser extension may post here"})
            if self.path != "/capture":
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._send(413, {"error": "page too large or empty"})
            try:
                page = json.loads(self.rfile.read(length))
                result = handle_capture(config, page, lock)
            except json.JSONDecodeError:
                return self._send(400, {"error": "bad JSON"})
            except Exception as exc:
                log.exception("capture failed")
                return self._send(500, {"error": str(exc)})
            if result["found"]:
                log.warning("%s: %d cars found, %d match your search, %d new, %d deals",
                            page.get("url", "")[:80], result["found"], result["saved"],
                            result["new"], len(result["deals"]))
            self._send(200, result)

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler


def serve(config: Config, port: int | None = None) -> None:
    port = port or config.server_port
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(config, threading.Lock()))
    print(f"car-search is listening on http://127.0.0.1:{port} - browse car listings and they'll be saved.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
