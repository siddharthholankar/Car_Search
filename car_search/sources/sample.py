"""Synthetic listings for trying the pipeline without an API key.

VINs start with "DEMO" so they can never be mistaken for real cars. Prices drift
down a little each day so repeated runs build up price history.
"""

from __future__ import annotations

import datetime as dt
import math
import random

from ..config import SearchConfig
from ..models import Listing
from .base import Source, matches

# Rough new-car price, trims (with premium over base) per model.
CATALOG = {
    ("Toyota", "RAV4"): (30000, {"LE": 0, "XLE": 2200, "XLE Premium": 3800, "Adventure": 4200, "Limited": 6500}),
    ("Honda", "CR-V"): (29000, {"LX": 0, "EX": 2500, "EX-L": 4500, "Touring": 7000}),
    ("Honda", "Accord"): (28000, {"LX": 0, "Sport": 2000, "EX-L": 4000, "Touring": 7500}),
    ("Toyota", "Camry"): (27000, {"LE": 0, "SE": 1500, "XLE": 4500, "XSE": 5000}),
    ("Mazda", "CX-5"): (28500, {"Sport": 0, "Touring": 2000, "Grand Touring": 5000}),
}

CITIES = [
    ("Alexandria", "LA", "71301", 31.3113, -92.4451),
    ("Pineville", "LA", "71360", 31.3224, -92.4343),
    ("Lafayette", "LA", "70501", 30.2241, -92.0198),
    ("Baton Rouge", "LA", "70801", 30.4515, -91.1871),
    ("Shreveport", "LA", "71101", 32.5252, -93.7502),
    ("Monroe", "LA", "71201", 32.5093, -92.1193),
    ("Lake Charles", "LA", "70601", 30.2266, -93.2174),
    ("Natchez", "MS", "39120", 31.5604, -91.4032),
    ("Jackson", "MS", "39201", 32.2988, -90.1848),
    ("Beaumont", "TX", "77701", 30.0802, -94.1266),
]
CENTER = (31.3113, -92.4451)  # 71301
DEALERS = ["Auto Plaza", "Motors", "Toyota", "Honda", "Pre-Owned Center", "Car Mart"]
VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
EPOCH = dt.date(2026, 1, 1)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class SampleSource(Source):
    name = "sample"

    def __init__(self, count: int = 3000, seed: int = 7, today: dt.date | None = None):
        self.count = count
        self.seed = seed
        self.today = today or dt.date.today()

    def fetch(self, search: SearchConfig) -> list[Listing]:
        day = (self.today - EPOCH).days % 365  # repeat yearly so the demo never runs dry
        out: list[Listing] = []
        for i in range(self.count):
            # Each car gets its own RNG so it's identical from one day to the next.
            rng = random.Random(self.seed * 100_003 + i)
            car = self._make_car(rng, i, day)
            if car is None:
                continue
            if matches(car, search):
                out.append(car)
                # Some cars also show up on the dealer's own site at a slightly different price.
                if rng.random() < 0.2:
                    dup = Listing(**{**car.to_dict(), "source": "sample-dealer-site",
                                     "price": car.price + rng.choice([-300, 0, 200, 500]),
                                     "url": f"https://dealer.example/inventory/{car.vin}"})
                    out.append(dup)
        return out

    def _make_car(self, rng: random.Random, i: int, day: int) -> Listing | None:
        (make, model), (base, trims) = rng.choice(list(CATALOG.items()))
        year = rng.randint(2015, 2025)
        age = max(self.today.year - year, 0)
        miles = max(1000, int(rng.gauss(12000 * max(age, 0.5), 9000)))
        trim, premium = rng.choice(list(trims.items()))
        awd = model != "Accord" and model != "Camry" and rng.random() < 0.55
        city, state, zip_code, lat, lon = rng.choice(CITIES)
        lat += rng.uniform(-0.05, 0.05)
        lon += rng.uniform(-0.05, 0.05)

        accident = rng.random() < 0.18
        salvage = rng.random() < 0.03
        rental = rng.random() < 0.08
        repo = rng.random() < 0.02
        owners = rng.choices([1, 2, 3, 4, 5], weights=[45, 30, 15, 7, 3])[0]
        service = rng.random() < 0.4
        certified = age <= 6 and not accident and rng.random() < 0.15

        value = (base + premium + (1400 if awd else 0)) * (0.92 ** age) * (1 - miles / 350_000)
        value *= 0.88 if accident else 1.0
        value *= 0.6 if salvage else 1.0
        value *= 1.04 if certified else 1.0
        value *= rng.gauss(1.0, 0.06)
        if rng.random() < 0.06:
            value *= 0.85  # a genuine bargain

        # Each car is "listed" on a day in a rolling window and sells after a while.
        listed_on = rng.randint(-120, 365)
        lifetime = rng.randint(10, 120)
        if not (listed_on <= day < listed_on + lifetime):
            return None
        dom = day - listed_on
        price = round(value * (1 - 0.0015 * dom) / 100) * 100  # slow price drops
        if price < 3000:
            return None

        vin = "DEMO" + "".join(rng.choice(VIN_CHARS) for _ in range(13))
        return Listing(
            vin=vin,
            source="sample",
            source_id=f"sample-{i}",
            price=float(price),
            year=year,
            make=make,
            model=model,
            trim=trim,
            drivetrain="AWD" if awd else "FWD",
            body_type="Sedan" if model in {"Accord", "Camry"} else "SUV",
            exterior_color=rng.choice(["White", "Black", "Silver", "Gray", "Blue", "Red"]),
            miles=miles,
            url=f"https://listings.example/cars/{vin}",
            dealer_name=f"{city} {rng.choice(DEALERS)}",
            city=city,
            state=state,
            zip=zip_code,
            latitude=round(lat, 4),
            longitude=round(lon, 4),
            distance_miles=round(haversine_miles(*CENTER, lat, lon), 1),
            days_on_market=dom,
            accident_reported=accident,
            salvage_or_rebuilt=salvage,
            repossession=repo,
            rental_history=rental,
            owners=owners,
            service_records=service,
            certified=certified,
        )
