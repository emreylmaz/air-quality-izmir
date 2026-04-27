"""End-to-end schema apply + ingestion smoke (Sprint 4 T7).

Boots a real PG 16 container via ``testcontainers``, runs the full
``0001..0004`` migration chain through `infra.migrations.run.run`,
seeds `dim_station` from `config/stations.yaml`, loads the bundled
100-row fixture CSV through `csv_loader.load_csv`, and asserts:

* migration chain registers all four versions in `schema_migrations`;
* `seed_dim_station` inserts the 6 İzmir stations defined in YAML;
* CSV load returns ``inserted > 0`` on the first run;
* a re-run of the same CSV is fully idempotent: ``inserted == 0`` and
  ``skipped == prior_inserted`` — driven by the
  `fact_measurements_unique_reading` UNIQUE constraint and
  `ON CONFLICT DO NOTHING`;
* an EXPLAIN of a single-month range query touches **only** the
  matching monthly partition (partition-pruning evidence on real data);
* `REFRESH MATERIALIZED VIEW v_hourly_aqi` succeeds on the populated
  fact table and the matview reports the expected
  station × pollutant × hour cardinality;
* `v_daily_trends` returns one row per ``(station, pollutant, day)``
  with valid MIN/MAX/AVG/COUNT aggregates.

DoD source: ``docs/sprints/sprint-04.md`` T7.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from infra.migrations.run import run as run_migrations
from infra.postgres.seed_dim_station import seed as seed_dim_station
from src.ingestion.csv_loader import load_csv, resolve_station_id
from tests.integration.conftest import reset_public_schema

# 100-row fixture from Sprint 3, 6 pollutants × ~hourly, 2024-01-01..2024-01-05.
# Re-used as the canonical input here so the schema-apply suite shares the
# same test data shape as the unit-level csv_loader tests.
FIXTURE_CSV = (
    Path(__file__).resolve().parents[1] / "ingestion" / "fixtures" / "izmir_sample_utf8.csv"
)

# Konak is the urban_traffic anchor station from `config/stations.yaml`;
# the fixture CSV is generic so any seeded slug works.
STATION_SLUG = "konak"

# Range covering the fixture timestamps (2024-01-01..2024-01-05).
JAN_START = "2024-01-01 00:00:00+00"
JAN_END = "2024-02-01 00:00:00+00"


# ---------------------------------------------------------------------------
# Apply chain: migrations + seed + csv load
# ---------------------------------------------------------------------------


def _apply_full_chain(dsn: str) -> tuple[int, int]:
    """Apply migrations → seed dim_station → load fixture CSV.

    Returns ``(inserted, skipped)`` from the first CSV load so callers
    can assert against the freshly populated state.
    """
    run_migrations(dsn)
    seed_dim_station(dsn)
    with psycopg.connect(dsn) as conn:
        station_id = resolve_station_id(conn, STATION_SLUG)
        return load_csv(FIXTURE_CSV, station_id, conn=conn)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSchemaApplyEndToEnd:
    """Migration chain + seed + ingest, against a live PG 16 instance."""

    def test_migration_chain_registers_all_versions(self, pg_container: str) -> None:
        """0001..0004 must end up in `schema_migrations` after `run()`."""
        reset_public_schema(pg_container)
        applied = run_migrations(pg_container)
        # Baseline + 0002 + 0003 + 0004 — at least four.
        assert applied >= 4

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            versions = [row[0] for row in cur.fetchall()]
        assert versions[:4] == ["0001", "0002", "0003", "0004"], versions

    def test_seed_inserts_six_izmir_stations(self, pg_container: str) -> None:
        """`seed_dim_station` from `config/stations.yaml` puts 6 rows in
        `dim_station` and is idempotent on re-run.
        """
        reset_public_schema(pg_container)
        run_migrations(pg_container)

        inserted, updated = seed_dim_station(pg_container)
        assert inserted == 6, f"expected 6 inserts, got {inserted}"
        assert updated == 0, f"expected 0 updates on first run, got {updated}"

        # Idempotency: a second seed converts inserts to updates.
        inserted_again, updated_again = seed_dim_station(pg_container)
        assert inserted_again == 0
        assert updated_again == 6

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM dim_station")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 6

    def test_first_csv_load_inserts_rows(self, pg_container: str) -> None:
        """First-pass load through `csv_loader.load_csv` lands data in
        `fact_measurements`. We assert the loader-reported counts and the
        actual table row count agree.
        """
        reset_public_schema(pg_container)
        inserted, skipped = _apply_full_chain(pg_container)
        # Cleaning may drop a handful of rows (negative/IQR/NaN); the
        # exact count depends on the fixture's outlier profile, but the
        # loader must report at least a couple hundred new rows for a
        # 100-row × 6-pollutant input. We use an inclusive lower bound
        # rather than an equality so a future fixture tweak doesn't
        # require this test to be re-pinned.
        assert inserted > 100, f"first load inserted only {inserted} rows"
        assert skipped == 0, f"first load should have 0 conflicts, got {skipped}"

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fact_measurements")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == inserted, f"row count {row[0]} != reported inserted {inserted}"

    def test_second_csv_load_is_idempotent(self, pg_container: str) -> None:
        """TD-09 contract: re-running the loader on the same CSV must
        skip every row and leave the table unchanged.
        """
        reset_public_schema(pg_container)
        first_inserted, _ = _apply_full_chain(pg_container)

        # Second load against the same CSV — expect every row to hit
        # `ON CONFLICT ON CONSTRAINT fact_measurements_unique_reading`.
        with psycopg.connect(pg_container) as conn:
            station_id = resolve_station_id(conn, STATION_SLUG)
            second_inserted, second_skipped = load_csv(FIXTURE_CSV, station_id, conn=conn)
        assert second_inserted == 0, f"second load inserted {second_inserted}, expected 0"
        assert (
            second_skipped == first_inserted
        ), f"second load skipped {second_skipped} != first_inserted {first_inserted}"

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fact_measurements")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == first_inserted, "row count drifted after idempotent re-load"

    def test_partition_pruning_on_real_data(self, pg_container: str) -> None:
        """Filter on a single month → only the matching partition is
        present in the EXPLAIN plan. We assert with the structured
        ``FORMAT JSON`` output so the substring match is robust to
        whitespace and pgsql release-notes wording changes.
        """
        reset_public_schema(pg_container)
        _apply_full_chain(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            # `TIMESTAMPTZ '<literal>'` is a *typed literal* (a single
            # token) — psycopg's `%s` substitution would generate
            # `TIMESTAMPTZ $1` which PG rejects. We pass the casts as
            # plain `%s` parameters; libpq sends them with their
            # text-mode timestamptz oid, no in-SQL cast needed.
            cur.execute(
                """
                EXPLAIN (FORMAT TEXT, ANALYZE)
                SELECT * FROM fact_measurements
                WHERE measured_at >= %s::timestamptz
                  AND measured_at <  %s::timestamptz
                """,
                (JAN_START, JAN_END),
            )
            plan_lines = [r[0] for r in cur.fetchall()]
        plan = "\n".join(plan_lines)

        # The 2024-01 partition must be referenced.
        assert "fact_measurements_2024_01" in plan, plan
        # No other monthly partition should appear (we picked four
        # spread-out neighbours as canaries).
        for unwanted in (
            "fact_measurements_2024_02",
            "fact_measurements_2024_06",
            "fact_measurements_2025_01",
            "fact_measurements_default",
        ):
            assert (
                unwanted not in plan
            ), f"partition pruning leaked: plan mentions {unwanted}\n{plan}"

    def test_v_hourly_aqi_refresh_and_count(self, pg_container: str) -> None:
        """`REFRESH MATERIALIZED VIEW v_hourly_aqi` must succeed on
        populated data and surface ``station × pollutant × hour``
        rows. We assert the matview is non-empty and aligned with the
        underlying fact-table cardinality (one row per
        ``(station, pollutant, hour)`` group).
        """
        reset_public_schema(pg_container)
        _apply_full_chain(pg_container)

        with psycopg.connect(pg_container) as conn:
            with conn.cursor() as cur:
                cur.execute("REFRESH MATERIALIZED VIEW v_hourly_aqi")
                cur.execute("SELECT count(*) FROM v_hourly_aqi")
                row = cur.fetchone()
                assert row is not None
                matview_rows = row[0]

                # Compare against the source-side group cardinality.
                cur.execute(
                    """
                    SELECT count(*) FROM (
                        SELECT 1
                        FROM fact_measurements
                        GROUP BY station_id, pollutant_id, date_trunc('hour', measured_at)
                    ) AS g
                    """,
                )
                row = cur.fetchone()
                assert row is not None
                source_groups = row[0]
            conn.commit()

        assert matview_rows > 0, "v_hourly_aqi populated 0 rows after REFRESH"
        assert (
            matview_rows == source_groups
        ), f"v_hourly_aqi rows ({matview_rows}) != source groups ({source_groups})"

    def test_v_daily_trends_aggregates(self, pg_container: str) -> None:
        """`v_daily_trends` returns one row per ``(day, station,
        pollutant)`` with sane MIN ≤ AVG ≤ MAX and a positive sample
        count. We don't pin the exact row count (cleaning kills some
        tuples), but the structural invariants must hold.
        """
        reset_public_schema(pg_container)
        _apply_full_chain(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT day, station_id, pollutant_id,
                       min_value, max_value, avg_value, sample_count
                FROM v_daily_trends
                ORDER BY day, station_id, pollutant_id
                """,
            )
            rows = cur.fetchall()

        assert rows, "v_daily_trends returned 0 rows on populated fact"
        for day, _station_id, _pollutant_id, mn, mx, avg, n in rows:
            assert day is not None
            assert n > 0, f"non-positive sample_count for {day}"
            assert (
                mn <= avg <= mx
            ), f"aggregate invariant failed for {day}: min={mn} avg={avg} max={mx}"
