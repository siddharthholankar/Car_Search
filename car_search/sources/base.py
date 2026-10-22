from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import SearchConfig
from ..models import Listing


class Source(ABC):
    name: str

    @abstractmethod
    def fetch(self, search: SearchConfig) -> list[Listing]:
        """Return every listing matching `search`."""


def matches(listing: Listing, search: SearchConfig) -> bool:
    """Apply the search filters locally, for sources that can't filter server-side."""
    if listing.price <= 0 or listing.price > search.max_price:
        return False
    if listing.miles > search.max_miles or listing.year < search.min_year:
        return False
    if search.max_year and listing.year > search.max_year:
        return False
    if listing.distance_miles is not None and listing.distance_miles > search.radius_miles:
        return False
    wanted = {(v.make.lower(), v.model.lower()) for v in search.vehicles}
    return (listing.make.lower(), listing.model.lower()) in wanted
