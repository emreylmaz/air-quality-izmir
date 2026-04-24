"""Unit tests for `src.ingestion.api_collector`.

All HTTP traffic is mocked with `respx`. No real OpenWeatherMap calls.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from src.config.settings import get_settings
from src.ingestion import api_collector
from src.ingestion.api_collector import (
    AIR_POLLUTION_URL,
    WEATHER_URL,
    AirPollutionRecord,
    StationReading,
    _is_retryable,
    _mask_url,
    _parse_air_pollution,
    _parse_weather,
    collect_all_stations,
    fetch_station_reading,
)
from src.ingestion.stations import Station

# asyncio_mode=auto in pyproject handles async tests automatically; no module
# marker needed (would warn on sync helper tests).

FAKE_API_KEY = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret


def _air_payload(epoch: int = 1_700_000_000) -> dict[str, Any]:
    """Sample OpenWeatherMap /air_pollution payload."""
    return {
        "coord": {"lat": 38.42, "lon": 27.13},
        "list": [
            {
                "main": {"aqi": 2},
                "components": {
                    "co": 250.0,
                    "no": 0.5,
                    "no2": 12.4,
                    "o3": 60.1,
                    "so2": 4.2,
                    "pm2_5": 18.3,
                    "pm10": 25.7,
                    "nh3": 1.1,
                },
                "dt": epoch,
            }
        ],
    }


def _weather_payload(epoch: int = 1_700_000_000) -> dict[str, Any]:
    """Sample OpenWeatherMap /weather payload."""
    return {
        "main": {"temp": 18.4, "humidity": 65, "pressure": 1015},
        "wind": {"speed": 3.2, "deg": 180},
        "weather": [{"main": "Clouds", "description": "scattered clouds"}],
        "dt": epoch,
    }


@pytest.fixture
def fake_station() -> Station:
    return Station(
        id="konak",
        name="Konak",
        district="Konak",
        lat=38.4192,
        lon=27.1287,
        category="urban_traffic",
    )


@pytest.fixture
def stations() -> list[Station]:
    """Six fake stations to mirror the real catalog without touching disk."""
    return [
        Station(
            id="konak",
            name="Konak",
            district="Konak",
            lat=38.4192,
            lon=27.1287,
            category="urban_traffic",
        ),
        Station(
            id="bornova",
            name="Bornova",
            district="Bornova",
            lat=38.4700,
            lon=27.2200,
            category="urban_residential",
        ),
        Station(
            id="karsiyaka",
            name="Karsiyaka",
            district="Karsiyaka",
            lat=38.4612,
            lon=27.1156,
            category="coastal_residential",
        ),
        Station(
            id="alsancak",
            name="Alsancak",
            district="Konak",
            lat=38.4380,
            lon=27.1430,
            category="urban_central",
        ),
        Station(
            id="bayrakli",
            name="Bayrakli",
            district="Bayrakli",
            lat=38.4615,
            lon=27.1670,
            category="urban_residential",
        ),
        Station(
            id="aliaga",
            name="Aliaga",
            district="Aliaga",
            lat=38.7990,
            lon=26.9720,
            category="industrial",
        ),
    ]


@pytest.fixture(autouse=True)
def _override_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force a known fake key into settings, bypassing the lru_cache."""
    monkeypatch.setenv("OPENWEATHER_API_KEY", FAKE_API_KEY)
    get_settings.cache_clear()
    # Sanity check: settings reflects our override.
    assert get_settings().openweather_api_key.get_secret_value() == FAKE_API_KEY
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_mask_url_redacts_appid() -> None:
    url = (
        "https://api.openweathermap.org/data/2.5/air_pollution"
        "?lat=38.4&lon=27.1&appid=" + FAKE_API_KEY
    )
    masked = _mask_url(url)
    assert FAKE_API_KEY not in masked
    assert "appid=***" in masked


def test_is_retryable_rules() -> None:
    fake_request = httpx.Request("GET", "https://example.test")
    for status in (429, 500, 502, 503):
        exc = httpx.HTTPStatusError(
            "boom", request=fake_request, response=httpx.Response(status, request=fake_request)
        )
        assert _is_retryable(exc) is True

    for status in (400, 401, 403, 404):
        exc = httpx.HTTPStatusError(
            "boom", request=fake_request, response=httpx.Response(status, request=fake_request)
        )
        assert _is_retryable(exc) is False

    transport = httpx.ConnectError("dns down")
    assert _is_retryable(transport) is True

    assert _is_retryable(ValueError("not http")) is False


