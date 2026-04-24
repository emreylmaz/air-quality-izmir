"""OpenWeatherMap API collector — async httpx client with tenacity retry.

Responsibilities:
- Fetch air pollution (`/data/2.5/air_pollution`) and current weather
  (`/data/2.5/weather`) for the Izmir station catalog.
- Validate responses with pydantic v2 models.
- Retry on transient errors (HTTP 429, 5xx, transport errors) using
  `tenacity.AsyncRetrying` with exponential backoff. 4xx (except 429) are
  raised immediately — wrong key or bad station should fail fast.
- Never log or surface the raw API key. Query strings are sanitised before
  any logging.

Public API:
    fetch_station_reading(station, *, client, include_weather=True)
    collect_all_stations(stations=None, *, timeout_seconds=10.0)

Downstream (Task 4) wraps these and routes invalid records to the DLQ topic.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.config.settings import get_settings
from src.ingestion.stations import Station, load_stations

_LOG = logging.getLogger(__name__)

AIR_POLLUTION_URL = "https://api.openweathermap.org/data/2.5/air_pollution"
WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"

_APPID_RE = re.compile(r"(appid=)[^&\s]+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class AirPollutionComponents(BaseModel):
    """Pollutant concentrations in µg/m³ as returned by OpenWeatherMap."""

    model_config = ConfigDict(extra="ignore")

    co: float
    no: float | None = None
    no2: float
    o3: float
    so2: float
    pm2_5: float
    pm10: float
    nh3: float | None = None


class AirPollutionRecord(BaseModel):
    """One air pollution measurement (single timestamp)."""

    model_config = ConfigDict(extra="ignore")

    aqi: int = Field(ge=1, le=5)
    components: AirPollutionComponents
    measured_at: datetime


class WeatherRecord(BaseModel):
    """Subset of the current-weather payload we care about."""

    model_config = ConfigDict(extra="ignore")

    temperature_c: float
    humidity_pct: int = Field(ge=0, le=100)
    pressure_hpa: int = Field(gt=0)
    wind_speed_ms: float = Field(ge=0)
    wind_deg: int | None = Field(default=None, ge=0, le=360)
    condition: str
    description: str
    measured_at: datetime

    @field_validator("wind_deg")
    @classmethod
    def _normalise_wind_deg(cls, v: int | None) -> int | None:
        # OWM occasionally returns 360 — normalise to 0 for downstream math.
        if v == 360:
            return 0
        return v


class StationReading(BaseModel):
    """Combined per-station reading published downstream."""

    model_config = ConfigDict(extra="forbid")

    station: Station
    air_pollution: AirPollutionRecord
    weather: WeatherRecord | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_url(url: str) -> str:
    """Replace `appid=<value>` with `appid=***` for safe logging."""
    return _APPID_RE.sub(r"\1***", url)


def _is_retryable(exc: BaseException) -> bool:
    """Retry on transport errors and HTTP 429/5xx; fail fast on 4xx."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status: int = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return False


def _parse_air_pollution(payload: dict[str, Any]) -> AirPollutionRecord:
    """Extract the first record from an OWM air_pollution response."""
    items = payload.get("list") or []
    if not items:
        raise ValueError("air_pollution response missing 'list' entries")
    first = items[0]
    main = first.get("main", {})
    components = first.get("components", {})
    dt_epoch = first.get("dt")
    if dt_epoch is None:
        raise ValueError("air_pollution record missing 'dt' epoch")
    return AirPollutionRecord(
        aqi=int(main["aqi"]),
        components=AirPollutionComponents.model_validate(components),
        measured_at=datetime.fromtimestamp(int(dt_epoch), tz=UTC),
    )


def _parse_weather(payload: dict[str, Any]) -> WeatherRecord:
    """Extract the fields we use from an OWM /weather response."""
    main = payload.get("main") or {}
    wind = payload.get("wind") or {}
    weather_list = payload.get("weather") or []
    weather0 = weather_list[0] if weather_list else {}
    dt_epoch = payload.get("dt")
    if dt_epoch is None:
        raise ValueError("weather response missing 'dt' epoch")
    return WeatherRecord(
        temperature_c=float(main["temp"]),
        humidity_pct=int(main["humidity"]),
        pressure_hpa=int(main["pressure"]),
        wind_speed_ms=float(wind.get("speed", 0.0)),
        wind_deg=int(wind["deg"]) if "deg" in wind else None,
        condition=str(weather0.get("main", "")),
        description=str(weather0.get("description", "")),
        measured_at=datetime.fromtimestamp(int(dt_epoch), tz=UTC),
    )


