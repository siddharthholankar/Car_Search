import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from car_search.config import Config, SearchConfig, Vehicle
from car_search.db import Database
from car_search.extract import extract, from_text, page_from_html
from car_search.importers import import_file
from car_search.models import Listing
from car_search.server import make_handler
from car_search.sources.base import matches

VIN = "2T3P1RFV8LC123456"


def car_node(vin=VIN, price=21995, miles=48210, **extra):
    return {
        "@context": "https://schema.org", "@type": "Car",
        "name": "Used 2020 Toyota RAV4 XLE AWD",
        "vehicleIdentificationNumber": vin,
        "brand": {"@type": "Brand", "name": "Toyota"}, "model": "RAV4",
        "vehicleModelDate": "2020", "vehicleConfiguration": "XLE",
        "driveWheelConfiguration": "https://schema.org/AllWheelDriveConfiguration",
        "mileageFromOdometer": {"@type": "QuantitativeValue", "value": f"{miles:,}", "unitCode": "SMI"},
        "color": "Blue",
        "offers": {"@type": "Offer", "price": price, "priceCurrency": "USD",
                   "seller": {"@type": "AutoDealer", "name": "Pineville Toyota",
                              "address": {"addressLocality": "Pineville", "addressRegion": "LA",
                                          "postalCode": "71360-1234"},
                              "geo": {"latitude": 31.32, "longitude": -92.43}}},
        **extra,
    }


def test_single_car_jsonld():
    [l] = extract({"url": "https://www.cars.com/vehicledetail/1/", "jsonld": [json.dumps(car_node())]})
    assert (l.vin, l.price, l.miles, l.year, l.make, l.model) == (VIN, 21995, 48210, 2020, "Toyota", "RAV4")
    assert l.trim == "XLE" and l.drivetrain == "AWD" and l.source == "cars.com"
    assert l.dealer_name == "Pineville Toyota" and l.zip == "71360" and l.latitude == 31.32


def test_results_page_itemlist_and_graph():
    vins = [f"2T3P1RFV8LC12{i:04d}" for i in range(3)]
    doc = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage", "name": "Used RAV4 for sale"},
        {"@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": i, "item": car_node(vin=v, price=20000 + i)}
            for i, v in enumerate(vins)]},
    ]}
    found = extract({"url": "https://www.cargurus.com/search", "jsonld": [json.dumps(doc), "not json{"]})
    assert sorted(l.vin for l in found) == vins


def test_product_shape_new_cars_and_kilometres():
    product = {"@type": "Product", "name": "2021 Honda CR-V EX-L", "vin": "7FARW2H85ME012345",
               "brand": "Honda", "model": "Honda CR-V", "mileageFromOdometer": {"value": 50000, "unitCode": "KMT"},
               "offers": [{"@type": "Offer", "price": "$24,500"}]}
    new_car = car_node(vin="2T3P1RFV8LC999999", itemCondition="https://schema.org/NewCondition")
    found = extract({"url": "https://dealer.example/vdp", "jsonld": [json.dumps([product, new_car])]})
    assert len(found) == 1
    l = found[0]
    assert l.model == "CR-V" and l.price == 24500 and l.miles == 31068 and l.year == 2021


def test_text_fallback_on_detail_page():
    text = "2019 Honda Accord Sport\n$18,450\nEst. $312/mo\n41,230 miles\nVIN: 1HGCV1F34KA012345\nStock 5512"
    [l] = from_text(text, "2019 Honda Accord Sport | Smith Motors", "https://smithmotors.example/vdp/5512")
    assert (l.vin, l.price, l.miles, l.make, l.model) == ("1HGCV1F34KA012345", 18450, 41230, "Honda", "Accord")
    # Two VINs is a results page: too ambiguous for the text fallback.
    assert from_text(text + " 1HGCV1F34KA099999", "x", "u") == []


def test_saved_html_page(tmp_path):
    html = ('<html><head><title>2020 RAV4</title><link rel="canonical" href="https://www.autotrader.com/x">'
            f'<script type="application/ld+json">{json.dumps(car_node())}</script></head>'
            '<body><script>var x=1;</script><h1>2020 Toyota RAV4</h1></body></html>')
    page = page_from_html(html)
    assert page["url"] == "https://www.autotrader.com/x" and len(page["jsonld"]) == 1
    assert "var x" not in page["text"]
    path = tmp_path / "rav4.html"
    path.write_text(html)
    [l] = import_file(path)
    assert l.source == "autotrader.com"


def test_loose_model_matching():
    search = SearchConfig(vehicles=[Vehicle("Honda", "CR-V")], max_price=30000)
    base = dict(vin=VIN, source="x", price=20000, year=2021, make="Honda", miles=30000)
    assert matches(Listing(model="CRV", **base), search)
    assert matches(Listing(model="CR-V Hybrid", **base), search)
    assert not matches(Listing(model="Civic", **base), search)


@pytest.fixture
def server(tmp_path):
    cfg = Config(database=str(tmp_path / "s.db"))
    cfg.search = SearchConfig(vehicles=[Vehicle("Toyota", "RAV4")])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg, threading.Lock()))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", cfg
    httpd.shutdown()


def post(url, body, origin=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read()), resp.headers


def test_server_capture_and_origin_check(server):
    base, cfg = server
    nodes = [car_node(vin=f"2T3P1RFV8LC1{i:05d}", price=15000 + 150 * i, miles=40000 + 900 * i) for i in range(35)]
    page = {"url": "https://www.cars.com/shopping/results/", "jsonld": [json.dumps(nodes)], "text": ""}

    status, body, headers = post(base + "/capture", page, origin="chrome-extension://abcdef")
    assert status == 200 and body["found"] == 35 and body["saved"] == 35 and body["new"] == 35
    assert headers["Access-Control-Allow-Origin"] == "chrome-extension://abcdef"
    assert all(c["deal_score"] is not None for c in body["cars"])  # 35 cars is enough to train

    with pytest.raises(urllib.error.HTTPError) as err:
        post(base + "/capture", page, origin="https://evil.example")
    assert err.value.code == 403

    with urllib.request.urlopen(base + "/status") as resp:
        assert json.loads(resp.read())["total_active"] == 35
    db = Database(cfg.database)
    assert len(db.captures_frame()) == 1
    db.close()


def test_stale_cars_retire(tmp_path):
    db = Database(tmp_path / "d.db")
    db.upsert_listings([Listing(vin=VIN, source="x", price=1, year=2020, make="Toyota", model="RAV4", miles=1)],
                       {}, seen_at="2020-01-01T00:00:00+00:00")
    assert db.mark_stale(14) == 1
    assert db.listings_frame(active_only=True).empty
    db.close()