def test_parse_air_pollution_happy_path() -> None:
    record = _parse_air_pollution(_air_payload(epoch=1_700_000_000))
    assert isinstance(record, AirPollutionRecord)
    assert record.aqi == 2
    assert record.components.pm2_5 == pytest.approx(18.3)
    assert record.measured_at.tzinfo is not None


def test_parse_air_pollution_missing_list_raises() -> None:
    with pytest.raises(ValueError, match="missing 'list'"):
        _parse_air_pollution({"coord": {}})


def test_parse_air_pollution_missing_dt_raises() -> None:
    payload = _air_payload()
    payload["list"][0].pop("dt")
    with pytest.raises(ValueError, match="missing 'dt'"):
        _parse_air_pollution(payload)


def test_parse_air_pollution_invalid_aqi_raises() -> None:
    payload = _air_payload()
    payload["list"][0]["main"]["aqi"] = 9  # outside 1-5
    with pytest.raises(ValidationError):
        _parse_air_pollution(payload)


def test_parse_weather_normalises_360_wind_deg() -> None:
    payload = _weather_payload()
    payload["wind"]["deg"] = 360
    record = _parse_weather(payload)
    assert record.wind_deg == 0


def test_parse_weather_missing_dt_raises() -> None:
    payload = _weather_payload()
    payload.pop("dt")
    with pytest.raises(ValueError, match="missing 'dt'"):
        _parse_weather(payload)


# ---------------------------------------------------------------------------
# fetch_station_reading — happy + retry behaviour
# ---------------------------------------------------------------------------


@respx.mock
async def test_happy_path_returns_validated_reading(fake_station: Station) -> None:
    air_route = respx.get(AIR_POLLUTION_URL).mock(
        return_value=httpx.Response(200, json=_air_payload())
    )
    weather_route = respx.get(WEATHER_URL).mock(
        return_value=httpx.Response(200, json=_weather_payload())
    )

    async with httpx.AsyncClient() as client:
        reading = await fetch_station_reading(fake_station, client=client)

    assert air_route.called
    assert weather_route.called
    assert isinstance(reading, StationReading)
    assert reading.station.id == "konak"
    assert reading.air_pollution.aqi == 2
    assert reading.air_pollution.components.pm10 == pytest.approx(25.7)
    assert reading.weather is not None
    assert reading.weather.temperature_c == pytest.approx(18.4)
    assert reading.weather.condition == "Clouds"


@respx.mock
async def test_skip_weather_when_disabled(fake_station: Station) -> None:
    air_route = respx.get(AIR_POLLUTION_URL).mock(
        return_value=httpx.Response(200, json=_air_payload())
    )
    weather_route = respx.get(WEATHER_URL).mock(
        return_value=httpx.Response(200, json=_weather_payload())
    )

    async with httpx.AsyncClient() as client:
        reading = await fetch_station_reading(fake_station, client=client, include_weather=False)

    assert air_route.called
    assert not weather_route.called
    assert reading.weather is None


@respx.mock
async def test_retry_on_429_then_success(
    fake_station: Station, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Speed up tenacity backoff for tests.
    monkeypatch.setattr(
        "src.ingestion.api_collector.wait_exponential", lambda **_: lambda *_a, **_kw: 0
    )  # noqa: E501

    air_route = respx.get(AIR_POLLUTION_URL).mock(
        side_effect=[
            httpx.Response(429, json={"message": "rate limited"}),
            httpx.Response(200, json=_air_payload()),
        ]
    )
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, json=_weather_payload()))

    async with httpx.AsyncClient() as client:
        reading = await fetch_station_reading(fake_station, client=client)

    assert air_route.call_count == 2
    assert reading.air_pollution.aqi == 2


@respx.mock
async def test_retry_on_5xx_then_success(fake_station: Station) -> None:
    air_route = respx.get(AIR_POLLUTION_URL).mock(
        side_effect=[
            httpx.Response(500, json={"message": "boom"}),
            httpx.Response(502, json={"message": "boom"}),
            httpx.Response(200, json=_air_payload()),
        ]
    )
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, json=_weather_payload()))

    async with httpx.AsyncClient() as client:
        reading = await fetch_station_reading(fake_station, client=client)

    assert air_route.call_count == 3
    assert reading.air_pollution.aqi == 2


