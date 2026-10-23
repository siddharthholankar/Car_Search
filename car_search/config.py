"""Load settings from a TOML file, with environment variables for the email password."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Vehicle:
    make: str
    model: str


@dataclass
class SearchConfig:
    zip: str = "71301"
    radius_miles: int = 200
    min_year: int = 2018
    max_year: int | None = None
    max_price: int = 23000
    max_miles: int = 80000
    vehicles: list[Vehicle] = field(default_factory=list)
    # Coordinates of `zip`, for distances to dealers whose pages include a location.
    center: tuple[float, float] | None = (31.3113, -92.4451)


@dataclass
class ScoringConfig:
    min_training_rows: int = 30
    negotiable_days: int = 45
    penalties: dict[str, float] = field(default_factory=lambda: {
        "accident": 0.05,
        "salvage_or_rebuilt": 0.40,
        "repossession": 0.05,
        "rental": 0.03,
        "many_owners": 0.03,
    })
    bonuses: dict[str, float] = field(default_factory=lambda: {
        "one_owner": 0.02,
        "service_records": 0.01,
        "certified": 0.02,
        "long_on_market": 0.01,
    })


@dataclass
class AlertConfig:
    threshold: float = 0.08
    realert_drop: float = 0.03
    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_to: str = ""


@dataclass
class Config:
    database: str = "car_search.db"
    search: SearchConfig = field(default_factory=SearchConfig)
    server_port: int = 8765
    stale_days: int = 14  # a car not seen in your browser for this long is treated as sold
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)


def _env(name: str, fallback: str) -> str:
    return os.environ.get(name) or fallback


def load_config(path: str | Path | None = None) -> Config:
    """Read `path` (default: ./config.toml if present) into a Config."""
    if path is None:
        path = Path("config.toml")
        raw = tomllib.loads(path.read_text()) if path.exists() else {}
    else:
        raw = tomllib.loads(Path(path).read_text())

    s = raw.get("search", {})
    search = SearchConfig(
        zip=str(s.get("zip", "71301")),
        radius_miles=int(s.get("radius_miles", 200)),
        min_year=int(s.get("min_year", 2018)),
        max_year=s.get("max_year"),
        max_price=int(s.get("max_price", 23000)),
        max_miles=int(s.get("max_miles", 80000)),
        vehicles=[Vehicle(v["make"], v["model"]) for v in s.get("vehicles", [])]
        or [Vehicle("Toyota", "RAV4"), Vehicle("Honda", "CR-V"), Vehicle("Honda", "Accord")],
    )
    if "center" in s:
        search.center = tuple(s["center"]) if s["center"] else None

    sc = raw.get("scoring", {})
    scoring = ScoringConfig()
    scoring.min_training_rows = int(sc.get("min_training_rows", scoring.min_training_rows))
    scoring.negotiable_days = int(sc.get("negotiable_days", scoring.negotiable_days))
    scoring.penalties.update(sc.get("penalties", {}))
    scoring.bonuses.update(sc.get("bonuses", {}))

    a = raw.get("alerts", {})
    alerts = AlertConfig(
        threshold=float(a.get("threshold", 0.08)),
        realert_drop=float(a.get("realert_drop", 0.03)),
        email_enabled=bool(a.get("email_enabled", False)),
        smtp_host=a.get("smtp_host", "smtp.gmail.com"),
        smtp_port=int(a.get("smtp_port", 587)),
        smtp_user=_env("SMTP_USER", a.get("smtp_user", "")),
        smtp_password=_env("SMTP_PASSWORD", a.get("smtp_password", "")),
        email_to=_env("ALERT_EMAIL_TO", a.get("email_to", "")),
    )

    return Config(
        database=raw.get("database", "car_search.db"),
        search=search,
        server_port=int(raw.get("server_port", 8765)),
        stale_days=int(raw.get("stale_days", 14)),
        scoring=scoring,
        alerts=alerts,
    )
