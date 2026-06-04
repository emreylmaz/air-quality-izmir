"""Sprint 5 ad-hoc bench — planner tuning EXPLAIN diff.

NOT a pytest test; a one-shot script invoked manually to fill in the
TODO blocks in `docs/sprints/sprint-05-perf.md`. Reuses the synthetic
data generator from `test_load_performance.py` so the row counts and
shapes match the Sprint 4 baseline runbook.

Strategy:

1. Spin up PG 16 testcontainer.
2. Apply 0001..0005 migration chain.
3. Seed `dim_station` from `config/stations.yaml`.
4. Bulk-load 311 040 synthetic rows (~50 s).
5. Open a *baseline* connection that overrides the database-level
   planner GUCs with `SET LOCAL` to PG's compile defaults
   (`random_page_cost = 4.0`, `effective_cache_size = '4GB'`).
6. Open a *tuned* connection that inherits the 0005 settings
   (`random_page_cost = 1.1`, `effective_cache_size = '2GB'`) from
   `pg_db_role_setting` — no per-session override.
7. Run the same `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` query on
   both connections; write both plans to
   `tests/integration/_artefacts/planner-tuning-diff.txt`.

The artefact is gitignored; the resulting text is pasted into the
runbook by hand. Re-run after any planner cost change.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from infra.migrations.run import run as run_migrations  # noqa: E402
from infra.postgres.seed_dim_station import seed as seed_dim_station  # noqa: E402
from tests.integration.test_load_performance import (  # noqa: E402
    _bulk_load,
    _fetch_explain_plan,
)


def _open(dsn: str, *, baseline: bool) -> psycopg.Connection:
    """Open a connection; if `baseline`, override planner GUCs back to PG defaults."""
    conn = psycopg.connect(dsn)
    if baseline:
        with conn.cursor() as cur:
            cur.execute("SET random_page_cost = 4.0")
            cur.execute("SET effective_cache_size = '4GB'")
    return conn


def _show_settings(conn: psycopg.Connection) -> dict[str, str]:
    out: dict[str, str] = {}
    with conn.cursor() as cur:
        for guc in ("random_page_cost", "effective_cache_size"):
            cur.execute(f"SHOW {guc}")
            row = cur.fetchone()
            assert row is not None
            out[guc] = row[0]
    return out


def _fetch_selective_plan(conn: psycopg.Connection) -> str:
    """A selective query (single station + pollutant + 1 month) — this
    is where the random_page_cost change can flip Seq Scan → Index Scan.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
            SELECT measured_at, value
            FROM fact_measurements
            WHERE measured_at >= '2024-06-01 00:00+00'::timestamptz
              AND measured_at <  '2024-07-01 00:00+00'::timestamptz
              AND station_id = 1
              AND pollutant_id = 1
            ORDER BY measured_at
            """
        )
        return "\n".join(row[0] for row in cur.fetchall())


def main() -> int:
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    out_dir = _REPO_ROOT / "tests" / "integration" / "_artefacts"
    out_dir.mkdir(exist_ok=True)
    report = out_dir / "planner-tuning-diff.txt"

    with PostgresContainer("postgres:16.4-alpine") as pg:
        dsn = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        print(f"[bench] container up: {dsn}")

        applied = run_migrations(dsn)
        print(f"[bench] migrations applied: {applied}")

        seed_dim_station(dsn)
        print("[bench] dim_station seeded")

        inserted, elapsed = _bulk_load(dsn)
        print(f"[bench] bulk load: {inserted} rows in {elapsed:.2f} s")

        # June 2024 → covered partition.
        month_start = datetime(2024, 6, 1, tzinfo=UTC)

        baseline_conn = _open(dsn, baseline=True)
        baseline_settings = _show_settings(baseline_conn)
        baseline_aggr = _fetch_explain_plan(baseline_conn, month_start=month_start)
        baseline_sel = _fetch_selective_plan(baseline_conn)
        baseline_conn.close()

        tuned_conn = _open(dsn, baseline=False)
        tuned_settings = _show_settings(tuned_conn)
        tuned_aggr = _fetch_explain_plan(tuned_conn, month_start=month_start)
        tuned_sel = _fetch_selective_plan(tuned_conn)
        tuned_conn.close()

        lines = [
            "=== Sprint 5 planner tuning bench ===",
            "container: postgres:16.4-alpine",
            f"loaded_rows: {inserted}",
            f"load_seconds: {elapsed:.3f}",
            "",
            "=== Query A — broad aggregate (full month, no filter) ===",
            "",
            "--- BASELINE (PG compile defaults) ---",
            f"random_page_cost = {baseline_settings['random_page_cost']}",
            f"effective_cache_size = {baseline_settings['effective_cache_size']}",
            "",
            baseline_aggr,
            "",
            "--- TUNED (0005 applied) ---",
            f"random_page_cost = {tuned_settings['random_page_cost']}",
            f"effective_cache_size = {tuned_settings['effective_cache_size']}",
            "",
            tuned_aggr,
            "",
            "=== Query B — selective (one station + one pollutant, ordered) ===",
            "",
            "--- BASELINE (PG compile defaults) ---",
            f"random_page_cost = {baseline_settings['random_page_cost']}",
            f"effective_cache_size = {baseline_settings['effective_cache_size']}",
            "",
            baseline_sel,
            "",
            "--- TUNED (0005 applied) ---",
            f"random_page_cost = {tuned_settings['random_page_cost']}",
            f"effective_cache_size = {tuned_settings['effective_cache_size']}",
            "",
            tuned_sel,
        ]
        report.write_text("\n".join(lines), encoding="utf-8")
        print(f"[bench] report written: {report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