@respx.mock
async def test_fail_after_max_retries(fake_station: Station) -> None:
    air_route = respx.get(AIR_POLLUTION_URL).mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await fetch_station_reading(fake_station, client=client)

    assert excinfo.value.response.status_code == 500
    # tenacity stop_after_attempt(3) → exactly 3 calls.
    assert air_route.call_count == 3


@respx.mock
async def test_no_retry_on_404(fake_station: Station) -> None:
    air_route = respx.get(AIR_POLLUTION_URL).mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await fetch_station_reading(fake_station, client=client)

    assert excinfo.value.response.status_code == 404
    # No retry on 4xx (except 429) — exactly one call.
    assert air_route.call_count == 1


@respx.mock
async def test_no_retry_on_401(fake_station: Station) -> None:
    air_route = respx.get(AIR_POLLUTION_URL).mock(
        return_value=httpx.Response(401, json={"message": "bad key"})
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_station_reading(fake_station, client=client)

    assert air_route.call_count == 1


# ---------------------------------------------------------------------------
# collect_all_stations
# ---------------------------------------------------------------------------


@respx.mock
async def test_collect_all_stations_partial_failure(
    stations: list[Station],
) -> None:
    """One station fails 3x with 500 — others succeed; failures are skipped."""
    aliaga_lon = 26.972  # noqa: F841 — readability marker

    def air_handler(request: httpx.Request) -> httpx.Response:
        # `aliaga` station has lon=26.972 — fail those calls.
        if request.url.params.get("lon") == "26.972":
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json=_air_payload())

    respx.get(AIR_POLLUTION_URL).mock(side_effect=air_handler)
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, json=_weather_payload()))

    readings = await collect_all_stations(stations=stations)

    assert len(readings) == 5
    assert all(r.station.id != "aliaga" for r in readings)


async def test_collect_all_stations_empty_list() -> None:
    readings = await collect_all_stations(stations=[])
    assert readings == []


@respx.mock
async def test_collect_all_stations_loads_default_catalog(
    monkeypatch: pytest.MonkeyPatch, stations: list[Station]
) -> None:
    """When stations=None, the YAML catalog loader is invoked."""
    monkeypatch.setattr(api_collector, "get_izmir_stations", lambda: stations)
    respx.get(AIR_POLLUTION_URL).mock(return_value=httpx.Response(200, json=_air_payload()))
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, json=_weather_payload()))

    readings = await collect_all_stations(include_weather=True)

    assert len(readings) == len(stations)


# ---------------------------------------------------------------------------
# Logging — secret hygiene
# ---------------------------------------------------------------------------


@respx.mock
async def test_api_key_masked_in_logs(
    fake_station: Station, caplog: pytest.LogCaptureFixture
) -> None:
    """API key must never appear unmasked in any log record."""
    respx.get(AIR_POLLUTION_URL).mock(
        side_effect=[
            httpx.Response(500, json={"message": "boom"}),
            httpx.Response(200, json=_air_payload()),
        ]
    )
    respx.get(WEATHER_URL).mock(return_value=httpx.Response(200, json=_weather_payload()))

    caplog.set_level(logging.DEBUG, logger="src.ingestion.api_collector")
    async with httpx.AsyncClient() as client:
        await fetch_station_reading(fake_station, client=client)

    full_log = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_API_KEY not in full_log
    # Mask token must be present (we know we logged at least one retry warning).
    assert "appid=***" in full_log
    # Defensive: scan for any leaked 32-char hex appid value.
    assert not re.search(r"appid=[0-9a-f]{32}", full_log)


@respx.mock
async def test_api_key_masked_in_logs_on_4xx(
    fake_station: Station, caplog: pytest.LogCaptureFixture
) -> None:
    """Non-retryable error path must also mask the key."""
    respx.get(AIR_POLLUTION_URL).mock(return_value=httpx.Response(401, json={"message": "bad key"}))

    caplog.set_level(logging.DEBUG, logger="src.ingestion.api_collector")
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_station_reading(fake_station, client=client)

    full_log = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_API_KEY not in full_log
    assert not re.search(r"appid=[0-9a-f]{32}", full_log)
