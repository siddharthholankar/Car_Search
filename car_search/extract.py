"""Pull car listings out of a web page the user opened in their browser.

Most listing and dealer sites embed schema.org Vehicle/Car/Product data as
JSON-LD for search engines, so that is read first. Pages without it fall back
to the visible text: a single VIN plus the title, price and mileage.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from .models import Listing

VEHICLE_TYPES = {"car", "vehicle", "motorizedbicycle", "product", "individualproduct"}
VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
PRICE_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{4,6})(?!\s*(?:/|per)\s*mo)", re.I)
MILES_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{1,6})\s*(?:mi\b|miles\b)", re.I)
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
MAKES = ["Acura", "Alfa Romeo", "Audi", "BMW", "Buick", "Cadillac", "Chevrolet", "Chrysler", "Dodge",
         "Fiat", "Ford", "Genesis", "GMC", "Honda", "Hyundai", "Infiniti", "Jaguar", "Jeep", "Kia",
         "Land Rover", "Lexus", "Lincoln", "Mazda", "Mercedes-Benz", "Mini", "Mitsubishi", "Nissan",
         "Porsche", "Ram", "Subaru", "Tesla", "Toyota", "Volkswagen", "Volvo"]
MAKE_RE = re.compile(r"\b(" + "|".join(re.escape(m) for m in MAKES) + r")\b\s+([A-Za-z0-9-]+)", re.I)


def _valid_vin(vin: str) -> bool:
    return bool(VIN_RE.fullmatch(vin)) and any(c.isdigit() for c in vin) and any(c.isalpha() for c in vin)


# --- JSON-LD ------------------------------------------------------------------

def _walk(node):
    """Yield every dict inside a JSON-LD document (handles @graph and ItemList nesting)."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _types(node: dict) -> set[str]:
    t = node.get("@type", [])
    return {str(x).lower() for x in (t if isinstance(t, list) else [t])}


def _name(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).strip() if value not in (None, "") else None


def _number(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("value", value.get("price"))
    if value is None:
        return None
    match = re.search(r"-?\d[\d,]*\.?\d*", str(value))
    return float(match.group().replace(",", "")) if match else None


def _offer(node: dict) -> dict:
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict) and _types(offers) & {"aggregateoffer"}:
        offers = {**offers, "price": offers.get("lowPrice", offers.get("price"))}
    return offers if isinstance(offers, dict) else {}


def from_jsonld(blocks: list[str], page_url: str) -> list[Listing]:
    out = []
    for block in blocks:
        try:
            doc = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(doc):
            vin = str(node.get("vehicleIdentificationNumber") or node.get("vin") or "").strip().upper()
            if not _valid_vin(vin) or not (_types(node) & VEHICLE_TYPES or not node.get("@type")):
                continue
            listing = _listing_from_node(node, vin, page_url)
            if listing:
                out.append(listing)
    return out


def _listing_from_node(node: dict, vin: str, page_url: str) -> Listing | None:
    offer = _offer(node)
    price = _number(offer.get("price")) or _number(node.get("price"))
    name = _name(node.get("name")) or ""
    year = _number(node.get("vehicleModelDate") or node.get("modelDate") or node.get("productionDate"))
    if not year and (m := YEAR_RE.search(name)):
        year = float(m.group(1))
    make = _name(node.get("brand")) or _name(node.get("manufacturer"))
    model = _name(node.get("model"))
    if not (make and model) and (m := MAKE_RE.search(name)):
        make, model = make or m.group(1), model or m.group(2)
    miles = _number(node.get("mileageFromOdometer"))
    if isinstance(node.get("mileageFromOdometer"), dict) and \
            str(node["mileageFromOdometer"].get("unitCode", "")).upper() in {"KMT", "KM"}:
        miles = miles * 0.621371 if miles else miles
    if not (price and year and make and model) or miles is None:
        return None

    seller = offer.get("seller") or node.get("seller") or {}
    seller = seller[0] if isinstance(seller, list) and seller else seller
    address = seller.get("address", {}) if isinstance(seller, dict) else {}
    address = address[0] if isinstance(address, list) and address else address
    geo = (seller.get("geo") if isinstance(seller, dict) else None) or {}
    condition = str(node.get("itemCondition") or offer.get("itemCondition") or "").lower()
    trim = _name(node.get("vehicleConfiguration")) or _name(node.get("trim"))
    url = node.get("url") or offer.get("url") or page_url
    if isinstance(url, str) and url.startswith("/"):
        parts = urlparse(page_url)
        url = f"{parts.scheme}://{parts.netloc}{url}"

    if "new" in condition and "used" not in condition:
        return None  # this is a used-car finder
    return Listing(
        vin=vin,
        source=_site(page_url),
        price=price,
        year=int(year),
        make=make,
        model=_clean_model(model, make),
        miles=int(miles),
        trim=trim,
        drivetrain=_drive(_name(node.get("driveWheelConfiguration"))),
        engine=_name(node.get("vehicleEngine")),
        body_type=_name(node.get("bodyType")),
        exterior_color=_name(node.get("color")),
        url=url if isinstance(url, str) else page_url,
        dealer_name=_name(seller) if isinstance(seller, dict) else None,
        city=address.get("addressLocality") if isinstance(address, dict) else None,
        state=address.get("addressRegion") if isinstance(address, dict) else None,
        zip=str(address.get("postalCode"))[:5] if isinstance(address, dict) and address.get("postalCode") else None,
        latitude=_number(geo.get("latitude")) if isinstance(geo, dict) else None,
        longitude=_number(geo.get("longitude")) if isinstance(geo, dict) else None,
        certified=True if "certified" in (name + " " + condition).lower() else None,
    )


