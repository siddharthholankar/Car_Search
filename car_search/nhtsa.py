"""Free NHTSA APIs: vPIC VIN decoding and recalls.

The public recalls API is keyed by make/model/year, not VIN, so it lists every
campaign for that vehicle. Whether a specific car's recall was already repaired
needs the VIN lookup at https://www.nhtsa.gov/recalls.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

VPIC_BATCH_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVINValuesBatch/"
RECALLS_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
BATCH_SIZE = 50


def decode_vins(vins: list[str], session: requests.Session | None = None) -> dict[str, dict]:
    """Return {vin: decoded fields} for VINs vPIC could decode."""
    session = session or requests.Session()
    decoded: dict[str, dict] = {}
    for start in range(0, len(vins), BATCH_SIZE):
        chunk = vins[start:start + BATCH_SIZE]
        resp = session.post(VPIC_BATCH_URL, data={"format": "json", "data": ";".join(chunk)}, timeout=60)
        resp.raise_for_status()
        for row in resp.json().get("Results", []):
            vin = (row.get("VIN") or "").upper()
            if vin and row.get("Make"):
                decoded[vin] = parse_vpic(row)
    return decoded


def parse_vpic(row: dict) -> dict:
    engine = None
    if row.get("DisplacementL"):
        engine = f"{float(row['DisplacementL']):.1f}L"
        if row.get("EngineCylinders"):
            engine += f" {row['EngineCylinders']}-cyl"
        if row.get("FuelTypePrimary"):
            engine += f" {row['FuelTypePrimary']}"
    return {
        "trim": row.get("Trim") or row.get("Series") or None,
        "drivetrain": normalize_drive(row.get("DriveType")),
        "engine": engine,
        "body_type": row.get("BodyClass") or None,
    }


def normalize_drive(drive: str | None) -> str | None:
    if not drive:
        return None
    d = drive.upper()
    if "AWD" in d or "ALL" in d:
        return "AWD"
    if "4WD" in d or "4X4" in d or "4-WHEEL" in d:
        return "4WD"
    if "FWD" in d or "FRONT" in d:
        return "FWD"
    if "RWD" in d or "REAR" in d:
        return "RWD"
    return drive


def fetch_recalls(make: str, model: str, year: int, session: requests.Session | None = None) -> list[dict]:
    session = session or requests.Session()
    resp = session.get(RECALLS_URL, params={"make": make, "model": model, "modelYear": year}, timeout=30)
    resp.raise_for_status()
    return [
        {
            "campaign": r.get("NHTSACampaignNumber"),
            "component": r.get("Component"),
            "summary": r.get("Summary"),
        }
        for r in resp.json().get("results", [])
    ]
