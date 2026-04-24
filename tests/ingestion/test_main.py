"""Unit tests for `src.ingestion.main` (APScheduler entrypoint).

Covers the per-tick fan-out (`collect_and_publish`) and the lifecycle
glue in `run()`. The scheduler itself is not exercised in real time —
we mock `AsyncIOScheduler` and trigger `stop_event` synchronously so
the test suite stays under a second.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ingestion import main as main_module
from src.ingestion.api_collector import (
    AirPollutionComponents,
    AirPollutionRecord,
    StationReading,
    WeatherRecord,
)
from src.ingestion.kafka_producer import KafkaPublishError
from src.ingestion.stations import Station

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_reading(slug: str) -> StationReading:
    measured_at = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)
    station = Station(
        id=slug,
        name=slug.title(),
        district="Konak",
        lat=38.4 + 0.01,
        lon=27.1 + 0.01,
        category="urban_central",
    )
    return StationReading(
        station=station,
        air_pollution=AirPollutionRecord(
            aqi=2,
            components=AirPollutionComponents(
                co=250.0, no2=12.0, o3=60.0, so2=4.0, pm2_5=18.0, pm10=25.0
            ),
            measured_at=measured_at,
        ),
        weather=WeatherRecord(
            temperature_c=22.0,
            humidity_pct=60,
            pressure_hpa=1013,
            wind_speed_ms=3.0,
            wind_deg=180,
            condition="Clouds",
            description="scattered clouds",
            measured_at=measured_at,
        ),
    )


@pytest.fixture
def three_readings() -> list[StationReading]:
    return [_make_reading(slug) for slug in ("alsancak", "kemeralti", "bornova")]


@pytest.fixture
def mock_producer() -> MagicMock:
    """A `KafkaProducerWrapper` lookalike with publish/flush/close stubs."""
    producer = MagicMock(name="KafkaProducerWrapper")
    producer.publish_reading = MagicMock()
    producer.flush = MagicMock(return_value=0)
    producer.close = MagicMock()
    return producer


# ---------------------------------------------------------------------------
# collect_and_publish
# ---------------------------------------------------------------------------


async def test_collect_and_publish_publishes_each_reading(
    monkeypatch: pytest.MonkeyPatch,
    mock_producer: MagicMock,
    three_readings: list[StationReading],
) -> None:
    """Every reading from collect_all_stations is handed to the producer."""

    async def fake_collect() -> list[StationReading]:
        return three_readings

    monkeypatch.setattr(main_module, "collect_all_stations", fake_collect)

    published = await main_module.collect_and_publish(mock_producer)

    assert published == 3
    assert mock_producer.publish_reading.call_count == 3
    seen_ids = [call.args[0].station.id for call in mock_producer.publish_reading.call_args_list]
    assert seen_ids == ["alsancak", "kemeralti", "bornova"]
    mock_producer.flush.assert_called_once_with(timeout_seconds=5.0)


async def test_collect_and_publish_continues_on_publish_error(
    monkeypatch: pytest.MonkeyPatch,
    mock_producer: MagicMock,
    three_readings: list[StationReading],
) -> None:
    """A single publish failure must not abort sibling stations."""

    async def fake_collect() -> list[StationReading]:
        return three_readings

    monkeypatch.setattr(main_module, "collect_all_stations", fake_collect)

    # Second reading explodes; first + third should still be attempted.
    def publish_side_effect(reading: StationReading) -> None:
        if reading.station.id == "kemeralti":
            raise KafkaPublishError("simulated buffer full")

    mock_producer.publish_reading.side_effect = publish_side_effect

    published = await main_module.collect_and_publish(mock_producer)

    assert published == 2
    assert mock_producer.publish_reading.call_count == 3
    mock_producer.flush.assert_called_once_with(timeout_seconds=5.0)


async def test_collect_and_publish_zero_readings(
    monkeypatch: pytest.MonkeyPatch,
    mock_producer: MagicMock,
) -> None:
    """No readings → no publishes, but flush still fires (idempotent)."""

    async def fake_collect() -> list[StationReading]:
        return []

    monkeypatch.setattr(main_module, "collect_all_stations", fake_collect)

    published = await main_module.collect_and_publish(mock_producer)

    assert published == 0
    mock_producer.publish_reading.assert_not_called()
    mock_producer.flush.assert_called_once_with(timeout_seconds=5.0)


# ---------------------------------------------------------------------------
# run() — lifecycle plumbing
# ---------------------------------------------------------------------------


async def test_run_schedules_job_and_shuts_down_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run()` must schedule the job, await stop, then flush + shutdown."""
    fake_scheduler = MagicMock(name="AsyncIOScheduler")
    fake_scheduler.add_job = MagicMock()
    fake_scheduler.start = MagicMock()
    fake_scheduler.shutdown = MagicMock()

    fake_producer = MagicMock(name="KafkaProducerWrapper")
    fake_producer.close = MagicMock()

    monkeypatch.setattr(main_module, "AsyncIOScheduler", lambda: fake_scheduler)
    monkeypatch.setattr(main_module, "KafkaProducerWrapper", lambda: fake_producer)

    # Patch asyncio.Event so wait() returns immediately (simulating a
    # signal arriving the moment we start awaiting).
    fake_event = MagicMock()
    fake_event.set = MagicMock()
    fake_event.wait = AsyncMock()
    monkeypatch.setattr(asyncio, "Event", lambda: fake_event)

    await main_module.run()

    # Job registered exactly once with the expected callable + kwargs.
    fake_scheduler.add_job.assert_called_once()
    add_job_kwargs = fake_scheduler.add_job.call_args.kwargs
    assert add_job_kwargs["trigger"] == "interval"
    assert add_job_kwargs["args"] == [fake_producer]
    assert add_job_kwargs["max_instances"] == 1
    assert add_job_kwargs["coalesce"] is True
    assert fake_scheduler.add_job.call_args.args[0] is main_module.collect_and_publish

    # Scheduler started, then shut down with wait=False on signal.
    fake_scheduler.start.assert_called_once()
    fake_scheduler.shutdown.assert_called_once_with(wait=False)

    # Producer closed with a generous flush timeout.
    fake_producer.close.assert_called_once_with(timeout_seconds=10.0)


