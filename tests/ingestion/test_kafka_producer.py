"""Unit tests for `src.ingestion.kafka_producer`.

The real `confluent_kafka.Producer` is replaced with a `MagicMock` via
`monkeypatch` so tests run with no broker. An optional integration test
(skipped by default) round-trips through a real broker when one is
available via `KAFKA_INTEGRATION_BOOTSTRAP`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from src.ingestion import kafka_producer as kp_module
from src.ingestion.api_collector import (
    AirPollutionComponents,
    AirPollutionRecord,
    StationReading,
    WeatherRecord,
)
from src.ingestion.kafka_producer import KafkaProducerWrapper, KafkaPublishError
from src.ingestion.stations import Station

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _DemoModel(BaseModel):
    station_id: str
    value: float


@pytest.fixture
def mock_producer(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """Patch `confluent_kafka.Producer` with a MagicMock for the wrapper."""
    instance = MagicMock(name="ProducerInstance")
    instance.produce = MagicMock()
    instance.poll = MagicMock()
    instance.flush = MagicMock(return_value=0)

    factory = MagicMock(name="ProducerFactory", return_value=instance)
    monkeypatch.setattr(kp_module, "Producer", factory)

    # Capture last-config for assertion convenience.
    instance._init_config_factory = factory
    yield instance


@pytest.fixture
def station() -> Station:
    return Station(
        id="alsancak",
        name="Alsancak",
        district="Konak",
        lat=38.435,
        lon=27.142,
        category="urban_central",
    )


@pytest.fixture
def station_reading(station: Station) -> StationReading:
    measured_at = datetime(2026, 4, 25, 14, 37, 12, tzinfo=UTC)
    return StationReading(
        station=station,
        air_pollution=AirPollutionRecord(
            aqi=2,
            components=AirPollutionComponents(
                co=250.0,
                no=0.5,
                no2=12.4,
                o3=60.1,
                so2=4.2,
                pm2_5=18.3,
                pm10=25.7,
                nh3=1.1,
            ),
            measured_at=measured_at,
        ),
        weather=WeatherRecord(
            temperature_c=22.0,
            humidity_pct=60,
            pressure_hpa=1013,
            wind_speed_ms=3.4,
            wind_deg=180,
            condition="Clouds",
            description="scattered clouds",
            measured_at=measured_at,
        ),
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_publish_serializes_basemodel(mock_producer: MagicMock) -> None:
    wrapper = KafkaProducerWrapper(default_topic="air-quality-raw")
    payload = _DemoModel(station_id="alsancak", value=12.5)

    wrapper.publish(value=payload, key="alsancak:2026-04-25T14:00:00+00:00")

    assert mock_producer.produce.call_count == 1
    kwargs = mock_producer.produce.call_args.kwargs
    assert kwargs["topic"] == "air-quality-raw"
    assert kwargs["key"] == b"alsancak:2026-04-25T14:00:00+00:00"
    decoded: dict[str, Any] = json.loads(kwargs["value"].decode("utf-8"))
    assert decoded == {"station_id": "alsancak", "value": 12.5}


def test_publish_serializes_mapping(mock_producer: MagicMock) -> None:
    wrapper = KafkaProducerWrapper(default_topic="topic-a")
    when = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)

    wrapper.publish(
        value={"station_id": "kemeralti", "measured_at": when, "pm10": 31.2},
        key="kemeralti:2026-04-25T12:00:00+00:00",
    )

    payload_bytes: bytes = mock_producer.produce.call_args.kwargs["value"]
    decoded: dict[str, Any] = json.loads(payload_bytes.decode("utf-8"))
    # `default=str` keeps datetime values JSON-friendly without losing info.
    assert decoded["station_id"] == "kemeralti"
    assert decoded["measured_at"].startswith("2026-04-25 12:00:00")
    assert decoded["pm10"] == 31.2


# ---------------------------------------------------------------------------
# Topic routing
# ---------------------------------------------------------------------------


def test_publish_uses_default_topic(mock_producer: MagicMock) -> None:
    wrapper = KafkaProducerWrapper(default_topic="air-quality-raw")
    wrapper.publish(value={"hello": "world"}, key="k")
    assert mock_producer.produce.call_args.kwargs["topic"] == "air-quality-raw"


def test_publish_explicit_topic_overrides_default(mock_producer: MagicMock) -> None:
    wrapper = KafkaProducerWrapper(default_topic="air-quality-raw")
    wrapper.publish(value={"hello": "world"}, key="k", topic="weather-raw")
    assert mock_producer.produce.call_args.kwargs["topic"] == "weather-raw"


def test_publish_routes_to_dlq_on_serialization_failure(
    mock_producer: MagicMock,
) -> None:
    wrapper = KafkaProducerWrapper(
        default_topic="air-quality-raw",
        dlq_topic="air-quality-dlq",
    )

    # `object()` is neither a BaseModel nor a Mapping → TypeError in
    # _serialize → routed to DLQ.
    wrapper.publish(value=object(), key="bad-key")  # type: ignore[arg-type]

    assert mock_producer.produce.call_count == 1
    call = mock_producer.produce.call_args.kwargs
    assert call["topic"] == "air-quality-dlq"
    envelope: dict[str, Any] = json.loads(call["value"].decode("utf-8"))
    assert "error" in envelope and "raw" in envelope
    assert "unsupported value type" in envelope["error"]
    # Live topic is never touched on serialization failure.
    topics = [c.kwargs["topic"] for c in mock_producer.produce.call_args_list]
    assert "air-quality-raw" not in topics


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def test_publish_reading_builds_iso_hour_key(
    mock_producer: MagicMock,
    station_reading: StationReading,
) -> None:
    wrapper = KafkaProducerWrapper(default_topic="air-quality-raw")
    wrapper.publish_reading(station_reading)

    key_bytes: bytes = mock_producer.produce.call_args.kwargs["key"]
    # Hour-truncated, UTC-aware, no minute/second components.
    assert key_bytes == b"alsancak:2026-04-25T14:00:00+00:00"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_flush_returns_pending_count(mock_producer: MagicMock) -> None:
    wrapper = KafkaProducerWrapper()
    mock_producer.flush.return_value = 3
    assert wrapper.flush(timeout_seconds=1.5) == 3
    mock_producer.flush.assert_called_with(1.5)


def test_close_calls_flush_via_context_manager(mock_producer: MagicMock) -> None:
    with KafkaProducerWrapper() as wrapper:
        wrapper.publish(value={"x": 1}, key="k")

    mock_producer.flush.assert_called()
    # After exit, further publishes raise.
    with pytest.raises(KafkaPublishError):
        wrapper.publish(value={"x": 1}, key="k")


def test_close_is_idempotent(mock_producer: MagicMock) -> None:
    wrapper = KafkaProducerWrapper()
    wrapper.close()
    wrapper.close()  # should not raise nor double-flush
    assert mock_producer.flush.call_count == 1


# ---------------------------------------------------------------------------
# Producer config
# ---------------------------------------------------------------------------


def test_idempotence_enabled_in_config(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(kp_module, "Producer", factory)

    KafkaProducerWrapper(
        bootstrap_servers="broker:9092",
        client_id="aqi-test",
    )

    assert factory.call_count == 1
    config: dict[str, Any] = factory.call_args.args[0]
    assert config["bootstrap.servers"] == "broker:9092"
    assert config["client.id"] == "aqi-test"
    assert config["acks"] == "all"
    assert config["enable.idempotence"] is True
    assert config["retries"] == 5
    assert config["compression.type"] == "gzip"


def test_extra_config_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(kp_module, "Producer", factory)

    KafkaProducerWrapper(
        extra_config={"linger.ms": 500, "security.protocol": "SASL_SSL"},
    )

    config: dict[str, Any] = factory.call_args.args[0]
    assert config["linger.ms"] == 500
    assert config["security.protocol"] == "SASL_SSL"
    # Idempotence default still present.
    assert config["enable.idempotence"] is True


# ---------------------------------------------------------------------------
# Buffer back-pressure
# ---------------------------------------------------------------------------


def test_publish_raises_on_buffer_full(mock_producer: MagicMock) -> None:
    mock_producer.produce.side_effect = BufferError("queue full")
    wrapper = KafkaProducerWrapper()

    with pytest.raises(KafkaPublishError):
        wrapper.publish(value={"x": 1}, key="k")


# ---------------------------------------------------------------------------
# Delivery callback (covers logging branches)
# ---------------------------------------------------------------------------


def test_delivery_report_logs_success_and_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    msg = MagicMock()
    msg.topic.return_value = "air-quality-raw"
    msg.partition.return_value = 0
    msg.offset.return_value = 42

    with caplog.at_level("DEBUG", logger="src.ingestion.kafka_producer"):
        KafkaProducerWrapper._delivery_report(None, msg)
    assert any("kafka delivered" in r.message for r in caplog.records)

    caplog.clear()
    with caplog.at_level("ERROR", logger="src.ingestion.kafka_producer"):
        KafkaProducerWrapper._delivery_report("broker down", msg)
    assert any("delivery failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Integration (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("KAFKA_INTEGRATION_BOOTSTRAP"),
    reason="needs running kafka broker (set KAFKA_INTEGRATION_BOOTSTRAP)",
)
def test_integration_round_trip(station_reading: StationReading) -> None:  # pragma: no cover
    """Publishes one reading and consumes it back. CI-only."""
    from confluent_kafka import Consumer  # type: ignore[import-not-found]

    bootstrap = os.environ["KAFKA_INTEGRATION_BOOTSTRAP"]
    topic = os.getenv("KAFKA_INTEGRATION_TOPIC", "air-quality-raw")

    with KafkaProducerWrapper(
        bootstrap_servers=bootstrap,
        default_topic=topic,
    ) as producer:
        producer.publish_reading(station_reading)
        producer.flush(10.0)

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": "kafka-producer-integration-test",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    try:
        consumer.subscribe([topic])
        msg = consumer.poll(10.0)
        assert msg is not None and msg.error() is None
        decoded: dict[str, Any] = json.loads(msg.value().decode("utf-8"))
        assert decoded["station"]["id"] == "alsancak"
    finally:
        consumer.close()
