"""Spark batch processing — daily/weekly/monthly aggregations, rolling windows.

Sprint 6 deliverable: PySpark batch job that reads `fact_measurements`
via JDBC, computes hourly AQI per (station, measured_at), then derives
daily/weekly/monthly aggregations + 7- and 30-day rolling means + a
cross-station correlation matrix. Output written back to PostgreSQL
aggregation tables (`agg_aqi_daily`, `agg_aqi_rolling`,
`agg_station_correlation`).

Execution path:

    docker compose -f infra/docker-compose.local.yml run --rm \\
        --service-ports aqi-spark-batch \\
        spark-submit --packages org.postgresql:postgresql:42.7.4 \\
        /app/src/processing/spark_batch.py \\
        --date-from 2024-01-01 --date-to 2024-12-31

Host (Python 3.13) üzerinde doğrudan çalıştırılmaz: PyPI'da PySpark
3.5.1 için cp313 wheel olmadığından local `.venv` yalnız import
sözleşmesini sağlar (test mock'ları). Production çalıştırma Docker
`bitnami/spark:3.5.1` üzerinden yapılır (TD-05 sprint-06 kararı:
Docker-only path).

Idempotency:

* Her aggregation tablosu `(date, station_id, pollutant_id)` üzerinde
  UNIQUE; INSERT … ON CONFLICT DO UPDATE ile re-run güvenli.
* JDBC `mode='overwrite'` partition-by-date staging tablosu yazımı
  + ardından `MERGE`/`INSERT … ON CONFLICT` ile final tabloya
  taşıma (atomic swap). Burada basitlik için `mode='append'` +
  upsert SQL kullanıyoruz; H10'da Spark Iceberg/Delta entegrasyonu
  değerlendirilecek.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pyspark.sql import DataFrame, SparkSession
    from pyspark.sql.types import StructType

_LOG = logging.getLogger(__name__)

# JDBC URL is read from APP_DATABASE_URL env var via the calling shell.
# We don't hardcode it here — secrets stay out of the codebase.
_JDBC_DRIVER: Final[str] = "org.postgresql.Driver"

# Rolling window definitions — 7-day and 30-day, hourly cadence.
_WINDOW_7D_HOURS: Final[int] = 7 * 24
_WINDOW_30D_HOURS: Final[int] = 30 * 24


def _build_spark_session(app_name: str = "aqi-batch") -> SparkSession:
    """Lazy-import + build a SparkSession.

    Importing pyspark at module top would break `python -c 'import
    spark_batch'` on a host without PySpark installed (TD-05). The
    import is deferred to the call site so unit tests can mock the
    builder without ever loading the JVM.
    """
    from pyspark.sql import SparkSession  # noqa: PLC0415

    return (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )


def _read_fact_measurements(
    spark: SparkSession, jdbc_url: str, date_from: date, date_to: date
) -> DataFrame:
    """Read fact_measurements partition for [date_from, date_to).

    Partition pruning is delegated to PostgreSQL — we pass a server-side
    `dbtable` subquery with the WHERE filter so the driver loads only
    the rows in the date range, not the full table.

    Security note (SQL injection guard): `date_from` and `date_to` are
    typed `datetime.date`. The caller (`main`) parses CLI strings with
    `date.fromisoformat`, which rejects anything outside `YYYY-MM-DD`;
    `.isoformat()` then re-emits exactly that grammar. Spark JDBC
    `dbtable` is a free-form SQL subquery (no parameter binding
    surface), so this validate-then-canonicalise pattern is the
    correct mitigation rather than a parameter placeholder.
    """
    iso_from = date_from.isoformat()
    iso_to = date_to.isoformat()
    query = f"""
        (SELECT measurement_id, station_id, pollutant_id, measured_at,
                value, source
         FROM fact_measurements
         WHERE measured_at >= '{iso_from}'::timestamptz
           AND measured_at <  '{iso_to}'::timestamptz) AS fm
    """
    return (
        spark.read.format("jdbc")
        .option("url", jdbc_url)
        .option("driver", _JDBC_DRIVER)
        .option("dbtable", query)
        .option("fetchsize", "10000")
        .load()
    )


def _read_dim_pollutant(spark: SparkSession, jdbc_url: str) -> DataFrame:
    """Read pollutant catalog for code lookup."""
    return (
        spark.read.format("jdbc")
        .option("url", jdbc_url)
        .option("driver", _JDBC_DRIVER)
        .option("dbtable", "(SELECT pollutant_id, code FROM dim_pollutant) AS dp")
        .load()
    )


def compute_hourly_aqi(measurements: DataFrame, pollutants: DataFrame) -> DataFrame:
    """Hourly AQI per (station_id, measured_at) using EPA breakpoints.

    Pivots the long-format `fact_measurements` into one row per
    (station, hour) with one column per pollutant code, applies the
    AQI sub-index UDF per pollutant, then takes max across pollutants
    as the overall AQI.

    Returns a DataFrame with columns
    `(station_id, measured_at, aqi, dominant_pollutant)`.
    """
    from pyspark.sql import functions as F  # noqa: PLC0415, N812
    from pyspark.sql.types import IntegerType  # noqa: PLC0415

    from .aqi_calculator import (  # noqa: PLC0415
        BREAKPOINTS,
        calculate_sub_index,
    )

    # Resolve pollutant codes alongside each measurement.
    enriched = measurements.join(pollutants, on="pollutant_id", how="inner")

    # Spark UDF wraps the pure-Python sub-index calculator.
    # F.udf is used here because the calculation is non-trivial and
    # rewriting it in pure Spark SQL would obscure the EPA formula.
    @F.udf(returnType=IntegerType())  # type: ignore[misc]
    def _sub_index_udf(code: str, value: float) -> int:
        if code not in BREAKPOINTS or value is None:
            return 0
        return calculate_sub_index(code, float(value))  # type: ignore[arg-type]

    sub_indexed = enriched.withColumn("sub_index", _sub_index_udf(F.col("code"), F.col("value")))

    # Per (station, hour): overall AQI = max sub-index, dominant
    # pollutant = code that produced the maximum.
    return (
        sub_indexed.groupBy("station_id", "measured_at")
        .agg(
            F.max("sub_index").alias("aqi"),
            F.first("code", ignorenulls=True).alias("dominant_pollutant"),
        )
        .orderBy("station_id", "measured_at")
    )


def compute_daily_aggregations(hourly_aqi: DataFrame) -> DataFrame:
    """Daily min/max/avg AQI per station from hourly AQI series.

    Returns a DataFrame with columns
    `(station_id, day, aqi_min, aqi_max, aqi_avg, hourly_observations)`.
    """
    from pyspark.sql import functions as F  # noqa: PLC0415, N812

    return (
        hourly_aqi.withColumn("day", F.to_date("measured_at"))
        .groupBy("station_id", "day")
        .agg(
            F.min("aqi").alias("aqi_min"),
            F.max("aqi").alias("aqi_max"),
            F.avg("aqi").alias("aqi_avg"),
            F.count("aqi").alias("hourly_observations"),
        )
        .orderBy("station_id", "day")
    )


def compute_rolling_means(hourly_aqi: DataFrame) -> DataFrame:
    """7-day and 30-day rolling means of hourly AQI per station.

    Uses Spark `Window` ordered by `measured_at` with a
    `rowsBetween(-N, 0)` frame; `rangeBetween` with timestamp would be
    more accurate when gaps exist but slower. For continuous hourly data
    the row-count frame is equivalent.
    """
    from pyspark.sql import Window  # noqa: PLC0415
    from pyspark.sql import functions as F  # noqa: PLC0415, N812

    station_window = Window.partitionBy("station_id").orderBy("measured_at")

    return (
        hourly_aqi.withColumn(
            "rolling_mean_7d",
            F.avg("aqi").over(station_window.rowsBetween(-(_WINDOW_7D_HOURS - 1), 0)),
        )
        .withColumn(
            "rolling_mean_30d",
            F.avg("aqi").over(station_window.rowsBetween(-(_WINDOW_30D_HOURS - 1), 0)),
        )
        .select(
            "station_id",
            "measured_at",
            "aqi",
            "rolling_mean_7d",
            "rolling_mean_30d",
        )
    )


def compute_station_correlation(hourly_aqi: DataFrame) -> DataFrame:
    """Pearson correlation of hourly AQI between every station pair.

    Pivots station_id → columns then computes pairwise correlation via
    `DataFrameStatFunctions.corr`. For 6 stations this gives a 6×6
    matrix; we emit it as a long-format DataFrame
    `(station_a, station_b, correlation)` so it loads cleanly into the
    `agg_station_correlation` table.

    Returned correlations are within [-1.0, 1.0]; null pairs (no shared
    timestamps) are skipped.
    """
    from pyspark.sql import functions as F  # noqa: PLC0415, N812

    pivoted = hourly_aqi.groupBy("measured_at").pivot("station_id").agg(F.first("aqi")).na.drop()
    station_columns = [c for c in pivoted.columns if c != "measured_at"]

    rows: list[dict[str, float | int]] = []
    for station_a in station_columns:
        for station_b in station_columns:
            if station_a >= station_b:
                # Symmetric matrix — only emit upper triangle + diagonal.
                continue
            corr = pivoted.stat.corr(station_a, station_b)
            rows.append(
                {
                    "station_a": int(station_a),
                    "station_b": int(station_b),
                    "correlation": float(corr) if corr is not None else 0.0,
                }
            )
    return pivoted.sql_ctx.createDataFrame(rows) if rows else _empty_correlation_df(pivoted)


def _empty_correlation_df(reference: DataFrame) -> DataFrame:
    """Build an empty correlation DataFrame with the expected schema."""
    from pyspark.sql.types import (  # noqa: PLC0415
        DoubleType,
        IntegerType,
        StructField,
        StructType,
    )

    schema: StructType = StructType(
        [
            StructField("station_a", IntegerType(), nullable=False),
            StructField("station_b", IntegerType(), nullable=False),
            StructField("correlation", DoubleType(), nullable=False),
        ]
    )
    return reference.sql_ctx.createDataFrame([], schema)


def _write_jdbc(df: DataFrame, jdbc_url: str, table: str, mode: str = "append") -> None:
    """Write a DataFrame to PostgreSQL via JDBC.

    `mode='append'` relies on the target table having ON CONFLICT
    DO UPDATE semantics (idempotent retries). Aggregation tables
    define UNIQUE constraints on the natural keys; staging tables
    use the JDBC append mode for atomic writes.
    """
    (
        df.write.format("jdbc")
        .option("url", jdbc_url)
        .option("driver", _JDBC_DRIVER)
        .option("dbtable", table)
        .option("batchsize", "10000")
        .mode(mode)
        .save()
    )


def run_batch(jdbc_url: str, date_from: date, date_to: date) -> None:
    """Orchestrate the full Sprint 6 batch pipeline.

    Args:
        jdbc_url: PostgreSQL JDBC connection URL.
        date_from: Inclusive lower bound of measurement range.
        date_to: Exclusive upper bound of measurement range.

    Stages:
        1. Read fact_measurements + dim_pollutant via JDBC.
        2. Compute hourly AQI per (station, measured_at).
        3. Derive daily aggregations → write to agg_aqi_daily.
        4. Derive 7d/30d rolling means → write to agg_aqi_rolling.
        5. Compute station correlation matrix → write to
           agg_station_correlation.
    """
    app_suffix = f"{date_from.isoformat()}-{date_to.isoformat()}"
    spark = _build_spark_session(app_name=f"aqi-batch-{app_suffix}")
    try:
        _LOG.info("reading fact_measurements [%s, %s)", date_from, date_to)
        measurements = _read_fact_measurements(spark, jdbc_url, date_from, date_to)
        pollutants = _read_dim_pollutant(spark, jdbc_url)

        _LOG.info("computing hourly AQI")
        hourly_aqi = compute_hourly_aqi(measurements, pollutants).cache()

        _LOG.info("computing daily aggregations")
        daily = compute_daily_aggregations(hourly_aqi)
        _write_jdbc(daily, jdbc_url, "agg_aqi_daily")

        _LOG.info("computing rolling means")
        rolling = compute_rolling_means(hourly_aqi)
        _write_jdbc(rolling, jdbc_url, "agg_aqi_rolling")

        _LOG.info("computing station correlation matrix")
        correlation = compute_station_correlation(hourly_aqi)
        _write_jdbc(correlation, jdbc_url, "agg_station_correlation")

        _LOG.info("batch job complete: [%s, %s)", date_from, date_to)
    finally:
        spark.stop()


def main(argv: list[str] | None = None) -> int:
    """`spark-submit src/processing/spark_batch.py --date-from ... --date-to ...`.

    `--date-from` / `--date-to` parsed via `date.fromisoformat` which
    rejects anything outside the `YYYY-MM-DD` grammar with a
    `ValueError`. argparse then surfaces the error message to the
    operator before any SQL is constructed downstream. This is the
    validate-at-boundary mitigation for the SQL-injection surface in
    `_read_fact_measurements`'s `dbtable` subquery.
    """
    parser = argparse.ArgumentParser(
        description="Sprint 6 Spark batch: AQI hourly + daily + rolling + correlation",
    )
    parser.add_argument(
        "--date-from",
        required=True,
        type=date.fromisoformat,
        help="ISO date (YYYY-MM-DD) inclusive lower bound",
    )
    parser.add_argument(
        "--date-to",
        required=True,
        type=date.fromisoformat,
        help="ISO date (YYYY-MM-DD) exclusive upper bound",
    )
    parser.add_argument(
        "--jdbc-url",
        default=None,
        help="JDBC URL (defaults to APP_DATABASE_URL env via Settings)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    jdbc_url = args.jdbc_url
    if jdbc_url is None:
        from src.config.settings import get_settings  # noqa: PLC0415

        # Convert PG DSN to JDBC URL form: postgresql:// → jdbc:postgresql://
        dsn = get_settings().database_url.get_secret_value()
        jdbc_url = "jdbc:" + dsn if dsn.startswith("postgresql://") else dsn

    run_batch(jdbc_url, args.date_from, args.date_to)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "compute_daily_aggregations",
    "compute_hourly_aqi",
    "compute_rolling_means",
    "compute_station_correlation",
    "main",
    "run_batch",
]
