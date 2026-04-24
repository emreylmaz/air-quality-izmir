"""Kafka producer wrapper for the air-quality ingestion pipeline.

Wraps `confluent_kafka.Producer` with:

- Pydantic / mapping value serialization (UTF-8 JSON bytes).
- Stable message keys built from `station_id:iso_hour` for downstream
  idempotency (Spark job dedupes by Kafka key + offset).
- DLQ routing for serialization failures so a single malformed payload
  cannot stall the producer or poison the live topic.
- Idempotent producer config (`enable.idempotence=True`, `acks=all`) so
  network blips do not duplicate writes inside a producer session.

Retry of *delivery* failures (broker down, partition leader election) is
handled by the librdkafka client itself via `retries`/`retry.backoff.ms`.
This wrapper only logs delivery callback errors — full DLQ replay logic
will land in Hafta 10 once we have observability for queue depth.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from types import TracebackType
from typing import TYPE_CHECKING, Any

from confluent_kafka import Producer
from pydantic import BaseModel

from src.config.settings import get_settings

if TYPE_CHECKING:
    from src.ingestion.api_collector import StationReading

_LOG = logging.getLogger(__name__)

# Default librdkafka knobs. Callers may override via `extra_config`. Keep
# this dict immutable in spirit — it is copied per instance.
_DEFAULT_PRODUCER_CONFIG: dict[str, Any] = {
    "acks": "all",
    "compression.type": "gzip",
    "linger.ms": 50,
    "enable.idempotence": True,
    "retries": 5,
    "retry.backoff.ms": 200,
}


class KafkaPublishError(RuntimeError):
    """Raised when a message cannot be serialized or producer is closed."""


class KafkaProducerWrapper:
    """Thin convenience wrapper around `confluent_kafka.Producer`.

    Designed for short-lived use as a context manager from the scheduler
    job; a single instance is safe to reuse for the lifetime of the
    process but must not be shared across forks (librdkafka background
    threads are not fork-safe).
    """

    def __init__(
        self,
        *,
        bootstrap_servers: str | None = None,
        client_id: str | None = None,
        default_topic: str | None = None,
        dlq_topic: str | None = None,
        extra_config: Mapping[str, Any] | None = None,
    ) -> None:
        settings = get_settings()
        self._bootstrap_servers = bootstrap_servers or settings.kafka_bootstrap_servers
        self._client_id = client_id or settings.kafka_client_id
        self._default_topic = default_topic or settings.kafka_topic_raw
        self._dlq_topic = dlq_topic or settings.kafka_topic_dlq

        config: dict[str, Any] = {
            "bootstrap.servers": self._bootstrap_servers,
            "client.id": self._client_id,
            **_DEFAULT_PRODUCER_CONFIG,
        }
        if extra_config:
            config.update(extra_config)

        self._config = config
        self._producer: Producer | None = Producer(config)
        self._closed = False
        _LOG.info(
            "kafka producer initialised: bootstrap=%s client_id=%s default_topic=%s dlq_topic=%s",
            self._bootstrap_servers,
            self._client_id,
            self._default_topic,
            self._dlq_topic,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(
        self,
        *,
        value: BaseModel | Mapping[str, Any],
        key: str,
        topic: str | None = None,
    ) -> None:
        """Serialize `value` and produce one message.

        On serialization failure the message is rerouted to the DLQ topic
        with an envelope containing `error` + truncated `raw` repr. The
        live topic is never written with a malformed payload.
        """
        target_topic = topic or self._default_topic
        try:
            payload = self._serialize(value)
        except (TypeError, ValueError) as exc:
            self._send_to_dlq(key=key, error=exc, raw_value=value)
            return

        self._produce(topic=target_topic, key=key, payload=payload)
        # Trigger delivery callbacks for any prior in-flight messages so
        # logs surface promptly. poll(0) is non-blocking.
        producer = self._require_producer()
        producer.poll(0)

    def publish_reading(self, reading: StationReading) -> None:
        """Convenience: publish a `StationReading` keyed by station + hour."""
        key = self._build_reading_key(
            station_id=reading.station.id,
            measured_at=reading.air_pollution.measured_at,
        )
        self.publish(value=reading, key=key)

    def flush(self, timeout_seconds: float = 5.0) -> int:
        """Block until pending messages are delivered. Returns leftovers."""
        producer = self._require_producer()
        remaining: int = producer.flush(timeout_seconds)
        if remaining:
            _LOG.warning("kafka flush timed out: %d message(s) still queued", remaining)
        return remaining

    def close(self, timeout_seconds: float = 5.0) -> None:
        """Flush + drop reference to the underlying producer."""
        if self._closed:
            return
        if self._producer is not None:
            try:
                self._producer.flush(timeout_seconds)
            finally:
                # confluent-kafka has no explicit close(); GC releases the
                # native handle. We just drop the reference.
                self._producer = None
        self._closed = True
        _LOG.info("kafka producer closed")

    def __enter__(self) -> KafkaProducerWrapper:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_producer(self) -> Producer:
        if self._closed or self._producer is None:
            raise KafkaPublishError("producer is closed")
        return self._producer

    @staticmethod
    def _serialize(value: BaseModel | Mapping[str, Any]) -> bytes:
        """Encode `value` as UTF-8 JSON bytes.

        Raises `TypeError` / `ValueError` for non-JSON-serializable input;
        callers route those to the DLQ.
        """
        if isinstance(value, BaseModel):
            text = value.model_dump_json()
        elif isinstance(value, Mapping):
            text = json.dumps(value, default=str, ensure_ascii=False)
        else:
            raise TypeError(f"unsupported value type for kafka publish: {type(value).__name__}")
        return text.encode("utf-8")

    @staticmethod
    def _build_reading_key(*, station_id: str, measured_at: datetime) -> str:
        """`{station_id}:{iso_hour}` — truncated to the hour, UTC-aware."""
        hour = measured_at.replace(minute=0, second=0, microsecond=0)
        return f"{station_id}:{hour.isoformat()}"

    def _produce(self, *, topic: str, key: str, payload: bytes) -> None:
        producer = self._require_producer()
        try:
            producer.produce(
                topic=topic,
                key=key.encode("utf-8"),
                value=payload,
                on_delivery=self._delivery_report,
            )
        except BufferError as exc:
            # Local queue full — surface as a publish error so caller can
            # decide to flush + retry or drop.
            _LOG.error(
                "kafka produce buffer full: topic=%s key=%s size=%d",
                topic,
                key,
                len(payload),
            )
            raise KafkaPublishError("producer queue is full") from exc

        _LOG.info(
            "kafka produced: topic=%s key=%s size=%d",
            topic,
            key,
            len(payload),
        )

    def _send_to_dlq(
        self,
        *,
        key: str,
        error: Exception,
        raw_value: object,
    ) -> None:
        envelope = {
            "error": str(error),
            "raw": repr(raw_value)[:500],
        }
        try:
            payload = json.dumps(envelope, default=str, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            # Fallback: even repr()'d payload is unserializable. Should not
            # happen but we still want a record on the DLQ.
            payload = json.dumps(
                {"error": str(error), "raw": "<unrepr-able>"},
                ensure_ascii=False,
            ).encode("utf-8")

        _LOG.warning(
            "kafka serialization failed, routing to DLQ: topic=%s key=%s error_type=%s",
            self._dlq_topic,
            key,
            type(error).__name__,
        )
        self._produce(topic=self._dlq_topic, key=key, payload=payload)
        producer = self._require_producer()
        producer.poll(0)

    @staticmethod
    def _delivery_report(err: Any, msg: Any) -> None:
        """librdkafka delivery callback. Never raises."""
        if err is None:
            try:
                _LOG.debug(
                    "kafka delivered: topic=%s partition=%s offset=%s",
                    msg.topic(),
                    msg.partition(),
                    msg.offset(),
                )
            except Exception:  # pragma: no cover — defensive against mock msgs
                _LOG.debug("kafka delivered (msg metadata unavailable)")
            return
        _LOG.error("kafka delivery failed: %s", err)


__all__ = [
    "KafkaProducerWrapper",
    "KafkaPublishError",
]
