"""OpenWeatherMap Air Pollution API collector.

TODO (Hafta 3): Full implementation by `data-engineer` agent.

Responsibilities:
- Fetch air pollution data for Izmir stations (lat/lon list)
- Validate response via pydantic models
- Retry on 429/5xx with tenacity
- Publish to Kafka via KafkaProducer
"""

from __future__ import annotations

import logging
from typing import Any

from src.ingestion.stations import Station, load_stations

logger = logging.getLogger(__name__)


def get_izmir_stations() -> list[Station]:
    """Return the Izmir station catalog.

    Lazy loader so module import does not touch the filesystem; tests and
    the scheduler call this on demand.
    """
    return load_stations()


async def fetch_air_pollution(station: Station) -> dict[str, Any]:
    """Fetch latest air pollution measurement for a station.

    TODO: implement in Hafta 3 (Task 3).
    """
    raise NotImplementedError("Hafta 3: data-engineer agent implements this")
