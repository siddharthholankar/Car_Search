import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from car_search import nhtsa
from car_search.alerts import build_message, select_alerts
from car_search.config import Config, ScoringConfig, SearchConfig, Vehicle, load_config
from car_search.db import Database
from car_search.dedupe import dedupe
from car_search.finance import monthly_payment
from car_search.models import Listing
from car_search.pipeline import run
from car_search.scoring import adjustments, score_listings
from car_search.sources.dealer_feed import parse_frame, read_feed
from car_search.sources.marketcheck import parse_listing
from car_search.sources.sample import SampleSource

VIN = "1HGCV1F30LA000001"


def car(**kw):
    base = dict(vin=VIN, source="a", price=20000, year=2021, make="Honda", model="Accord", miles=40000)
    return Listing(**{**base, **kw})


def config(tmp_path, **kw):
    cfg = Config(database=str(tmp_path / "t.db"), decode_vins=False, recalls=False)
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


def test_marketcheck_parse():
    item = {"id": "x1", "vin": VIN, "price": 21000, "miles": 30000, "dom_active": 50, "dist": 42.5,
            "vdp_url": "https://d/1", "carfax_clean_title": False, "carfax_1_owner": True,
            "build": {"year": 2021, "make": "Honda", "model": "Accord", "trim": "Sport", "drivetrain": "FWD"},
            "dealer": {"name": "D", "city": "Alexandria", "state": "LA", "latitude": "31.3", "longitude": "-92.4"}}
    l = parse_listing(item)
    assert l.salvage_or_rebuilt is True and l.owners == 1 and l.days_on_market == 50
    assert l.latitude == 31.3 and l.trim == "Sport"
    assert parse_listing({"vin": VIN}) is None


def test_dealer_feed_csv():
    text = "VIN,Year,Make,Model,Mileage,Internet Price,Certified\n" \
           f"{VIN},2021,HONDA,Accord,\"41,000\",\"$19,995\",yes\nbad,x,Honda,Accord,1,1,no\n"
    out = parse_frame(read_feed(text, "inv.csv"))
    assert len(out) == 1
    assert out[0].price == 19995 and out[0].miles == 41000 and out[0].certified is True
    assert out[0].make == "Honda"


def test_vpic_parse():
    row = {"DriveType": "AWD/All-Wheel Drive", "DisplacementL": "2.5", "EngineCylinders": "4",
           "Trim": "XLE", "BodyClass": "Sport Utility Vehicle (SUV)/Multi-Purpose Vehicle (MPV)"}
    d = nhtsa.parse_vpic(row)
    assert d["drivetrain"] == "AWD" and d["engine"] == "2.5L 4-cyl" and d["trim"] == "XLE"


def test_pipeline_history_and_alerts(tmp_path):
    cfg = config(tmp_path)
    db = Database(cfg.database)
    day1 = SampleSource(today=dt.date(2026, 3, 1))
    r1 = run(cfg, db, sources=[day1])
    assert r1.unique > 50 and r1.new == r1.unique
    assert r1.fetched > r1.unique  # duplicates across "sites" were merged
    assert len(r1.alerts) > 0 and r1.alert_channels == []
    assert r1.model_info["method"] == "ridge_log_price"

    # Pretend alerts went out, then come back a week later.
    db.mark_alerted(list(zip(r1.alerts["vin"], r1.alerts["price"])))
    r2 = run(cfg, db, sources=[SampleSource(today=dt.date(2026, 3, 8))])
    assert r2.price_drops > 0 and r2.went_inactive > 0
    frame = db.listings_frame()
    dropped = frame[frame["vin"].isin(r1.alerts["vin"]) & (frame["price"] < frame["alerted_price"])]
    # Previously alerted cars only re-alert after a price drop.
    assert set(r2.alerts["vin"]) & set(r1.alerts["vin"]) <= set(dropped["vin"])
    vin = frame[frame["active"] == 1].iloc[0]["vin"]
    assert len(db.price_history(vin)) >= 1
    assert "deal" in build_message(r2.alerts.head(2)) or r2.alerts.empty
    db.close()


def test_failed_source_does_not_retire_cars(tmp_path):
    cfg = config(tmp_path)
    db = Database(cfg.database)
    run(cfg, db, sources=[SampleSource(today=dt.date(2026, 3, 1))], send_alerts=False)

    class Broken(SampleSource):
        name = "broken"

        def fetch(self, search):
            raise RuntimeError("down")

    r = run(cfg, db, sources=[Broken()], send_alerts=False)
    assert r.went_inactive == 0 and r.errors
    assert (db.listings_frame()["active"] == 1).all()
    db.close()


def test_select_alerts_threshold():
    df = pd.DataFrame({"vin": ["a", "b", "c"], "active": [1, 1, 1], "deal_score": [0.1, 0.05, 0.2],
                       "price": [100, 100, 90], "alerted_price": [None, None, 95]})
    assert list(select_alerts(df, 0.08)["vin"]) == ["c", "a"]


def test_load_config(tmp_path, monkeypatch):
    path = tmp_path / "c.toml"
    path.write_text('[search]\nzip = "70501"\nvehicles = [{make="Mazda", model="CX-5"}]\n'
                    '[scoring]\npenalties = { accident = 0.1 }\n[alerts]\nthreshold = 0.1\n')
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    cfg = load_config(path)
    assert cfg.search.zip == "70501" and cfg.search.vehicles[0].model == "CX-5"
    assert cfg.scoring.penalties["accident"] == 0.1 and cfg.scoring.penalties["rental"] == 0.03
    assert cfg.alerts.threshold == 0.1 and cfg.alerts.telegram_bot_token == "tok"
