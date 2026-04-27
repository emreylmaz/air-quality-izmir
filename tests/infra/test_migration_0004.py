"""Migration 0004 — views + DQ audit table test suite.

Two layers (mirror of `test_migration_0003.py`):

* **Unit / static** — file-shape contract checks. No DB; always run.
  Verify forward + down files exist, that v_hourly_aqi is a MATERIALIZED
  VIEW (with `WITH NO DATA`), v_daily_trends is a regular view,
  `data_quality_runs` table + index declared, the matview's CONCURRENTLY
  refresh prerequisite UNIQUE INDEX is present, GRANT'ler `pg_roles`
  guard'ı içinde, no `CREATE EXTENSION`.
* **Integration** (`@pytest.mark.integration`) — real PG 16 via
  `testcontainers`. Apply 0001+0002+0003+0004 chain, assert empty-table
  REFRESH succeeds, v_daily_trends returns 0 rows on empty fact, audit
  insert + JSONB default works, matview UNIQUE index exists, GRANT
  guard is graceful when roles missing, second `run()` returns 0
  (idempotency).

DoD source: `docs/sprints/sprint-04.md` T6.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest

from infra.migrations.run import discover_migrations, run

if TYPE_CHECKING:
    from collections.abc import Iterator


_MIGRATION_0004_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "migrations" / "0004_views_and_audit.sql"
)
_MIGRATION_0004_DOWN_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "migrations" / "0004_views_and_audit.down.sql"
)


# ---------------------------------------------------------------------------
# Static / unit tests — file contract
# ---------------------------------------------------------------------------


class TestMigration0004Files:
    """Static checks on 0004 SQL files — no DB required.

    These guard against accidental regression of the 0004 contract:
    forward script must declare a MATERIALIZED view + UNIQUE index +
    regular view + audit table + role-guarded GRANTs, and must not
    introduce extensions or DROP statements (down.sql is the only DROP
    home).
    """

    def test_forward_sql_exists(self) -> None:
        assert (
            _MIGRATION_0004_SQL.is_file()
        ), f"0004 forward migration missing at {_MIGRATION_0004_SQL}"

    def test_down_sql_exists(self) -> None:
        assert (
            _MIGRATION_0004_DOWN_SQL.is_file()
        ), f"0004 rollback companion missing at {_MIGRATION_0004_DOWN_SQL}"

    def test_forward_creates_materialized_view_with_no_data(self) -> None:
        body = _MIGRATION_0004_SQL.read_text(encoding="utf-8")
        # IF NOT EXISTS keeps the migration idempotent on re-apply.
        assert "CREATE MATERIALIZED VIEW IF NOT EXISTS v_hourly_aqi" in body
        # WITH NO DATA: ilk yaratımda boş, sonradan REFRESH ile dolar.
        assert "WITH NO DATA" in body

    def test_forward_includes_aqi_placeholder(self) -> None:
        """AQI sütunu H7 streaming'e kadar `NULL::NUMERIC`."""
        body = _MIGRATION_0004_SQL.read_text(encoding="utf-8")
        assert "NULL::NUMERIC" in body
        # Kolon adı `aqi` olmalı (downstream Grafana/Streamlit referansı).
        assert " AS aqi" in body

    def test_forward_creates_unique_index_for_concurrent_refresh(self) -> None:
        """CONCURRENTLY refresh için PG zorunlu kıldığı UNIQUE INDEX."""
        body = _MIGRATION_0004_SQL.read_text(encoding="utf-8")
        assert "CREATE UNIQUE INDEX IF NOT EXISTS ix_v_hourly_aqi_pk" in body
        assert "ON v_hourly_aqi" in body

    def test_forward_creates_daily_trends_view(self) -> None:
        body = _MIGRATION_0004_SQL.read_text(encoding="utf-8")
        # CREATE OR REPLACE VIEW idempotent + regular (non-materialized).
        assert "CREATE OR REPLACE VIEW v_daily_trends" in body
        # Aggregate functions — DoD'da MIN/MAX/AVG zorunlu.
        for fn in ("MIN(", "MAX(", "AVG(", "COUNT("):
            assert fn in body, f"v_daily_trends missing aggregate {fn}"

    def test_forward_creates_dq_runs_table(self) -> None:
        body = _MIGRATION_0004_SQL.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS data_quality_runs" in body
        # BIGSERIAL PK + JSONB payload contract.
        assert "run_id        BIGSERIAL PRIMARY KEY" in body
        assert "payload       JSONB" in body
        assert "DEFAULT '{}'::jsonb" in body
        # Composite index on (suite_name, run_at DESC) for hot query pattern.
        assert "CREATE INDEX IF NOT EXISTS ix_dqr_suite_run_at" in body
        assert "(suite_name, run_at DESC)" in body

    def test_forward_grants_are_role_guarded(self) -> None:
        """Roller (app_reader/app_writer/grafana_ro) migration time'da
        olmayabilir; her GRANT bloğu `pg_roles` lookup'ı arkasında.
        """
        body = _MIGRATION_0004_SQL.read_text(encoding="utf-8")
        # DO block + pg_roles existence check.
        assert "DO $$" in body
        assert "FROM pg_catalog.pg_roles WHERE rolname = 'app_reader'" in body
        assert "FROM pg_catalog.pg_roles WHERE rolname = 'app_writer'" in body
        # writer must be able to insert into audit table.
        assert "GRANT SELECT, INSERT, UPDATE ON data_quality_runs TO app_writer" in body
        # reader gets SELECT on all three new objects.
        for obj in ("v_hourly_aqi", "v_daily_trends", "data_quality_runs"):
            assert f"GRANT SELECT ON {obj}" in body, f"app_reader missing GRANT on {obj}"

    def test_forward_has_no_drop_or_extension(self) -> None:
        """Sprint-04 ret kriterleri: DROP yok, CREATE EXTENSION yok."""
        body = _MIGRATION_0004_SQL.read_text(encoding="utf-8").upper()
        # Strip comment lines first so descriptive prose doesn't false-hit.
        code_only = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        assert "DROP TABLE" not in code_only
        assert "DROP VIEW" not in code_only
        assert "DROP MATERIALIZED VIEW" not in code_only
        assert "CREATE EXTENSION" not in code_only

    def test_forward_does_not_self_manage_transaction(self) -> None:
        """Runner wraps each file in a transaction; nested BEGIN/COMMIT
        breaks that contract."""
        body = _MIGRATION_0004_SQL.read_text(encoding="utf-8")
        code_only = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        assert "\nBEGIN;" not in code_only
        assert "\nCOMMIT;" not in code_only

    def test_runner_skips_down_sql(self) -> None:
        """0004.down.sql must NOT be picked up by discover_migrations."""
        migrations = discover_migrations()
        names = {m.path.name for m in migrations}
        assert "0004_views_and_audit.sql" in names
        assert "0004_views_and_audit.down.sql" not in names

    def test_full_chain_versions_present(self) -> None:
        """0001..0004 hepsi discover'da görünmeli, sırayla."""
        migrations = discover_migrations()
        versions = [m.version for m in migrations]
        for v in ("0001", "0002", "0003", "0004"):
            assert v in versions, f"missing {v} in discovered migrations"
        # Sıra koruması.
        assert versions.index("0001") < versions.index("0002")
        assert versions.index("0002") < versions.index("0003")
        assert versions.index("0003") < versions.index("0004")

    def test_down_lists_all_drop_statements(self) -> None:
        body = _MIGRATION_0004_DOWN_SQL.read_text(encoding="utf-8")
        assert "DROP TABLE IF EXISTS data_quality_runs" in body
        assert "DROP VIEW IF EXISTS v_daily_trends" in body
        # Matview'i düz DROP VIEW kaldırmaz; DROP MATERIALIZED VIEW gerek.
        assert "DROP MATERIALIZED VIEW IF EXISTS v_hourly_aqi" in body


