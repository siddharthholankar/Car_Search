"""Load settings from a TOML file, with environment variables for secrets."""

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
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class Config:
    database: str = "car_search.db"
    search: SearchConfig = field(default_factory=SearchConfig)
    sources: list[str] = field(default_factory=lambda: ["sample"])
    marketcheck_api_key: str = ""
    marketcheck_max_pages: int = 5
    dealer_feed_urls: list[str] = field(default_factory=list)
    decode_vins: bool = True
    recalls: bool = True
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

    src = raw.get("sources", {})
    mc = src.get("marketcheck", {})
    feeds = src.get("dealer_feed", {})
    enrich = raw.get("enrich", {})

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
        telegram_enabled=bool(a.get("telegram_enabled", False)),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN", a.get("telegram_bot_token", "")),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID", a.get("telegram_chat_id", "")),
    )

    return Config(
        database=raw.get("database", "car_search.db"),
        search=search,
        sources=list(src.get("enabled", ["sample"])),
        marketcheck_api_key=_env("MARKETCHECK_API_KEY", mc.get("api_key", "")),
        marketcheck_max_pages=int(mc.get("max_pages", 5)),
        dealer_feed_urls=list(feeds.get("urls", [])),
        decode_vins=bool(enrich.get("decode_vins", True)),
        recalls=bool(enrich.get("recalls", True)),
        scoring=scoring,
        alerts=alerts,
    )
