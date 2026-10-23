import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from car_search.alerts import build_message, select_alerts
from car_search.config import Config, ScoringConfig, SearchConfig, Vehicle, load_config
from car_search.db import Database
from car_search.dedupe import dedupe
from car_search.finance import monthly_payment
from car_search.models import Listing
from car_search.pipeline import ingest
from car_search.scoring import adjustments, score_listings
from car_search.importers import parse_frame, read_feed
from car_search.sources.sample import SampleSource

VIN = "1HGCV1F30LA000001"


def car(**kw):
    base = dict(vin=VIN, source="a", price=20000, year=2021, make="Honda", model="Accord", miles=40000)
    return Listing(**{**base, **kw})


def config(tmp_path, **kw):
    cfg = Config(database=str(tmp_path / "t.db"))
    cfg.search = SearchConfig(vehicles=[Vehicle("Toyota", "RAV4"), Vehicle("Honda", "CR-V"),
                                        Vehicle("Honda", "Accord")])
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_dedupe_keeps_cheapest_and_merges_flags():
    a = car(source="a", price=20000, url="https://a/1", accident_reported=None, owners=1)
    b = car(source="b", price=19500, url="https://b/1", accident_reported=True, owners=2, trim="EX-L")
    other = car(vin="1HGCV1F30LA000002")
    out = {l.vin: l for l in dedupe([a, b, other, car(vin="short")])}
    assert len(out) == 2
    m = out[VIN]
    assert m.price == 19500 and m.source == "b"
    assert m.accident_reported is True and m.owners == 2 and m.trim == "EX-L"
    assert m.other_urls == ["https://a/1"]


def test_adjustments():
    cfg = ScoringConfig()
    total, notes, negotiable = adjustments(pd.Series(
        {"accident_reported": 1, "owners": 1, "certified": 0, "days_on_market": 60}), cfg)
    assert total == pytest.approx(-0.05 + 0.02 + 0.01)
    assert negotiable and len(notes) == 3
    total, _, _ = adjustments(pd.Series({"salvage_or_rebuilt": 1, "owners": 5}), cfg)
    assert total == pytest.approx(-0.43)


def test_model_finds_underpriced_car():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(120):
        year = int(rng.integers(2018, 2025))
        miles = int(rng.integers(5000, 80000))
        price = 30000 * 0.92 ** (2026 - year) * (1 - miles / 350000) * rng.normal(1, 0.03)
        rows.append(dict(vin=f"V{i:016d}", price=price, year=year, make="Toyota", model="RAV4",
                         trim="XLE", miles=miles, drivetrain="AWD", state="LA", days_on_market=20))
    df = pd.DataFrame(rows)
    df.loc[0, "price"] *= 0.8  # a bargain
    scores, info = score_listings(df, ScoringConfig())
    assert info["method"] == "ridge_log_price"
    assert info["median_abs_pct_error"] < 0.05
    assert scores.iloc[0]["value_score"] > 0.12
    assert scores["value_score"].iloc[1:].abs().median() < 0.05


def test_small_dataset_uses_comps():
    df = pd.DataFrame([dict(vin=f"V{i}", price=p, year=2020, make="Honda", model="CR-V", miles=50000)
                       for i, p in enumerate([20000, 21000, 19000, 15000])])
    scores, info = score_listings(df, ScoringConfig())
    assert info["method"] == "comps_median"
    assert scores.iloc[3]["market_price"] == 20000


def test_payment():
    p = monthly_payment(20000, down_payment=2000, apr=6.0, term_months=60, tax_rate=0.0)
    assert p.amount_financed == 18000
    assert p.monthly_payment == pytest.approx(347.99, abs=0.01)
    assert monthly_payment(12000, apr=0, term_months=12, tax_rate=0).monthly_payment == 1000


def test_dealer_feed_csv():
    text = "VIN,Year,Make,Model,Mileage,Internet Price,Certified\n" \
           f"{VIN},2021,HONDA,Accord,\"41,000\",\"$19,995\",yes\nbad,x,Honda,Accord,1,1,no\n"
    out = parse_frame(read_feed(text, "inv.csv"))
    assert len(out) == 1
    assert out[0].price == 19995 and out[0].miles == 41000 and out[0].certified is True
    assert out[0].make == "Honda"


def test_pipeline_history_and_alerts(tmp_path):
    cfg = config(tmp_path)
    db = Database(cfg.database)
    day1 = SampleSource(today=dt.date(2026, 3, 1))
    r1 = ingest(cfg, db, day1.fetch(cfg.search), complete_snapshot=True)
    assert r1.unique > 50 and r1.new == r1.unique
    assert r1.received > r1.unique  # duplicates across "sites" were merged
    assert len(r1.alerts) > 0 and r1.alert_channels == []
    assert r1.model_info["method"] == "ridge_log_price"

    # Alerts are marked as shown; come back a week later.
    r2 = ingest(cfg, db, SampleSource(today=dt.date(2026, 3, 8)).fetch(cfg.search), complete_snapshot=True)
    assert r2.price_drops > 0 and r2.went_inactive > 0
    frame = db.listings_frame()
    dropped = frame[frame["vin"].isin(r1.alerts["vin"]) & (frame["price"] < frame["alerted_price"])]
    # Previously alerted cars only re-alert after a price drop.
    assert set(r2.alerts["vin"]) & set(r1.alerts["vin"]) <= set(dropped["vin"])
    vin = frame[frame["active"] == 1].iloc[0]["vin"]
    assert len(db.price_history(vin)) >= 1
    assert "deal" in build_message(r2.alerts.head(2)) or r2.alerts.empty
    db.close()


def test_select_alerts_threshold():
    df = pd.DataFrame({"vin": ["a", "b", "c", "d"], "active": [1, 1, 1, 1],
                       "deal_score": [0.1, 0.05, 0.2, 0.3],
                       "price": [100, 100, 90, 99], "alerted_price": [None, None, 95, 100]})
    # "d" dropped only 1% since its last alert, so it stays quiet.
    assert list(select_alerts(df, 0.08)["vin"]) == ["c", "a"]


def test_load_config(tmp_path, monkeypatch):
    path = tmp_path / "c.toml"
    path.write_text('[search]\nzip = "70501"\nvehicles = [{make="Mazda", model="CX-5"}]\n'
                    '[scoring]\npenalties = { accident = 0.1 }\n[alerts]\nthreshold = 0.1\n')
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    cfg = load_config(path)
    assert cfg.search.zip == "70501" and cfg.search.vehicles[0].model == "CX-5"
    assert cfg.scoring.penalties["accident"] == 0.1 and cfg.scoring.penalties["rental"] == 0.03
    assert cfg.alerts.threshold == 0.1 and cfg.alerts.smtp_password == "pw"


def test_alert_text_with_missing_location():
    row = pd.Series({"year": 2023, "make": "Honda", "model": "CR-V", "trim": np.nan, "price": 16900.0,
                     "deal_score": 0.12, "market_price": 19200.0, "miles": 31402, "city": np.nan,
                     "state": None, "dealer_name": np.nan, "distance_miles": np.nan,
                     "alerted_price": np.nan, "url": "https://dealer.example/1"})
    text = build_message(pd.DataFrame([row]))
    assert "2023 Honda CR-V - $16,900" in text and "\nSeller\n" in text
