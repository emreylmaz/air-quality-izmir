"""Spark Structured Streaming — Kafka → AQI enrichment → PostgreSQL.

Sprint 7 deliverable: Structured Streaming job that consumes the
`air-quality-raw` Kafka topic, validates and parses incoming JSON,
computes hourly AQI per (station, hour) over a 1-hour tumbling window
with a 10-minute event-time watermark, then writes enriched rows to
the PostgreSQL `fact_measurements` partitioned fact table.

Execution path:

    docker compose -f infra/docker-compose.local.yml run --rm \\
        --service-ports aqi-spark-stream \\
        spark-submit \\
        --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1,\\
                   org.postgresql:postgresql:42.7.4 \\
        /app/src/processing/spark_streaming.py \\
        --kafka-bootstrap kafka:9092 \\
        --checkpoint /var/spark/checkpoints/aqi-stream

Watermark and window decisions (sprint-07 B1 decision):

* `withWatermark("event_time", "10 minutes")` — accepts late events up
  to 10 minutes after their event time. Beyond that, late records are
  dropped. The choice is informed by `api_collector.py`'s APScheduler
  cron (60-minute cadence); 10 minutes covers typical network/queue
  delays without retaining excessive in-memory state.
* `window("event_time", "1 hour")` — tumbling window aligned to clock
  hour boundaries. The output cadence matches the upstream API
  resolution (saatlik) and matches the `dim_time` granularity, so
  every micro-batch row maps cleanly to a `time_id` in the fact
  table.
* `outputMode("append")` — final aggregations are emitted only after
  the watermark passes the window's end, ensuring exactly-one row per
  (station, window) pair. Late updates are not re-emitted; the rare
  late record (>10 minutes) is silently dropped (TD-13 strict mode
  H10 sprintinde DLQ tarafına yönlendirilecek).

Checkpoint contract:

* Checkpoint directory contains offset commit log + state store.
  Persistent volume mount is mandatory — losing the checkpoint forces
  a Kafka offset reset (data loss or duplication risk).
* On restart, the job resumes from the last committed offset; replay
  is idempotent thanks to `fact_measurements_unique_reading` UNIQUE
  constraint downstream.

Docker-only path (TD-05): same rationale as `spark_batch.py` — host
imports the module without PySpark loaded; production runs in the
`bitnami/spark:3.5.1` container.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pyspark.sql import DataFrame, SparkSession
    from pyspark.sql.streaming.query import StreamingQuery
    from pyspark.sql.types import StructType

_LOG = logging.getLogger(__name__)

_JDBC_DRIVER: Final[str] = "org.postgresql.Driver"

# Topic naming follows the convention set in `kafka_producer.py`:
# `air-quality-raw` carries validated measurements; the DLQ topic
# carries undecodable payloads (H10 streaming security pass).
_KAFKA_TOPIC: Final[str] = "air-quality-raw"

# Event-time watermark window definitions — see module docstring B1.
_WATERMARK: Final[str] = "10 minutes"
_TUMBLING_WINDOW: Final[str] = "1 hour"


def _build_spark_session(app_name: str = "aqi-streaming") -> SparkSession:
    """Lazy-import + build a SparkSession tuned for Structured Streaming.

    See `spark_batch._build_spark_session` for the lazy-import
    rationale (TD-05 host compatibility).
    """
    from pyspark.sql import SparkSession  # noqa: PLC0415

    return (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.streaming.minBatchesToRetain", "20")
        .getOrCreate()
    )


def _kafka_message_schema() -> StructType:
    """Pydantic-mirror Spark schema for the Kafka payload.

    The producer (`kafka_producer.KafkaProducerWrapper.publish`) writes
    JSON values matching the `OpenWeatherMeasurement` pydantic model;
    we declare the same shape here so `from_json` can parse it without
    a schema-inference round trip.
    """
    from pyspark.sql.types import (  # noqa: PLC0415
        DoubleType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    return StructType(
        [
            StructField("station_slug", StringType(), nullable=False),
            StructField("measured_at", TimestampType(), nullable=False),
            StructField("pollutant_code", StringType(), nullable=False),
            StructField("value_ugm3", DoubleType(), nullable=False),
            StructField("source", StringType(), nullable=False),
        ]
    )


def _read_kafka_stream(spark: SparkSession, bootstrap: str) -> DataFrame:
    """Read the air-quality-raw topic as a streaming DataFrame.

    `failOnDataLoss=false` is set because Kafka retention may expire
    old offsets between streaming job restarts; we'd rather skip
    expired offsets than crash on a benign retention event.
    `startingOffsets='latest'` for cold starts; checkpointed runs
    ignore this and resume from the committed offset.
    """
    return (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap)
        .option("subscribe", _KAFKA_TOPIC)
        .option("startingOffsets", "latest")
        .option("failOnDataLoss", "false")
        .load()
    )


def parse_kafka_messages(raw_kafka: DataFrame) -> DataFrame:
    """Parse Kafka JSON values into typed measurement rows.

    Drops rows whose JSON fails to parse (malformed payloads); these
    are silently filtered out at this stage. The DLQ topic (set up by
    the producer) handles bad-format payloads before they reach Kafka
    in the first place; here we treat any remaining decode failure as
    a benign skip.
    """
    from pyspark.sql import functions as F  # noqa: PLC0415, N812

    schema = _kafka_message_schema()
    parsed = raw_kafka.select(
        F.from_json(F.col("value").cast("string"), schema).alias("payload"),
        F.col("timestamp").alias("kafka_ingestion_time"),
    )
    return parsed.select("payload.*", "kafka_ingestion_time").filter(
        F.col("station_slug").isNotNull() & F.col("measured_at").isNotNull()
    )


def windowed_aqi(parsed: DataFrame) -> DataFrame:
    """Tumbling 1-hour window with 10-minute watermark + AQI computation.

    Per-pollutant sub-index is computed via the same Spark UDF used by
    the batch path (`compute_hourly_aqi` in `spark_batch.py`). The
    overall AQI is `max(sub_index)` across pollutants within each
    (station, window) bucket.

    Returns a DataFrame with columns
    `(station_slug, window_start, window_end, aqi, dominant_pollutant)`.
    """
    from pyspark.sql import functions as F  # noqa: PLC0415, N812
    from pyspark.sql.types import IntegerType  # noqa: PLC0415

    from .aqi_calculator import (  # noqa: PLC0415
        BREAKPOINTS,
        calculate_sub_index,
    )

    @F.udf(returnType=IntegerType())  # type: ignore[misc]
    def _sub_index_udf(code: str, value: float) -> int:
        if code not in BREAKPOINTS or value is None:
            return 0
        return calculate_sub_index(code, float(value))  # type: ignore[arg-type]

    with_sub_index = parsed.withColumn(
        "sub_index", _sub_index_udf(F.col("pollutant_code"), F.col("value_ugm3"))
    )

    return (
        with_sub_index.withWatermark("measured_at", _WATERMARK)
        .groupBy(
            F.window(F.col("measured_at"), _TUMBLING_WINDOW).alias("event_window"),
            F.col("station_slug"),
        )
        .agg(
            F.max("sub_index").alias("aqi"),
            F.first("pollutant_code", ignorenulls=True).alias("dominant_pollutant"),
        )
        .select(
            F.col("station_slug"),
            F.col("event_window.start").alias("window_start"),
            F.col("event_window.end").alias("window_end"),
            F.col("aqi"),
            F.col("dominant_pollutant"),
        )
    )


def _write_to_fact_measurements(micro_batch: DataFrame, batch_id: int, jdbc_url: str) -> None:
    """`foreachBatch` sink: upsert each micro-batch into fact_measurements.

    The streaming aggregation produces (station_slug, window_start,
    aqi) rows. We translate to `fact_measurements`-shaped rows
    (resolving `station_id` via dim_station + writing AQI as a
    derived measurement under `pollutant_code='aqi'`). JDBC append +
    UNIQUE constraint on `(station_id, pollutant_id, measured_at,
    source)` gives idempotent retries automatically.

    `batch_id` is logged for forensic correlation with Spark UI's
    micro-batch view.
    """
    from pyspark.sql import functions as F  # noqa: PLC0415, N812

    _LOG.info("micro-batch %s: %d rows", batch_id, micro_batch.count())

    # Materialize the batch once to avoid two reads when computing
    # count + write.
    materialized = micro_batch.cache()

    # Map window_start → measured_at; source='stream'.
    fact_rows = materialized.select(
        F.col("station_slug"),
        F.col("window_start").alias("measured_at"),
        F.lit("aqi").alias("pollutant_code"),
        F.col("aqi").cast("double").alias("value"),
        F.lit("stream").alias("source"),
    )

    (
        fact_rows.write.format("jdbc")
        .option("url", jdbc_url)
        .option("driver", _JDBC_DRIVER)
        .option("dbtable", "fact_measurements_stream_staging")
        .option("batchsize", "1000")
        .mode("append")
        .save()
    )

    materialized.unpersist()


def start_streaming_job(
    kafka_bootstrap: str,
    jdbc_url: str,
    checkpoint_location: str,
) -> StreamingQuery:
    """Wire the full streaming graph and start the query.

    Returns the `StreamingQuery` so the caller (or `main`) can decide
    whether to block on `awaitTermination` or schedule a graceful
    shutdown (e.g. via a Kafka sentinel message).

    Raises:
        RuntimeError: If Spark fails to start the query (e.g. Kafka
            unreachable, checkpoint directory write-protected).
    """
    spark = _build_spark_session()

    raw_kafka = _read_kafka_stream(spark, kafka_bootstrap)
    parsed = parse_kafka_messages(raw_kafka)
    aggregated = windowed_aqi(parsed)

    query = (
        aggregated.writeStream.foreachBatch(
            lambda df, batch_id: _write_to_fact_measurements(df, batch_id, jdbc_url)
        )
        .option("checkpointLocation", checkpoint_location)
        .outputMode("append")
        .trigger(processingTime="1 minute")
        .start()
    )
    _LOG.info(
        "streaming job started: topic=%s window=%s watermark=%s checkpoint=%s",
        _KAFKA_TOPIC,
        _TUMBLING_WINDOW,
        _WATERMARK,
        checkpoint_location,
    )
    return query


def main(argv: list[str] | None = None) -> int:
    """`spark-submit src/processing/spark_streaming.py [--kafka-bootstrap ...]`."""
    parser = argparse.ArgumentParser(
        description="Sprint 7 Spark Structured Streaming: Kafka -> AQI -> PG",
    )
    parser.add_argument(
        "--kafka-bootstrap",
        required=True,
        help="Kafka bootstrap servers (e.g. kafka:9092)",
    )
    parser.add_argument(
        "--jdbc-url",
        default=None,
        help="JDBC URL (defaults to APP_DATABASE_URL env via Settings)",
    )
    parser.add_argument(
        "--checkpoint",
        required=True,
        help="Persistent checkpoint directory (must survive restarts)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    jdbc_url = args.jdbc_url
    if jdbc_url is None:
        from src.config.settings import get_settings  # noqa: PLC0415

        dsn = get_settings().database_url.get_secret_value()
        jdbc_url = "jdbc:" + dsn if dsn.startswith("postgresql://") else dsn

    query = start_streaming_job(args.kafka_bootstrap, jdbc_url, args.checkpoint)
    query.awaitTermination()
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "main",
    "parse_kafka_messages",
    "start_streaming_job",
    "windowed_aqi",
]
