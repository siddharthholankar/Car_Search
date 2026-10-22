"""MarketCheck active-inventory search (https://www.marketcheck.com/apis)."""

from __future__ import annotations

import datetime as dt
import logging

import requests

from ..config import SearchConfig
from ..models import Listing
from .base import Source

log = logging.getLogger(__name__)

SEARCH_URL = "https://mc-api.marketcheck.com/v2/search/car/active"
PAGE_SIZE = 50  # API maximum


class MarketCheckSource(Source):
    name = "marketcheck"

    def __init__(self, api_key: str, max_pages: int = 5, session: requests.Session | None = None):
        self.api_key = api_key
        self.max_pages = max_pages
        self.session = session or requests.Session()

    def fetch(self, search: SearchConfig) -> list[Listing]:
        max_year = search.max_year or dt.date.today().year + 1
        results: list[Listing] = []
        for vehicle in search.vehicles:
            for page in range(self.max_pages):
                params = {
                    "api_key": self.api_key,
                    "car_type": "used",
                    "make": vehicle.make,
                    "model": vehicle.model,
                    "year_range": f"{search.min_year}-{max_year}",
                    "price_range": f"1-{search.max_price}",
                    "miles_range": f"0-{search.max_miles}",
                    "zip": search.zip,
                    "radius": search.radius_miles,
                    "rows": PAGE_SIZE,
                    "start": page * PAGE_SIZE,
                }
                resp = self.session.get(SEARCH_URL, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("listings", [])
                results.extend(l for l in (parse_listing(item) for item in batch) if l)
                if len(batch) < PAGE_SIZE or (page + 1) * PAGE_SIZE >= data.get("num_found", 0):
                    break
            log.info("marketcheck: %s %s done (%d listings so far)", vehicle.make, vehicle.model, len(results))
        return results


def parse_listing(item: dict) -> Listing | None:
    build = item.get("build") or {}
    dealer = item.get("dealer") or {}
    vin, price = item.get("vin"), item.get("price")
    if not vin or not price or not build.get("year"):
        return None
    clean_title = item.get("carfax_clean_title")
    one_owner = item.get("carfax_1_owner")
    return Listing(
        vin=vin,
        source="marketcheck",
        source_id=item.get("id"),
        price=float(price),
        year=int(build["year"]),
        make=build.get("make", ""),
        model=build.get("model", ""),
        trim=build.get("trim"),
        drivetrain=build.get("drivetrain"),
        engine=build.get("engine"),
        body_type=build.get("body_type"),
        exterior_color=item.get("exterior_color"),
        miles=int(item.get("miles") or 0),
        url=item.get("vdp_url"),
        dealer_name=dealer.get("name"),
        city=dealer.get("city"),
        state=dealer.get("state"),
        zip=dealer.get("zip"),
        latitude=_float(dealer.get("latitude")),
        longitude=_float(dealer.get("longitude")),
        distance_miles=_float(item.get("dist")),
        days_on_market=item.get("dom_active", item.get("dom")),
        salvage_or_rebuilt=None if clean_title is None else not clean_title,
        owners=1 if one_owner else None,
        certified=item.get("is_certified"),
    )


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
