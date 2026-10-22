"""Listing sources. Each returns normalized `Listing` objects for a search."""

from __future__ import annotations

from ..config import Config
from .base import Source
from .dealer_feed import DealerFeedSource
from .marketcheck import MarketCheckSource
from .sample import SampleSource


def build_sources(config: Config) -> list[Source]:
    sources: list[Source] = []
    for name in config.sources:
        if name == "sample":
            sources.append(SampleSource())
        elif name == "marketcheck":
            if not config.marketcheck_api_key:
                raise ValueError("marketcheck is enabled but no API key is set (MARKETCHECK_API_KEY)")
            sources.append(MarketCheckSource(config.marketcheck_api_key, config.marketcheck_max_pages))
        elif name == "dealer_feed":
            sources.append(DealerFeedSource(config.dealer_feed_urls))
        else:
            raise ValueError(f"unknown source: {name}")
    return sources


__all__ = ["Source", "SampleSource", "MarketCheckSource", "DealerFeedSource", "build_sources"]