async def _request_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    *,
    station_id: str,
) -> dict[str, Any]:
    """Issue a GET with bounded retry. Returns parsed JSON dict.

    Logs every retry attempt with the masked URL (no API key leaks).
    """
    # Pre-build masked URL including query for log breadcrumbs. We never log
    # the live `params` dict because it contains the API key.
    full_request_url = str(httpx.URL(url, params=params))
    safe_url = _mask_url(full_request_url)

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    ):
        with attempt:
            attempt_no = attempt.retry_state.attempt_number
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if _is_retryable(exc):
                    _LOG.warning(
                        "openweather request failed (will retry): "
                        "station=%s url=%s attempt=%d status=%d",
                        station_id,
                        safe_url,
                        attempt_no,
                        status,
                    )
                else:
                    _LOG.error(
                        "openweather request failed (non-retryable): "
                        "station=%s url=%s status=%d",
                        station_id,
                        safe_url,
                        status,
                    )
                raise
            except httpx.TransportError as exc:
                _LOG.warning(
                    "openweather transport error (will retry): "
                    "station=%s url=%s attempt=%d error=%s",
                    station_id,
                    safe_url,
                    attempt_no,
                    type(exc).__name__,
                )
                raise

            data: Any = response.json()
            if not isinstance(data, dict):
                raise ValueError(
                    f"unexpected response shape for station={station_id}: "
                    f"expected dict, got {type(data).__name__}"
                )
            _LOG.debug(
                "openweather request ok: station=%s url=%s attempt=%d",
                station_id,
                safe_url,
                attempt_no,
            )
            return data

    # Unreachable: AsyncRetrying with reraise=True either returns or raises.
    raise RuntimeError("retry loop exited without result")  # pragma: no cover


# ---------------------------------------------------------------------------
# Public collectors
# ---------------------------------------------------------------------------


def get_izmir_stations() -> list[Station]:
    """Return the Izmir station catalog (lazy filesystem read)."""
    return load_stations()


async def fetch_station_reading(
    station: Station,
    *,
    client: httpx.AsyncClient,
    include_weather: bool = True,
) -> StationReading:
    """Fetch air pollution (+ optional weather) for a single station.

    Args:
        station: Validated `Station` from the catalog.
        client: Caller-owned async HTTP client (allows connection reuse).
        include_weather: When False, the `/weather` call is skipped and
            `StationReading.weather` is None.

    Returns:
        Validated `StationReading`.

    Raises:
        httpx.HTTPStatusError: After max retries, or immediately on 4xx.
        httpx.TransportError: After max retries.
        pydantic.ValidationError / ValueError: On malformed payload.
    """
    api_key = get_settings().openweather_api_key.get_secret_value()
    base_params: dict[str, Any] = {
        "lat": station.lat,
        "lon": station.lon,
        "appid": api_key,
    }

    air_payload = await _request_with_retry(
        client,
        AIR_POLLUTION_URL,
        params=base_params,
        station_id=station.id,
    )
    air_record = _parse_air_pollution(air_payload)

    weather_record: WeatherRecord | None = None
    if include_weather:
        weather_params = dict(base_params)
        weather_params["units"] = "metric"
        weather_payload = await _request_with_retry(
            client,
            WEATHER_URL,
            params=weather_params,
            station_id=station.id,
        )
        weather_record = _parse_weather(weather_payload)

    return StationReading(
        station=station,
        air_pollution=air_record,
        weather=weather_record,
    )


async def collect_all_stations(
    stations: Sequence[Station] | None = None,
    *,
    timeout_seconds: float = 10.0,
    include_weather: bool = True,
) -> list[StationReading]:
    """Fetch readings for every station in parallel.

    Per-station failures are logged and skipped — they do not abort sibling
    stations. Task 4 (kafka_producer) will route those to a DLQ topic; here
    we only emit a warning.

    Args:
        stations: Optional override (defaults to the YAML catalog).
        timeout_seconds: Per-request timeout for the shared client.
        include_weather: When False, weather endpoint is skipped.

    Returns:
        Successful `StationReading`s in catalog order.
    """
    targets = list(stations) if stations is not None else get_izmir_stations()
    if not targets:
        return []

    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            fetch_station_reading(s, client=client, include_weather=include_weather)
            for s in targets
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    readings: list[StationReading] = []
    for station, result in zip(targets, results, strict=True):
        if isinstance(result, BaseException):
            err = result.__cause__ if isinstance(result, RetryError) else result
            _LOG.warning(
                "station fetch failed (skipped): station=%s error_type=%s",
                station.id,
                type(err).__name__,
            )
            continue
        readings.append(result)

    return readings


__all__ = [
    "AIR_POLLUTION_URL",
    "WEATHER_URL",
    "AirPollutionComponents",
    "AirPollutionRecord",
    "StationReading",
    "WeatherRecord",
    "collect_all_stations",
    "fetch_station_reading",
    "get_izmir_stations",
]
