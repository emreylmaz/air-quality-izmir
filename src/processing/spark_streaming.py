"""Spark Structured Streaming — Kafka → AQI enrichment → PostgreSQL.

TODO (Hafta 7): Full implementation by `spark-engineer` agent.

Responsibilities:
- Kafka source (air-quality-raw)
- Watermark 10 min, tumbling window 1h
- AQI enrichment via aqi_calculator
- Append to fact_measurements via JDBC
- Checkpoint to persistent volume
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def start_streaming_job() -> None:
    """Start Spark streaming job. Blocks until termination.

    TODO: implement in Hafta 7.
    """
    raise NotImplementedError("Hafta 7: spark-engineer agent implements this")
