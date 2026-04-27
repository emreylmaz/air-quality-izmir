"""Sprint 4 T8 — 312K row load performance smoke test.

Generates a deterministic synthetic dataset spanning a full calendar
year (12 months × 30 days × 24 hours × 6 stations × 6 pollutants ≈
311,040 rows) and pushes it through the same `insert_rows` code path
the CSV loader uses on real data. Measures wall-clock duration with
``time.perf_counter()`` and asserts:

* total load completes in ≤ 60 s on local SSD;
* `EXPLAIN (ANALYZE, BUFFERS)` of a single-month range query touches
  exactly one monthly partition (partition-pruning evidence under
  load);
* index footprints recorded — BRIN should be substantially smaller
  than the B-tree composite, validating the storage trade-off chosen
  in 0003.

The runbook ``docs/sprints/sprint-04-perf.md`` is the human-readable
counterpart of this measurement: every numeric value pinned in the
runbook came from a successful run of this test.

We *do not* go through `csv_loader.load_csv` here even though we
generate CSV-shaped data. Reasons:
  1. Pandas-side cleaning (IQR, ffill) on 312K rows would dominate
     the timing and obscure the DB throughput we're profiling.
  2. The values are already clean and physically plausible — the
     IQR filter would silently drop legitimate tails and skew the
     reported row count.
  3. `insert_rows` is the same cursor.executemany path the loader
     uses internally, so the *DB-side* contract under test is
     identical.

DoD source: ``docs/sprints/sprint-04.md`` T8.
"""

from __future__ import annotations

import math
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest

from infra.migrations.run import run as run_migrations
from infra.postgres.seed_dim_station import seed as seed_dim_station
from src.ingestion.csv_loader import insert_rows, load_pollutant_id_map
from tests.integration.conftest import reset_public_schema

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

# ---------------------------------------------------------------------------
# Synthetic dataset shape
# ---------------------------------------------------------------------------

# 12 months × 30 days × 24 hours × 6 stations × 6 pollutants = 311,040 rows.
# We deliberately under-shoot the calendar (30 days/month rather than 28-31)
# so the row count is a clean integer the runbook can quote.
DAYS_PER_MONTH = 30
MONTHS = 12
HOURS_PER_DAY = 24
START = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)

# Pollutant code → (min, max) plausible µg/m³ band. The cleaning
# pipeline's plausibility limits aren't applied here; we keep readings
# inside realistic envelopes so the reviewer can eyeball values without
# raising eyebrows.
POLLUTANT_RANGES: dict[str, tuple[float, float]] = {
    "pm25": (5.0, 100.0),
    "pm10": (10.0, 150.0),
    "no2": (5.0, 80.0),
    "so2": (1.0, 30.0),
    "o3": (10.0, 200.0),
    # CO already stored in µg/m³ post-`standardise_units`. Synthetic
    # values land in mid-band so neither tail trips DQ alerts.
    "co": (200.0, 4000.0),
}

# DoD wall-clock budget on local SSD.
MAX_LOAD_SECONDS = 60.0

# Deterministic seed so the dataset is reproducible across runs and
# CI failures can be re-staged locally without re-randomising.
RNG_SEED = 20260427


# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------


def _expected_row_count() -> int:
    """Compile-time row count — used to pin the test's lower bound."""
    return MONTHS * DAYS_PER_MONTH * HOURS_PER_DAY * 6 * len(POLLUTANT_RANGES)


