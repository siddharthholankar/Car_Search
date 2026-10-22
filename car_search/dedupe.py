"""Merge the same car seen on several sites into one record, keyed by VIN."""

from __future__ import annotations

from dataclasses import fields

from .models import Listing

_FLAGS = ("accident_reported", "salvage_or_rebuilt", "repossession", "rental_history",
          "service_records", "certified")


def dedupe(listings: list[Listing]) -> list[Listing]:
    """Keep the cheapest copy of each VIN, filling gaps from the other copies.

    History flags are OR-ed (any source reporting an accident counts), owners
    takes the highest count, and every other copy's URL is kept in `other_urls`.
    """
    groups: dict[str, list[Listing]] = {}
    for listing in listings:
        if len(listing.vin) == 17:
            groups.setdefault(listing.vin, []).append(listing)

    merged = []
    for copies in groups.values():
        copies.sort(key=lambda l: l.price)
        best = Listing(**copies[0].to_dict())
        for other in copies[1:]:
            for f in fields(Listing):
                if getattr(best, f.name) is None and getattr(other, f.name) is not None:
                    setattr(best, f.name, getattr(other, f.name))
            for flag in _FLAGS:
                if getattr(other, flag):
                    setattr(best, flag, True)
            if other.owners is not None:
                best.owners = max(best.owners or 0, other.owners)
            if other.days_on_market is not None:
                best.days_on_market = max(best.days_on_market or 0, other.days_on_market)
        best.other_urls = sorted({c.url for c in copies[1:] if c.url and c.url != best.url})
        merged.append(best)
    return merged
