"""Import files saved from your browser: pages ("Save page as" .html) and
inventory spreadsheets some dealers offer for download (.csv / .json)."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from .extract import extract, page_from_html
from .models import Listing

COLUMN_ALIASES = {
    "vin": ["vin", "vin_number"],
    "price": ["price", "internet_price", "selling_price", "list_price", "asking_price"],
    "year": ["year", "model_year"],
    "make": ["make"],
    "model": ["model"],
    "trim": ["trim", "series"],
    "miles": ["miles", "mileage", "odometer"],
    "drivetrain": ["drivetrain", "drive_type"],
    "exterior_color": ["exterior_color", "color", "ext_color"],
    "url": ["url", "vdp_url", "link"],
    "dealer_name": ["dealer", "dealer_name"],
    "city": ["city"],
    "state": ["state"],
    "zip": ["zip", "zipcode", "postal_code"],
    "days_on_market": ["days_on_market", "dom", "age"],
    "certified": ["certified", "cpo", "is_certified"],
}


def import_file(path: str | Path) -> list[Listing]:
    path = Path(path)
    text = path.read_text(errors="replace")
    if path.suffix.lower() in {".html", ".htm", ".mhtml"}:
        listings = extract(page_from_html(text))
        for l in listings:
            if l.source == "browser":
                l.source = "saved-page"
        return listings
    return parse_frame(read_feed(text, path.name))


def read_feed(text: str, url: str = "") -> pd.DataFrame:
    stripped = text.lstrip()
    if url.lower().endswith(".json") or stripped.startswith(("[", "{")):
        frame = pd.read_json(io.StringIO(text))
    else:
        frame = pd.read_csv(io.StringIO(text))
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    return frame


def parse_frame(frame: pd.DataFrame) -> list[Listing]:
    columns = {}
    for field_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in frame.columns:
                columns[field_name] = alias
                break
    required = {"vin", "price", "year", "make", "model", "miles"}
    if missing := required - columns.keys():
        raise ValueError(f"feed is missing columns: {sorted(missing)}")

    listings = []
    for row in frame.to_dict("records"):
        values = {f: row.get(col) for f, col in columns.items()}
        values = {k: (None if pd.isna(v) else v) for k, v in values.items()}
        try:
            values["price"] = float(str(values["price"]).replace("$", "").replace(",", ""))
            values["miles"] = int(float(str(values["miles"]).replace(",", "")))
            values["year"] = int(values["year"])
        except (TypeError, ValueError):
            continue
        if values.get("days_on_market") is not None:
            values["days_on_market"] = int(values["days_on_market"])
        if values.get("certified") is not None:
            values["certified"] = str(values["certified"]).strip().lower() in {"1", "true", "yes", "y"}
        for key in ("zip", "vin", "make", "model"):
            if values.get(key) is not None:
                values[key] = str(values[key])
        listings.append(Listing(source="file-import", **values))
    return listings
