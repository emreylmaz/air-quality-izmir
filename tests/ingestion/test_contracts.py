"""Sprint 03 data-quality baseline (T10).

Until the full DQ framework lands in Hafta 12 (`src/quality/data_quality.py`),
this module pins the cross-cutting contracts that downstream consumers
(Spark streaming, Streamlit, Grafana) rely on. Every assertion here
represents an invariant whose violation should fail CI before the
ingestion layer ships:

1. **API response shape** — `StationReading` (pydantic) accepts the
   minimum OpenWeatherMap payload and rejects malformed components.
2. **Producer key format** — `{station_id}:{iso_hour}` is parseable by
   regex; downstream Spark dedupe relies on this.
3. **CSV loader row-count invariant** — every cleaned row has a known
   pollutant code, a UTC-aware timestamp, and a non-negative finite
   value. Insert payload preserves the cleaned row count exactly.
4. **Pollutant code closure** — schema seed, station catalog, and
   API model all agree on the six-pollutant universe.

These tests are intentionally redundant with module-level unit tests
(`test_api_collector`, `test_kafka_producer`, `test_csv_loader`); the
purpose is a single file the data-quality-engineer points at as the
"contract surface" so future schema changes have a clear failure
signal.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from src.ingestion.api_collector import (
    AirPollutionComponents,
    AirPollutionRecord,
    StationReading,
    WeatherRecord,
)
from src.ingestion.csv_loader import (
    _build_insert_payload,
    clean,
    read_csv,
    to_long_format,
)
from src.ingestion.kafka_producer import KafkaProducerWrapper
from src.ingestion.stations import Station, load_stations
from src.storage import SCHEMA_SQL

# Six-pollutant universe — the source of truth for every consumer.
EXPECTED_POLLUTANTS = {"pm25", "pm10", "no2", "so2", "o3", "co"}

# `<station_id>:<iso_hour_utc>` — Spark streaming dedupe key.
KEY_FORMAT_RE = re.compile(r"^[a-z][a-z0-9_]*:\d{4}-\d{2}-\d{2}T\d{2}:00:00\+00:00$")

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "izmir_sample_utf8.csv"


# ---------------------------------------------------------------------------
# Contract 1 — API response shape
# ---------------------------------------------------------------------------


class TestApiResponseContract:
    """`StationReading` must accept the minimum OWM payload and reject junk."""

    def test_minimum_valid_payload_parses(self) -> None:
        station = Station(
            id="konak",
            name="Konak",
            district="Konak",
            lat=38.4192,
            lon=27.1287,
            category="urban_traffic",
        )
        reading = StationReading(
            station=station,
            air_pollution=AirPollutionRecord(
                aqi=2,
                components=AirPollutionComponents(
                    co=250.0, no2=12.0, o3=60.0, so2=4.0, pm2_5=18.0, pm10=25.0
                ),
                measured_at=datetime(2026, 4, 25, tzinfo=UTC),
            ),
            weather=None,  # weather is optional per contract
        )
        assert reading.air_pollution.aqi == 2

    def test_aqi_outside_1_to_5_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AirPollutionRecord(
                aqi=99,  # OWM enum is 1..5
                components=AirPollutionComponents(
                    co=1.0, no2=1.0, o3=1.0, so2=1.0, pm2_5=1.0, pm10=1.0
                ),
                measured_at=datetime(2026, 4, 25, tzinfo=UTC),
            )

    def test_humidity_outside_0_to_100_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WeatherRecord(
                temperature_c=20.0,
                humidity_pct=150,
                pressure_hpa=1013,
                wind_speed_ms=2.0,
                wind_deg=180,
                condition="Clouds",
                description="few clouds",
                measured_at=datetime(2026, 4, 25, tzinfo=UTC),
            )

    def test_extra_fields_in_components_ignored(self) -> None:
        """Schema must tolerate OWM adding new components without breaking."""
        c = AirPollutionComponents.model_validate(
            {
                "co": 1.0,
                "no2": 1.0,
                "o3": 1.0,
                "so2": 1.0,
                "pm2_5": 1.0,
                "pm10": 1.0,
                "future_pollutant_x": 999.0,  # not in our schema yet
            }
        )
        assert not hasattr(c, "future_pollutant_x")


# ---------------------------------------------------------------------------
# Contract 2 — Producer key format
# ---------------------------------------------------------------------------


class TestProducerKeyContract:
    """`{station_id}:{iso_hour_utc}` is the dedupe contract Spark depends on."""

    def test_iso_hour_key_matches_regex(self) -> None:
        key = KafkaProducerWrapper._build_reading_key(
            station_id="konak",
            measured_at=datetime(2026, 4, 25, 14, 37, 12, tzinfo=UTC),
        )
        assert KEY_FORMAT_RE.match(key), f"key {key!r} violates contract"

    def test_minute_and_second_truncated_to_zero(self) -> None:
        key = KafkaProducerWrapper._build_reading_key(
            station_id="alsancak",
            measured_at=datetime(2026, 4, 25, 14, 59, 59, tzinfo=UTC),
        )
        assert key.endswith("T14:00:00+00:00")

    def test_every_catalog_station_id_passes_pattern(self) -> None:
        """Catalog ids must be valid prefixes for the producer key regex."""
        for station in load_stations():
            test_key = KafkaProducerWrapper._build_reading_key(
                station_id=station.id,
                measured_at=datetime(2026, 4, 25, tzinfo=UTC),
            )
            assert KEY_FORMAT_RE.match(
                test_key
            ), f"station id {station.id!r} produces invalid key {test_key!r}"


# ---------------------------------------------------------------------------
# Contract 3 — CSV loader invariants
# ---------------------------------------------------------------------------


class TestCsvLoaderContract:
    """Cleaned rows must satisfy schema NOT-NULL + non-negative invariants."""

    @pytest.fixture(scope="class")
    def cleaned_fixture(self) -> pd.DataFrame:
        df = read_csv(FIXTURE_PATH)
        return clean(to_long_format(df))

    def test_no_nan_values_after_clean(self, cleaned_fixture: pd.DataFrame) -> None:
        assert not cleaned_fixture["value"].isna().any()

    def test_no_negative_values_after_clean(self, cleaned_fixture: pd.DataFrame) -> None:
        assert (cleaned_fixture["value"] >= 0).all()

    def test_all_values_finite(self, cleaned_fixture: pd.DataFrame) -> None:
        import math

        assert all(math.isfinite(v) for v in cleaned_fixture["value"])

    def test_pollutant_codes_subset_of_universe(self, cleaned_fixture: pd.DataFrame) -> None:
        seen = set(cleaned_fixture["pollutant_code"].unique())
        assert seen <= EXPECTED_POLLUTANTS

    def test_timestamps_are_utc_aware(self, cleaned_fixture: pd.DataFrame) -> None:
        assert str(cleaned_fixture["measured_at"].dt.tz) == "UTC"

    def test_insert_payload_count_matches_cleaned_rows(self, cleaned_fixture: pd.DataFrame) -> None:
        """Insert payload row count == cleaned row count (no silent drop)."""
        seed_map = {code: i for i, code in enumerate(sorted(EXPECTED_POLLUTANTS), start=1)}
        rows = _build_insert_payload(cleaned_fixture, station_id=1, pollutant_ids=seed_map)
        assert len(rows) == len(cleaned_fixture)


# ---------------------------------------------------------------------------
# Contract 4 — Pollutant code closure across schema, catalog, model
# ---------------------------------------------------------------------------


class TestPollutantUniverseClosure:
    """Schema seed and CSV mapping must agree on the six-pollutant universe."""

    def test_schema_seeds_exactly_the_expected_codes(self) -> None:
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        # Capture quoted lowercase tokens between INSERT…ON CONFLICT.
        seed_match = re.search(r"INSERT\s+INTO\s+dim_pollutant\b.*?ON\s+CONFLICT", sql, re.I | re.S)
        assert seed_match is not None
        seed_block = seed_match.group(0)
        for code in EXPECTED_POLLUTANTS:
            assert f"'{code}'" in seed_block

    def test_csv_default_mapping_targets_only_known_codes(self) -> None:
        from src.ingestion.csv_loader import DEFAULT_POLLUTANT_COLUMN_MAP

        targets = set(DEFAULT_POLLUTANT_COLUMN_MAP.values())
        unknown = targets - EXPECTED_POLLUTANTS
        assert not unknown, f"csv mapping targets unknown codes: {unknown}"

    def test_air_pollution_components_cover_all_expected_pollutants(self) -> None:
        """Every code in the universe must be reachable from the OWM model.

        OWM names them differently (`pm2_5` vs schema `pm25`, plus `co` etc.);
        we assert presence by alias check rather than name equality.
        """
        owm_fields = set(AirPollutionComponents.model_fields.keys())
        # Translation table: schema code → OWM field name.
        schema_to_owm = {
            "pm25": "pm2_5",
            "pm10": "pm10",
            "no2": "no2",
            "so2": "so2",
            "o3": "o3",
            "co": "co",
        }
        missing = {code for code, owm in schema_to_owm.items() if owm not in owm_fields}
        assert not missing, f"OWM model missing fields for codes: {missing}"
