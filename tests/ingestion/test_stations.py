"""Tests for the Izmir station catalog loader."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.ingestion.stations import Station, load_stations

CATALOG_PATH = Path("config/stations.yaml")


@pytest.fixture(scope="module")
def stations() -> list[Station]:
    """Load the real catalog once per module."""
    return load_stations(CATALOG_PATH)


def test_catalog_file_exists() -> None:
    """`config/stations.yaml` must be checked into the repo."""
    assert CATALOG_PATH.is_file(), f"missing catalog: {CATALOG_PATH}"


def test_load_returns_six_stations(stations: list[Station]) -> None:
    """The H3 Izmir catalog ships with 6 curated stations."""
    assert len(stations) == 6


def test_load_returns_list_of_station_models(stations: list[Station]) -> None:
    """Loader contract: `list[Station]` (pydantic-validated)."""
    assert isinstance(stations, list)
    assert all(isinstance(s, Station) for s in stations)


def test_station_ids_are_unique(stations: list[Station]) -> None:
    """`id` is the Kafka key prefix — duplicates would corrupt partitioning."""
    ids = [s.id for s in stations]
    duplicates = [item for item, count in Counter(ids).items() if count > 1]
    assert not duplicates, f"duplicate station ids: {duplicates}"


def test_lat_lon_within_izmir_bbox(stations: list[Station]) -> None:
    """All stations must fall inside the Izmir metropolitan bbox."""
    for station in stations:
        assert 38.0 <= station.lat <= 38.8, f"{station.id}: lat {station.lat} out of range"
        assert 26.8 <= station.lon <= 27.5, f"{station.id}: lon {station.lon} out of range"


def test_at_least_one_industrial_station(stations: list[Station]) -> None:
    """Aliağa (rafineri + demir-çelik) gives the catalog an industrial profile."""
    industrial = [s for s in stations if s.category == "industrial"]
    assert industrial, "expected at least one industrial-category station (Aliaga)"
    assert any(s.id == "aliaga" for s in industrial)


def test_station_id_is_snake_case_slug(stations: list[Station]) -> None:
    """Kafka keys join id with `:` — uppercase or hyphens would surprise consumers."""
    for station in stations:
        assert station.id.islower()
        assert " " not in station.id
        assert "-" not in station.id


def test_invalid_lat_rejected(tmp_path: Path) -> None:
    """Out-of-bbox lat must raise pydantic ValidationError."""
    bad = tmp_path / "stations.yaml"
    bad.write_text(
        "stations:\n"
        "  - id: somewhere\n"
        "    name: Somewhere\n"
        "    district: Nowhere\n"
        "    lat: 41.0\n"  # Istanbul, not Izmir
        "    lon: 27.0\n"
        "    category: urban_traffic\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_stations(bad)


def test_invalid_category_rejected(tmp_path: Path) -> None:
    """Unknown category strings must fail Literal validation."""
    bad = tmp_path / "stations.yaml"
    bad.write_text(
        "stations:\n"
        "  - id: somewhere\n"
        "    name: Somewhere\n"
        "    district: Konak\n"
        "    lat: 38.4\n"
        "    lon: 27.1\n"
        "    category: rural_pasture\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_stations(bad)


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    """Loader must surface duplicate ids before they reach Kafka."""
    bad = tmp_path / "stations.yaml"
    bad.write_text(
        "stations:\n"
        "  - id: konak\n"
        "    name: Konak A\n"
        "    district: Konak\n"
        "    lat: 38.4\n"
        "    lon: 27.1\n"
        "    category: urban_traffic\n"
        "  - id: konak\n"
        "    name: Konak B\n"
        "    district: Konak\n"
        "    lat: 38.42\n"
        "    lon: 27.13\n"
        "    category: urban_central\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate station id"):
        load_stations(bad)