def _iter_synthetic_rows(
    *,
    station_ids: list[int],
    pollutant_ids: dict[str, int],
) -> Iterable[tuple[int, int, datetime, float]]:
    """Yield ``(station_id, pollutant_id, measured_at, value)`` tuples.

    The generator is plain Python (not pandas) so we don't pay an
    intermediate DataFrame allocation for 312K rows. Memory footprint
    stays roughly proportional to the batch size held in
    ``insert_rows``'s payload list (~25 MB for 312K tuples).

    Calendar walk strategy: ``month_offset × DAYS_PER_MONTH +
    day_offset`` is treated as an absolute day index into the year, and
    converted to a real date by adding to ``START``. This avoids the
    Feb-overflow trap where (month=2, day_offset=29) and (month=3,
    day_offset=0) would resolve to the same calendar date and trip the
    UNIQUE constraint. With 12 × 30 = 360 days we land safely inside a
    single year regardless of leap-year status.
    """
    rng = random.Random(RNG_SEED)
    for month_offset in range(MONTHS):
        for day_offset in range(DAYS_PER_MONTH):
            absolute_day_idx = month_offset * DAYS_PER_MONTH + day_offset
            for hour in range(HOURS_PER_DAY):
                ts = START + timedelta(days=absolute_day_idx, hours=hour)
                for station_id in station_ids:
                    for code, (lo, hi) in POLLUTANT_RANGES.items():
                        pollutant_id = pollutant_ids[code]
                        # Smooth diurnal swing + jitter — keeps values
                        # within the band but not flat.
                        diurnal = 0.5 * (1 + math.sin(2 * math.pi * hour / 24))
                        value = lo + diurnal * (hi - lo) + rng.uniform(-2.0, 2.0)
                        # Clip into the plausibility band (defensive —
                        # the jitter could overshoot near the edges).
                        value = min(max(value, lo), hi)
                        yield (station_id, pollutant_id, ts, value)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _bulk_load(dsn: str, *, batch_size: int = 10_000) -> tuple[int, float]:
    """Load 312K synthetic rows via batched ``insert_rows`` calls.

    Returns ``(inserted, elapsed_seconds)``. Splits the stream into
    `batch_size` chunks so a single executemany doesn't allocate one
    monolithic 25 MB payload list — that helps Windows + psycopg
    binary protocol stability on the test box.
    """
    with psycopg.connect(dsn) as conn:
        # Resolve dim FKs once; the IDs don't change for the duration
        # of the load.
        with conn.cursor() as cur:
            cur.execute("SELECT station_id FROM dim_station ORDER BY station_id")
            station_ids = [int(r[0]) for r in cur.fetchall()]
        pollutant_ids = load_pollutant_id_map(conn)
        assert set(POLLUTANT_RANGES).issubset(
            pollutant_ids
        ), f"dim_pollutant missing codes: {set(POLLUTANT_RANGES) - pollutant_ids.keys()}"

        batch: list[tuple[int, int, datetime, float]] = []
        total_inserted = 0
        started = time.perf_counter()
        for row in _iter_synthetic_rows(station_ids=station_ids, pollutant_ids=pollutant_ids):
            batch.append(row)
            if len(batch) >= batch_size:
                inserted, _ = insert_rows(batch, conn=conn, source="synthetic")
                total_inserted += inserted
                batch.clear()
        if batch:
            inserted, _ = insert_rows(batch, conn=conn, source="synthetic")
            total_inserted += inserted
        elapsed = time.perf_counter() - started

    return total_inserted, elapsed


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _fetch_explain_plan(conn: psycopg.Connection, *, month_start: datetime) -> str:
    """`EXPLAIN (ANALYZE, BUFFERS)` for a one-month range over the
    given partition's start date. Returns the plain-text plan.
    """
    month_end = (month_start + timedelta(days=32)).replace(day=1)
    with conn.cursor() as cur:
        cur.execute(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
            SELECT count(*), avg(value)
            FROM fact_measurements
            WHERE measured_at >= %s::timestamptz
              AND measured_at <  %s::timestamptz
            """,
            (month_start, month_end),
        )
        return "\n".join(row[0] for row in cur.fetchall())


def _fetch_index_sizes(conn: psycopg.Connection) -> dict[str, int]:
    """Return ``{logical_index_name: total_size_bytes}`` for the three
    0003 indexes, summed across every partition leaf.

    Partitioned tables have a *partitioned* (logical) index whose own
    relation is empty — the bytes live in the per-partition child
    indexes. ``pg_partition_tree(parent_index_oid)`` walks down to the
    leaf indexes; we sum ``pg_relation_size`` across them so the
    runbook can quote the actual on-disk footprint.
    """
    targets = (
        "fact_measurements_measured_at_brin",
        "fact_measurements_station_time_idx",
        "fact_measurements_pollutant_idx",
    )
    sizes: dict[str, int] = {}
    with conn.cursor() as cur:
        for name in targets:
            cur.execute(
                """
                SELECT COALESCE(SUM(pg_relation_size(relid)), 0)
                FROM pg_partition_tree(%s::regclass)
                """,
                (name,),
            )
            row = cur.fetchone()
            assert row is not None, f"index {name} missing"
            sizes[name] = int(row[0])
    return sizes


def _emit_perf_report(
    *,
    inserted: int,
    elapsed: float,
    plan: str,
    index_sizes: dict[str, int],
) -> None:
    """Write the measured numbers to a sidecar file the runbook
    references. Pytest captures stdout by default, so writing to disk
    keeps the report visible after a green run.

    The destination is `tests/integration/_artefacts/perf-last-run.txt`
    (gitignored — `_artefacts/` is a junk dir). Only the runbook itself
    is committed; this file is a forensic artefact for re-tuning.
    """
    out_dir = Path(__file__).resolve().parent / "_artefacts"
    out_dir.mkdir(exist_ok=True)
    report = out_dir / "perf-last-run.txt"
    lines = [
        f"inserted_rows={inserted}",
        f"wall_clock_seconds={elapsed:.3f}",
        f"throughput_rows_per_second={inserted / max(elapsed, 1e-6):.0f}",
        "index_sizes_bytes:",
    ]
    for name, size in index_sizes.items():
        lines.append(f"  {name}={size}")
    lines.append("")
    lines.append("explain_plan:")
    lines.append(plan)
    report.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures + tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def loaded_db(pg_container: str) -> Iterator[tuple[str, int, float]]:
    """Apply migrations + seed + bulk insert. Yields
    ``(dsn, inserted, elapsed_seconds)`` so multiple assertions can
    share the same expensive load instead of repeating it.

    Runs once per test module (T8). The package-level container
    fixture (``pg_container``) is scoped wider, but each test module
    that re-uses it must `reset_public_schema` to land on a clean
    slate.
    """
    reset_public_schema(pg_container)
    run_migrations(pg_container)
    seed_dim_station(pg_container)
    inserted, elapsed = _bulk_load(pg_container)
    yield pg_container, inserted, elapsed


@pytest.mark.integration
@pytest.mark.slow
class TestLoadPerformance:
    """312K-row smoke test — runtime, pruning, index footprint."""

    def test_dataset_shape_matches_design(self) -> None:
        """Sanity: 12 × 30 × 24 × 6 × 6 = 311,040 rows. If anyone
        edits the constants above, this assertion is the canary that
        the runbook numbers need re-pinning.
        """
        assert _expected_row_count() == 311_040

    def test_load_completes_within_sla(self, loaded_db: tuple[str, int, float]) -> None:
        """DoD: 312K rows must land in ≤ 60 s on local SSD."""
        _dsn, inserted, elapsed = loaded_db
        assert (
            inserted == _expected_row_count()
        ), f"loaded {inserted} rows, expected {_expected_row_count()}"
        assert (
            elapsed <= MAX_LOAD_SECONDS
        ), f"load took {elapsed:.2f}s (>{MAX_LOAD_SECONDS}s budget)"

    def test_partition_pruning_under_load(self, loaded_db: tuple[str, int, float]) -> None:
        """`EXPLAIN ANALYZE` for 2024-06 → only ``fact_measurements_2024_06``
        appears in the plan. Other monthly partitions must not leak.
        """
        dsn, _inserted, _elapsed = loaded_db
        june_start = datetime(2024, 6, 1, tzinfo=UTC)
        with psycopg.connect(dsn) as conn:
            plan = _fetch_explain_plan(conn, month_start=june_start)

        assert "fact_measurements_2024_06" in plan, plan
        for unwanted in (
            "fact_measurements_2024_05",
            "fact_measurements_2024_07",
            "fact_measurements_2025_06",
            "fact_measurements_default",
        ):
            assert unwanted not in plan, f"plan should not scan {unwanted}:\n{plan}"

    def test_brin_smaller_than_btree_composite(self, loaded_db: tuple[str, int, float]) -> None:
        """Substantive sanity: BRIN ≪ B-tree composite at 312K rows.

        Empty-table sizing in 0003 already asserts ``brin <= btree``
        (one page minimum each). Under load the gap should be at least
        an order of magnitude — append-only timestamp data is the BRIN
        textbook case. We assert a 5× factor as a comfortable floor;
        runbook documents the actual ratio.
        """
        dsn, _inserted, _elapsed = loaded_db
        with psycopg.connect(dsn) as conn:
            sizes = _fetch_index_sizes(conn)
        brin = sizes["fact_measurements_measured_at_brin"]
        btree = sizes["fact_measurements_station_time_idx"]
        assert brin > 0 and btree > 0
        assert btree >= 5 * brin, (
            f"BRIN ({brin} B) vs B-tree composite ({btree} B): expected "
            f"B-tree ≥ 5× BRIN, got ratio {btree / max(brin, 1):.2f}"
        )

    def test_emit_perf_report_artefact(self, loaded_db: tuple[str, int, float]) -> None:
        """Side-effect test: dump measurements to
        ``tests/integration/_artefacts/perf-last-run.txt`` so a human
        can re-pin the runbook from the freshest numbers without
        re-running the suite.
        """
        dsn, inserted, elapsed = loaded_db
        june_start = datetime(2024, 6, 1, tzinfo=UTC)
        with psycopg.connect(dsn) as conn:
            plan = _fetch_explain_plan(conn, month_start=june_start)
            sizes = _fetch_index_sizes(conn)
        _emit_perf_report(inserted=inserted, elapsed=elapsed, plan=plan, index_sizes=sizes)
        # The artefact existence check is the assertion — anything else
        # would race with parallel test runs.
        out_path = Path(__file__).resolve().parent / "_artefacts" / "perf-last-run.txt"
        assert out_path.is_file()
        body = out_path.read_text(encoding="utf-8")
        assert f"inserted_rows={inserted}" in body
        assert "fact_measurements_2024_06" in body
