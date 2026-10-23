"""Take captured listings through dedupe -> filter -> store -> score -> alert."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import pandas as pd

from .alerts import dispatch, select_alerts
from .config import Config
from .db import Database
from .dedupe import dedupe
from .models import Listing
from .scoring import score_listings
from .sources.base import matches
from .sources.sample import haversine_miles

log = logging.getLogger(__name__)

TRAINING_WINDOW_DAYS = 90


@dataclass
class IngestResult:
    received: int = 0
    unique: int = 0
    skipped: int = 0  # outside your search (model, year, price, miles, distance)
    new: int = 0
    price_drops: int = 0
    went_inactive: int = 0
    saved_vins: list[str] = field(default_factory=list)
    alerts: pd.DataFrame = field(default_factory=pd.DataFrame)
    alert_channels: list[str] = field(default_factory=list)
    model_info: dict = field(default_factory=dict)


def ingest(config: Config, db: Database, listings: list[Listing], send_alerts: bool = True,
           complete_snapshot: bool = False) -> IngestResult:
    """Store listings and rescore.

    Browser captures only show part of the market, so cars are retired after
    `stale_days` without being seen. `complete_snapshot=True` (the demo source)
    instead retires every car missing from this batch.
    """
    result = IngestResult(received=len(listings))
    sources_by_vin: dict[str, list[str]] = {}
    for l in listings:
        sources_by_vin.setdefault(l.vin, []).append(l.source)
    cars = dedupe(listings)
    result.unique = len(cars)

    center = config.search.center
    for car in cars:
        if car.distance_miles is None and center and car.latitude is not None and car.longitude is not None:
            car.distance_miles = round(haversine_miles(*center, car.latitude, car.longitude), 1)
    kept = [c for c in cars if matches(c, config.search)]
    result.skipped = len(cars) - len(kept)
    result.saved_vins = [c.vin for c in kept]

    result.new, result.price_drops = db.upsert_listings(kept, sources_by_vin)
    if complete_snapshot:
        result.went_inactive = db.mark_inactive({c.vin for c in kept})
    else:
        result.went_inactive = db.mark_stale(config.stale_days)

    result.model_info = rescore(config, db)

    if send_alerts:
        alerts = select_alerts(db.listings_frame(active_only=True), config.alerts.threshold,
                               config.alerts.realert_drop)
        result.alerts = alerts
        result.alert_channels = dispatch(config.alerts, alerts)
        if len(alerts):
            # The browser notification is always shown for these, so they count as sent.
            db.mark_alerted(list(zip(alerts["vin"], alerts["price"])))
    return result


def rescore(config: Config, db: Database) -> dict:
    frame = db.listings_frame()
    if frame.empty:
        return {}
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=TRAINING_WINDOW_DAYS)).isoformat()
    frame = frame[(frame["active"] == 1) | (frame["last_seen"] >= cutoff)]
    scores, info = score_listings(frame, config.scoring)
    db.save_scores(scores)
    return info