def _clean_model(model: str, make: str) -> str:
    """Brand prefixes like "Toyota RAV4" in the model field become "RAV4"."""
    if model.lower().startswith(make.lower() + " "):
        model = model[len(make) + 1:]
    return model.strip()


def _drive(value: str | None) -> str | None:
    if not value:
        return None
    v = value.upper()
    for key, words in {"AWD": ("AWD", "ALL"), "4WD": ("4WD", "4X4", "FOUR"),
                       "FWD": ("FWD", "FRONT"), "RWD": ("RWD", "REAR")}.items():
        if any(w in v for w in words):
            return key
    return value


def _site(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host or "browser"


# --- visible-text fallback ------------------------------------------------------

def from_text(text: str, title: str, page_url: str) -> list[Listing]:
    """A single car's detail page without structured data."""
    vins = {v for v in VIN_RE.findall(text.upper()) if _valid_vin(v)}
    if len(vins) != 1:
        return []  # a results page or no VIN at all: too ambiguous to guess
    vin = vins.pop()
    heading = title or text[:300]
    year = YEAR_RE.search(heading)
    mm = MAKE_RE.search(heading)
    prices = [float(p.replace(",", "")) for p in PRICE_RE.findall(text)]
    prices = [p for p in prices if 1000 <= p <= 300000]
    miles = MILES_RE.search(text)
    if not (year and mm and prices and miles):
        return []
    return [Listing(
        vin=vin,
        source=_site(page_url),
        price=prices[0],
        year=int(year.group(1)),
        make=mm.group(1),
        model=mm.group(2),
        miles=int(miles.group(1).replace(",", "")),
        url=page_url,
    )]


def extract(page: dict) -> list[Listing]:
    """`page` is what the extension sends: url, title, jsonld (list of str), text."""
    url = page.get("url", "")
    found = from_jsonld(page.get("jsonld") or [], url)
    if not found:
        found = from_text(page.get("text") or "", page.get("title") or "", url)
    return found


# --- saved HTML files -------------------------------------------------------------

class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.jsonld, self.text, self.title = [], [], ""
        self._in_ld = self._in_title = self._skip = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and "ld+json" in (attrs.get("type") or ""):
            self._in_ld, self._buf = True, []
        elif tag in ("script", "style", "noscript"):
            self._skip = True
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self.jsonld.append("".join(self._buf))
            self._in_ld = False
        elif tag in ("script", "style", "noscript"):
            self._skip = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)
        elif self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.text.append(data.strip())


def page_from_html(html: str, url: str = "") -> dict:
    """Turn a page saved with the browser's "Save page as" into the same shape the extension sends."""
    parser = _PageParser()
    parser.feed(html)
    if not url and (m := re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html)):
        url = m.group(1)
    return {"url": url, "title": parser.title.strip(), "jsonld": parser.jsonld, "text": "\n".join(parser.text)}
