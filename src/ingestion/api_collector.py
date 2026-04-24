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
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Station:
    """Izmir measurement station metadata."""

    id: int
    name: str
    lat: float
    lon: float
    district: str


# TODO: move to config/stations.yaml, loaded at startup
IZMIR_STATIONS: list[Station] = [
    # Station(id=1, name="Konak", lat=38.4192, lon=27.1287, district="Konak"),
]


async def fetch_air_pollution(station: Station) -> dict[str, Any]:
    """Fetch latest air pollution measurement for a station.

    TODO: implement in Hafta 3.
    """
    raise NotImplementedError("Hafta 3: data-engineer agent implements this")