# ---------------------------------------------------------------------------
# Integration tests — real PG 16 container
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def monkeypatch_session() -> Iterator[pytest.MonkeyPatch]:
    """Module-scoped MonkeyPatch (default fixture is function-scoped)."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def pg_container(monkeypatch_session: pytest.MonkeyPatch) -> Iterator[str]:
    """Spin up PG 16 via testcontainers, yield psycopg-compatible DSN.

    Same pattern as `test_migration_0003.pg_container` — module-scoped
    so all tests share one cold-start. Ryuk disabled for Windows + Docker
    Desktop port mapping reliability.
    """
    pytest.importorskip("testcontainers.postgres")
    monkeypatch_session.setenv("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.4-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        yield url


def _drop_public_schema(dsn: str) -> None:
    """Hard reset between tests so each starts on a blank slate."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
        conn.commit()


@pytest.mark.integration
class TestMigration0004Integration:
    """End-to-end: 0001 + 0002 + 0003 + 0004 against a live PG 16."""

    def test_chain_applies_in_order(self, pg_container: str) -> None:
        _drop_public_schema(pg_container)
        applied = run(pg_container)
        assert applied >= 4  # baseline + 0002 + 0003 + 0004

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            versions = [r[0] for r in cur.fetchall()]
        assert versions[:4] == ["0001", "0002", "0003", "0004"]

    def test_v_hourly_aqi_is_materialized(self, pg_container: str) -> None:
        """`relkind = 'm'` — matview, not regular view ('v')."""
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT relkind FROM pg_class
                WHERE relname = 'v_hourly_aqi'
                  AND relnamespace = 'public'::regnamespace
                """,
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "m", f"v_hourly_aqi should be matview (relkind={row[0]!r})"

    def test_v_hourly_aqi_unique_index_exists(self, pg_container: str) -> None:
        """CONCURRENTLY refresh için zorunlu UNIQUE INDEX yaratılmış mı."""
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename  = 'v_hourly_aqi'
                  AND indexname  = 'ix_v_hourly_aqi_pk'
                """,
            )
            row = cur.fetchone()
            assert row is not None, "ix_v_hourly_aqi_pk index missing"

    def test_v_hourly_aqi_refreshes_when_empty(self, pg_container: str) -> None:
        """`WITH NO DATA` ile yaratılan matview boş fact tablosunda
        REFRESH'te hata vermemeli (0 row üretir).
        """
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn:
            with conn.cursor() as cur:
                cur.execute("REFRESH MATERIALIZED VIEW v_hourly_aqi")
                cur.execute("SELECT COUNT(*) FROM v_hourly_aqi")
                row = cur.fetchone()
                assert row is not None
                assert row[0] == 0
            conn.commit()

    def test_v_hourly_aqi_concurrent_refresh_after_initial(
        self,
        pg_container: str,
    ) -> None:
        """İlk REFRESH (non-concurrent) sonrası CONCURRENTLY çalışmalı —
        UNIQUE INDEX prerequisite'i sağlandığı için.
        """
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn:
            # CONCURRENTLY autocommit gerekli (transaction içinde fail eder).
            conn.autocommit = True
            with conn.cursor() as cur:
                # İlk REFRESH non-concurrent (matview ilk başta `WITH NO
                # DATA` durumda; CONCURRENTLY ilk populate'te kullanılamaz).
                cur.execute("REFRESH MATERIALIZED VIEW v_hourly_aqi")
                # İkinci REFRESH CONCURRENTLY — H7'nin koşacağı şekilde.
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY v_hourly_aqi")

    def test_v_daily_trends_returns_zero_on_empty(self, pg_container: str) -> None:
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM v_daily_trends")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_v_daily_trends_aggregates_when_data_present(
        self,
        pg_container: str,
    ) -> None:
        """Functional smoke: 3 row insert → 1 grouped row, MIN/MAX/AVG
        beklenen değerler.
        """
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO dim_station (slug, name, district, lat, lon, category)
                    VALUES ('trend-test', 'Trend Test', 'Konak', 38.4, 27.1, 'urban')
                    RETURNING station_id
                    """,
                )
                row = cur.fetchone()
                assert row is not None
                station_id = row[0]
                cur.execute("SELECT pollutant_id FROM dim_pollutant WHERE code = 'pm25'")
                row = cur.fetchone()
                assert row is not None
                pollutant_id = row[0]

                # Aynı gün, aynı (station, pollutant), 3 farklı saat → tek
                # grouped row, sample_count=3.
                for hour, value, source in (
                    (10, 12.0, "csv"),
                    (11, 18.0, "openweather"),
                    (12, 6.0, "stream"),
                ):
                    cur.execute(
                        """
                        INSERT INTO fact_measurements
                            (station_id, pollutant_id, measured_at, value, source)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            station_id,
                            pollutant_id,
                            f"2024-06-15 {hour:02d}:00:00+00",
                            value,
                            source,
                        ),
                    )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT min_value, max_value, avg_value, sample_count
                    FROM v_daily_trends
                    WHERE station_id = %s AND pollutant_id = %s
                    """,
                    (station_id, pollutant_id),
                )
                row = cur.fetchone()
                assert row is not None
                min_value, max_value, avg_value, sample_count = row
                assert float(min_value) == 6.0
                assert float(max_value) == 18.0
                assert float(avg_value) == pytest.approx(12.0)
                assert sample_count == 3

    def test_data_quality_runs_insert_smoke(self, pg_container: str) -> None:
        """Audit tablosu basic insert: payload default `{}`, run_at default
        now(), run_id auto-increment.
        """
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO data_quality_runs
                        (suite_name, total_checks, passed, failed)
                    VALUES ('smoke', 1, 1, 0)
                    RETURNING run_id, run_at, payload
                    """,
                )
                row = cur.fetchone()
                assert row is not None
                run_id, run_at, payload = row
                assert run_id == 1
                assert run_at is not None
                # Default JSONB `{}` (psycopg loads JSONB as dict).
                assert payload == {}
            conn.commit()

    def test_data_quality_runs_payload_jsonb_roundtrip(
        self,
        pg_container: str,
    ) -> None:
        """JSONB payload custom dict → roundtrip equality."""
        from psycopg.types.json import Jsonb

        _drop_public_schema(pg_container)
        run(pg_container)

        sample = {"failed_checks": ["null_value", "range"], "threshold": 0.95}
        with psycopg.connect(pg_container) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO data_quality_runs
                        (suite_name, total_checks, passed, failed, payload)
                    VALUES ('roundtrip', 5, 3, 2, %s)
                    RETURNING payload
                    """,
                    (Jsonb(sample),),
                )
                row = cur.fetchone()
                assert row is not None
                assert row[0] == sample
            conn.commit()

    def test_data_quality_runs_index_exists(self, pg_container: str) -> None:
        """ix_dqr_suite_run_at composite index hot query pattern için."""
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename  = 'data_quality_runs'
                  AND indexname  = 'ix_dqr_suite_run_at'
                """,
            )
            row = cur.fetchone()
            assert row is not None

    def test_grants_skipped_when_roles_missing(self, pg_container: str) -> None:
        """testcontainers'da app_reader/app_writer/grafana_ro yok — DO
        block guard'ı sayesinde migration hata vermeden geçer.
        """
        _drop_public_schema(pg_container)
        applied = run(pg_container)  # should not raise
        assert applied >= 4

        # Sanity: rolün gerçekten yok olduğunu doğrula (guard'ın anlamlı
        # olduğunu kanıtlar).
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT rolname FROM pg_roles
                WHERE rolname IN ('app_reader', 'app_writer', 'grafana_ro')
                """,
            )
            present = {row[0] for row in cur.fetchall()}
        assert present == set(), f"unexpected roles in fresh container: {present}"

    def test_grants_applied_when_roles_present(self, pg_container: str) -> None:
        """Roller önceden yaratılırsa GRANT'ler doğru rol-obje matrisine
        düşer: app_reader → SELECT on (v_hourly_aqi, v_daily_trends,
        data_quality_runs); app_writer → INSERT on data_quality_runs.
        """
        _drop_public_schema(pg_container)

        # Pre-create roles before running migrations (init.sql'in
        # production'da yaptığı işin testcontainers eşdeğeri).
        with psycopg.connect(pg_container) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("CREATE ROLE app_reader LOGIN PASSWORD 'x'")  # pragma: allowlist secret
                cur.execute("CREATE ROLE app_writer LOGIN PASSWORD 'x'")  # pragma: allowlist secret

        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            # app_reader SELECT on view.
            cur.execute(
                """
                SELECT has_table_privilege('app_reader', 'v_hourly_aqi', 'SELECT')
                """,
            )
            row = cur.fetchone()
            assert row is not None and row[0] is True

            cur.execute(
                """
                SELECT has_table_privilege('app_reader', 'data_quality_runs', 'SELECT')
                """,
            )
            row = cur.fetchone()
            assert row is not None and row[0] is True

            # app_writer INSERT on audit.
            cur.execute(
                """
                SELECT has_table_privilege('app_writer', 'data_quality_runs', 'INSERT')
                """,
            )
            row = cur.fetchone()
            assert row is not None and row[0] is True

            # app_reader does NOT have INSERT (negative assertion).
            cur.execute(
                """
                SELECT has_table_privilege('app_reader', 'data_quality_runs', 'INSERT')
                """,
            )
            row = cur.fetchone()
            assert row is not None and row[0] is False

    def test_chain_is_idempotent(self, pg_container: str) -> None:
        """Second `run()` returns 0 — checksum guard skips applied versions."""
        _drop_public_schema(pg_container)
        first = run(pg_container)
        second = run(pg_container)
        assert first >= 4
        assert second == 0

    def test_pollutant_seed_survives_chain(self, pg_container: str) -> None:
        """0001 baseline'da seed edilen 6 satır 0004 sonrası KAYBOLMAMALI."""
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM dim_pollutant")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 6, f"pollutant seed lost: count={row[0]}"

    def test_migration_0004_completes_quickly(self, pg_container: str) -> None:
        """T6 acceptance: empty-table migration süresi makul (< 5 sn)."""
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT duration_ms FROM schema_migrations WHERE version = '0004'")
            row = cur.fetchone()
            assert row is not None
            duration_ms = row[0]
        assert duration_ms < 5000, f"0004 took {duration_ms} ms (>5 sn budget)"
