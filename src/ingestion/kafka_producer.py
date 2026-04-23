"""Kafka producer for air-quality-raw / weather-raw topics.

TODO (Hafta 3): Full implementation by `data-engineer` agent.

Responsibilities:
- confluent-kafka Producer wrapper
- JSON serialization with schema validation (pydantic)
- Key = f"{station_id}:{iso_hour}"
- Retry on transient failures, DLQ on malformed payloads
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class KafkaProducerWrapper:
    """Thin wrapper around confluent-kafka Producer.

    TODO: implement in Hafta 3.
    """

    def __init__(self, bootstrap_servers: str, default_topic: str) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.default_topic = default_topic
        # TODO: init confluent_kafka.Producer

    def publish(self, key: str, value: dict, topic: str | None = None) -> None:
        """Publish a JSON-serializable value to Kafka."""
        raise NotImplementedError("Hafta 3: data-engineer agent implements this")

    def flush(self, timeout: float = 10.0) -> int:
        """Wait for pending messages. Returns remaining count."""
        raise NotImplementedError("Hafta 3")
