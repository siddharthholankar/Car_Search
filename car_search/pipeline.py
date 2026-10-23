"""One collection run: fetch -> dedupe -> enrich -> store -> score -> alert."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import pandas as pd
import requests

from . import nhtsa
from .alerts import dispatch, select_alerts
from .config import Config
from .db import Database, now
from .dedupe import dedupe
from .models import Listing
from .scoring import score_listings
from .sources import Source, build_sources

log = logging.getLogger(__name__)

TRAINING_WINDOW_DAYS = 90


@dataclass
class RunResult:
    fetched: int = 0
    unique: int = 0
    new: int = 0
    price_drops: int = 0
    went_inactive: int = 0
    alerts: pd.DataFrame = field(default_factory=pd.DataFrame)
    alert_channels: list[str] = field(default_factory=list)
    model_info: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def run(config: Config, db: Database, sources: list[Source] | None = None,
        send_alerts: bool = True) -> RunResult:
    started = now()
    result = RunResult()
    sources = sources if sources is not None else build_sources(config)

    raw: list[Listing] = []
    succeeded = 0
    for source in sources:
        try:
            got = source.fetch(config.search)
            log.info("%s: %d listings", source.name, len(got))
            raw.extend(got)
            succeeded += 1
        except Exception as exc:
            log.error("%s failed: %s", source.name, exc)
            result.errors.append(f"{source.name}: {exc}")

    result.fetched = len(raw)
    sources_by_vin: dict[str, list[str]] = {}
    for l in raw:
        sources_by_vin.setdefault(l.vin, []).append(l.source)
    cars = dedupe(raw)
    result.unique = len(cars)

    if config.decode_vins:
        _enrich_vins(db, cars, result)

    result.new, result.price_drops = db.upsert_listings(cars, sources_by_vin)
    # Only retire cars when every source answered, so an outage doesn't wipe the board.
    if succeeded == len(sources) and sources:
        result.went_inactive = db.mark_inactive({c.vin for c in cars})

    if config.recalls:
        _attach_recalls(db, cars, result)

    rescore(config, db, result)

    if send_alerts:
        alerts = select_alerts(db.listings_frame(active_only=True), config.alerts.threshold,
                               config.alerts.realert_drop)
        result.alerts = alerts
        result.alert_channels = dispatch(config.alerts, alerts)
        if result.alert_channels:
            db.mark_alerted(list(zip(alerts["vin"], alerts["price"])))

    db.log_run(started, result.fetched, result.unique, result.new, result.price_drops,
               len(result.alerts), result.errors)
    return result


def rescore(config: Config, db: Database, result: RunResult | None = None) -> dict:
    frame = db.listings_frame()
    if frame.empty:
        return {}
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=TRAINING_WINDOW_DAYS)).isoformat()
    frame = frame[(frame["active"] == 1) | (frame["last_seen"] >= cutoff)]
    scores, info = score_listings(frame, config.scoring)
    db.save_scores(scores)
    if result is not None:
        result.model_info = info
    return info


def _enrich_vins(db: Database, cars: list[Listing], result: RunResult) -> None:
    """Fill trim/drivetrain/engine gaps from NHTSA vPIC, caching every decode."""
    real = [c.vin for c in cars if not c.vin.startswith("DEMO")]
    cached = db.cached_decodes(real)
    missing = [v for v in real if v not in cached]
    if missing:
        try:
            fresh = nhtsa.decode_vins(missing)
            db.save_decodes(fresh)
            cached.update(fresh)
        except requests.RequestException as exc:
            result.errors.append(f"vPIC: {exc}")
    for car in cars:
        for key, value in cached.get(car.vin, {}).items():
            if value and getattr(car, key) is None:
                setattr(car, key, value)


def _attach_recalls(db: Database, cars: list[Listing], result: RunResult) -> None:
    for make, model, year in sorted({(c.make, c.model, c.year) for c in cars}):
        recalls = db.cached_recalls(make, model, year)
        if recalls is None:
            try:
                recalls = nhtsa.fetch_recalls(make, model, year)
                db.save_recalls(make, model, year, recalls)
            except requests.RequestException as exc:
                result.errors.append(f"recalls {year} {make} {model}: {exc}")
                continue
        for car in cars:
            if (car.make, car.model, car.year) == (make, model, year):
                db.set_recall_count(car.vin, len(recalls))