async def test_run_closes_producer_even_if_event_wait_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `await stop_event.wait()` raises, finally-block still cleans up."""
    fake_scheduler = MagicMock(name="AsyncIOScheduler")
    fake_producer = MagicMock(name="KafkaProducerWrapper")

    monkeypatch.setattr(main_module, "AsyncIOScheduler", lambda: fake_scheduler)
    monkeypatch.setattr(main_module, "KafkaProducerWrapper", lambda: fake_producer)

    fake_event = MagicMock()
    fake_event.wait = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(asyncio, "Event", lambda: fake_event)

    with pytest.raises(RuntimeError, match="boom"):
        await main_module.run()

    fake_scheduler.shutdown.assert_called_once_with(wait=False)
    fake_producer.close.assert_called_once_with(timeout_seconds=10.0)


# ---------------------------------------------------------------------------
# main() — sync wrapper
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_asyncio_run(coro: Any) -> None:
        # Close the coroutine to silence "coroutine was never awaited".
        captured["coro"] = coro
        coro.close()

    monkeypatch.setattr(asyncio, "run", fake_asyncio_run)

    rc = main_module.main()

    assert rc == 0
    assert captured["coro"] is not None


def test_main_returns_130_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_kbd(coro: Any) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", raise_kbd)

    assert main_module.main() == 130


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------


def test_configure_logging_respects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging as _logging

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    main_module._configure_logging()
    assert _logging.getLogger().level == _logging.DEBUG


def test_configure_logging_explicit_level_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging as _logging

    monkeypatch.delenv("LOG_LEVEL", raising=False)
    main_module._configure_logging(level="WARNING")
    assert _logging.getLogger().level == _logging.WARNING
