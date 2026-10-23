"""The normalized listing record every source produces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

_UPPERCASE_MAKES = {"BMW", "GMC", "RAM", "MINI", "FIAT"}


def normalize_make(make: str) -> str:
    make = make.strip()
    return make.upper() if make.upper() in _UPPERCASE_MAKES else make.title()


@dataclass
class Listing:
    vin: str
    source: str
    price: float
    year: int
    make: str
    model: str
    miles: int
    trim: str | None = None
    drivetrain: str | None = None
    engine: str | None = None
    body_type: str | None = None
    exterior_color: str | None = None
    url: str | None = None
    source_id: str | None = None
    dealer_name: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_miles: float | None = None
    days_on_market: int | None = None
    # History flags. None means the source didn't say.
    accident_reported: bool | None = None
    salvage_or_rebuilt: bool | None = None
    repossession: bool | None = None
    rental_history: bool | None = None
    owners: int | None = None
    service_records: bool | None = None
    certified: bool | None = None
    other_urls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.vin = self.vin.strip().upper()
        self.make = normalize_make(self.make) if self.make else self.make
        self.model = self.model.strip() if self.model else self.model

    def to_dict(self) -> dict:
        return asdict(self)
