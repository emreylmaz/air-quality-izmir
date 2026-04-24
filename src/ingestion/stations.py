"""Izmir measurement station catalog.

Loads `config/stations.yaml` and validates each entry via pydantic v2.
The catalog is the single source of truth for station id/lat/lon — no
hardcoded coordinates elsewhere in `src/ingestion/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

StationCategory = Literal[
    "urban_traffic",
    "urban_residential",
    "coastal_residential",
    "urban_central",
    "industrial",
]

DEFAULT_STATIONS_PATH = Path("config/stations.yaml")


class Station(BaseModel):
    """Izmir measurement station metadata.

    Lat/lon ranges constrain to the Izmir metropolitan bounding box so a
    typo (e.g. swapping lat/lon) fails validation early.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    district: str = Field(min_length=1)
    lat: float = Field(ge=38.0, le=38.8)
    lon: float = Field(ge=26.8, le=27.5)
    category: StationCategory


def load_stations(path: Path = DEFAULT_STATIONS_PATH) -> list[Station]:
    """Load and validate the Izmir station catalog.

    Args:
        path: Path to the YAML catalog (defaults to `config/stations.yaml`).

    Returns:
        List of validated `Station` instances in file order.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If the YAML payload shape is invalid (missing `stations`
            key, or duplicate station ids).
        pydantic.ValidationError: If any record fails field validation.
    """
    with path.open("r", encoding="utf-8") as fh:
        payload: Any = yaml.safe_load(fh)

    if not isinstance(payload, dict) or "stations" not in payload:
        raise ValueError(f"{path}: expected top-level mapping with 'stations' key")

    raw_records: Any = payload["stations"]
    if not isinstance(raw_records, list):
        raise ValueError(f"{path}: 'stations' must be a list")

    stations: list[Station] = [Station.model_validate(record) for record in raw_records]

    seen_ids: set[str] = set()
    for station in stations:
        if station.id in seen_ids:
            raise ValueError(f"{path}: duplicate station id '{station.id}'")
        seen_ids.add(station.id)

    return stations
