"""Migration runner test suite.

Two layers:

* **Unit** — Pure Python: discovery regex, checksum determinism, drift
  detection logic. No DB required. Always run.
* **Integration** (`@pytest.mark.integration`) — Spins up a real PG 16
  container via `testcontainers` and applies the baseline migration
  end-to-end. Requires Docker daemon; `make test` skips by default,
  `make test-integration` (T9) will include them.

Coverage hedefi `infra/migrations/run.py` ≥ %85; unit testler driver
mantığı + drift, integration testler `apply_migration` happy path +
re-run idempotency + checksum mismatch'i kapsar.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest

from infra.migrations.run import (
    Migration,
    MigrationChecksumError,
    MigrationDiscoveryError,
    _verify_no_drift,
    discover_migrations,
    main,
    run,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Unit tests — discovery & checksum
# ---------------------------------------------------------------------------


class TestDiscoverMigrations:
    """`discover_migrations` filters, sorts, and rejects malformed files."""

    def test_finds_baseline_in_real_directory(self) -> None:
        migrations = discover_migrations()
        assert migrations, "0001_baseline.sql should be discovered"
        assert migrations[0].version == "0001"
        assert migrations[0].slug == "baseline"
        assert migrations[0].path.name == "0001_baseline.sql"
        assert len(migrations[0].checksum) == 64  # sha256 hex length

    def test_short_checksum_is_12_chars(self) -> None:
        migrations = discover_migrations()
        assert len(migrations[0].short_checksum) == 12
        assert migrations[0].checksum.startswith(migrations[0].short_checksum)

    def test_skips_down_sql_files(self, tmp_path: Path) -> None:
        (tmp_path / "0001_baseline.sql").write_text("SELECT 1;\n")
        (tmp_path / "0001_baseline.down.sql").write_text("SELECT 0;\n")
        migrations = discover_migrations(tmp_path)
        assert [m.version for m in migrations] == ["0001"]
        assert all(not m.path.name.endswith(".down.sql") for m in migrations)

    def test_sorts_by_version_lexicographically(self, tmp_path: Path) -> None:
        # Created out of order on purpose.
        (tmp_path / "0003_third.sql").write_text("-- 3\n")
        (tmp_path / "0001_first.sql").write_text("-- 1\n")
        (tmp_path / "0002_second.sql").write_text("-- 2\n")
        migrations = discover_migrations(tmp_path)
        assert [m.version for m in migrations] == ["0001", "0002", "0003"]

    def test_ignores_non_sql_files(self, tmp_path: Path) -> None:
        (tmp_path / "0001_real.sql").write_text("-- ok\n")
        (tmp_path / "README.md").write_text("# notes\n")
        (tmp_path / "0002_skip.txt").write_text("not sql\n")
        migrations = discover_migrations(tmp_path)
        assert [m.version for m in migrations] == ["0001"]

    def test_rejects_malformed_filename(self, tmp_path: Path) -> None:
        (tmp_path / "001_short_version.sql").write_text("-- bad\n")
        with pytest.raises(MigrationDiscoveryError, match="does not match"):
            discover_migrations(tmp_path)

    def test_rejects_uppercase_slug(self, tmp_path: Path) -> None:
        (tmp_path / "0001_BadCase.sql").write_text("-- bad\n")
        with pytest.raises(MigrationDiscoveryError, match="does not match"):
            discover_migrations(tmp_path)

    def test_raises_on_missing_directory(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        with pytest.raises(MigrationDiscoveryError, match="missing"):
            discover_migrations(missing)

    def test_checksum_is_deterministic(self, tmp_path: Path) -> None:
        path = tmp_path / "0001_x.sql"
        body_bytes = b"CREATE TABLE t (id INT);\n"
        # Use write_bytes — write_text on Windows translates "\n" to "\r\n".
        path.write_bytes(body_bytes)
        expected = hashlib.sha256(body_bytes).hexdigest()
        migrations = discover_migrations(tmp_path)
        assert migrations[0].checksum == expected


class TestVerifyNoDrift:
    """`_verify_no_drift` raises on edited migrations, no-op otherwise."""

    def _migration(self, version: str = "0001", checksum: str = "abc") -> Migration:
        return Migration(
            version=version,
            slug="baseline",
            path=Path("/tmp/fake.sql"),
            checksum=checksum,
        )

    def test_no_applied_means_no_drift(self) -> None:
        _verify_no_drift([self._migration()], applied={})  # no raise

    def test_matching_checksum_passes(self) -> None:
        m = self._migration(checksum="abc")
        _verify_no_drift([m], applied={"0001": "abc"})  # no raise

    def test_mismatched_checksum_raises(self) -> None:
        m = self._migration(checksum="aaa" * 22)  # 66 chars; truncated in msg
        with pytest.raises(MigrationChecksumError, match="has been edited"):
            _verify_no_drift([m], applied={"0001": "bbb" * 22})

    def test_pending_migration_not_in_applied_is_skipped(self) -> None:
        m1 = self._migration(version="0001", checksum="abc")
        m2 = self._migration(version="0002", checksum="def")
        _verify_no_drift([m1, m2], applied={"0001": "abc"})  # no raise


# ---------------------------------------------------------------------------
# Integration tests — real PG container
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_container(monkeypatch_session: pytest.MonkeyPatch) -> Iterator[str]:
    """Spin up PG 16 via testcontainers, yield psycopg-compatible DSN.

    Module-scoped so all integration tests in this file share one container
    (~5-8 sn cold-start). Each test resets the schema explicitly.

    Ryuk (testcontainers' reaper sidecar) is disabled because Windows +
    Docker Desktop port mapping for the reaper container is unreliable
    (`ConnectionError: Port mapping … 8080 not available`). With Ryuk off,
    the `with PostgresContainer(...)` context manager still cleans up its
    own container on exit; we just lose the orphan-cleanup safety net,
    which is acceptable for a deterministic test fixture.
    """
    pytest.importorskip("testcontainers.postgres")
    monkeypatch_session.setenv("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.4-alpine") as pg:
        # `get_connection_url()` returns `postgresql+psycopg2://…`. psycopg3
        # accepts the bare scheme `postgresql://` so we strip the driver hint.
        url = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        yield url


@pytest.fixture(scope="module")
def monkeypatch_session() -> Iterator[pytest.MonkeyPatch]:
    """Module-scoped MonkeyPatch (default fixture is function-scoped)."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _drop_public_schema(dsn: str) -> None:
    """Hard reset between tests so each one starts on a blank slate."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
        conn.commit()


@pytest.mark.integration
class TestRunIntegration:
    def test_first_run_applies_baseline(self, pg_container: str) -> None:
        _drop_public_schema(pg_container)
        applied = run(pg_container)
        assert applied >= 1

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT version, checksum FROM schema_migrations ORDER BY version")
            rows = cur.fetchall()
        versions = [r[0] for r in rows]
        assert "0001" in versions

    def test_second_run_is_noop(self, pg_container: str) -> None:
        _drop_public_schema(pg_container)
        first = run(pg_container)
        second = run(pg_container)
        assert first >= 1
        assert second == 0

    def test_dry_run_does_not_apply(self, pg_container: str) -> None:
        _drop_public_schema(pg_container)
        result = run(pg_container, dry_run=True)
        assert result == 0
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            # Bootstrap table exists (drift check requires it) but no
            # migration row should have been inserted in dry-run mode.
            cur.execute("SELECT COUNT(*) FROM schema_migrations")
            count = cur.fetchone()
            assert count is not None
            assert count[0] == 0

    def test_baseline_creates_expected_tables(self, pg_container: str) -> None:
        _drop_public_schema(pg_container)
        run(pg_container)
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name"
            )
            tables = {row[0] for row in cur.fetchall()}
        assert {"dim_station", "dim_pollutant", "fact_measurements"}.issubset(tables)
        # schema_migrations is the very first object — bootstrapped before baseline.
        assert "schema_migrations" in tables

    def test_baseline_seeds_six_pollutants(self, pg_container: str) -> None:
        _drop_public_schema(pg_container)
        run(pg_container)
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT code FROM dim_pollutant ORDER BY code")
            codes = {row[0] for row in cur.fetchall()}
        assert codes == {"co", "no2", "o3", "pm10", "pm25", "so2"}

    def test_checksum_drift_aborts_run(
        self,
        pg_container: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Edit a migration after apply → drift detector must abort."""
        # First run: a tiny one-shot migration directory.
        custom_dir = tmp_path / "migrations"
        custom_dir.mkdir()
        path = custom_dir / "0001_demo.sql"
        path.write_text("CREATE TABLE drift_demo (id INT);\n", encoding="utf-8")

        monkeypatch.setattr("infra.migrations.run.MIGRATIONS_DIR", custom_dir)
        _drop_public_schema(pg_container)
        run(pg_container)

        # Edit the file (content drift).
        path.write_text("CREATE TABLE drift_demo (id BIGINT);\n", encoding="utf-8")

        with pytest.raises(MigrationChecksumError, match="has been edited"):
            run(pg_container)

    def test_pending_migration_gets_applied_after_baseline(
        self,
        pg_container: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T2 handoff smoke: drop a 0002 file, expect runner picks it up."""
        custom_dir = tmp_path / "migrations"
        custom_dir.mkdir()
        baseline = custom_dir / "0001_baseline.sql"
        baseline.write_text(
            "CREATE TABLE alpha (id INT PRIMARY KEY);\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("infra.migrations.run.MIGRATIONS_DIR", custom_dir)
        _drop_public_schema(pg_container)
        first = run(pg_container)
        assert first == 1

        # Simulate database-architect adding a follow-up migration.
        (custom_dir / "0002_add_beta.sql").write_text(
            "CREATE TABLE beta (id INT PRIMARY KEY);\n",
            encoding="utf-8",
        )
        second = run(pg_container)
        assert second == 1

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            versions = [r[0] for r in cur.fetchall()]
        assert versions == ["0001", "0002"]

    def test_failing_migration_rolls_back(
        self,
        pg_container: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A SQL error inside a migration must not leave partial state."""
        custom_dir = tmp_path / "migrations"
        custom_dir.mkdir()
        (custom_dir / "0001_good.sql").write_text(
            "CREATE TABLE good (id INT);\n",
            encoding="utf-8",
        )
        # Intentional syntax error after a CREATE → entire migration must rollback.
        (custom_dir / "0002_bad.sql").write_text(
            "CREATE TABLE bad (id INT); SELECT * FROM nonexistent_xyz;\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("infra.migrations.run.MIGRATIONS_DIR", custom_dir)
        _drop_public_schema(pg_container)

        with pytest.raises(psycopg.Error):
            run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            # Migration 0001 stayed (committed in its own transaction).
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            assert [r[0] for r in cur.fetchall()] == ["0001"]
            # `bad` table did not survive 0002's failed transaction.
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='bad'"
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_main_cli_returns_zero_on_success(self, pg_container: str) -> None:
        _drop_public_schema(pg_container)
        rc = main(["--dsn", pg_container])
        assert rc == 0

    def test_main_cli_returns_nonzero_on_drift(
        self,
        pg_container: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        custom_dir = tmp_path / "migrations"
        custom_dir.mkdir()
        path = custom_dir / "0001_demo.sql"
        path.write_text("CREATE TABLE x (id INT);\n", encoding="utf-8")

        monkeypatch.setattr("infra.migrations.run.MIGRATIONS_DIR", custom_dir)
        _drop_public_schema(pg_container)
        assert main(["--dsn", pg_container]) == 0

        path.write_text("CREATE TABLE x (id BIGINT);\n", encoding="utf-8")
        assert main(["--dsn", pg_container]) == 2  # MigrationError exit


# ---------------------------------------------------------------------------
# Migration 0002 — unit tests (file shape, idempotency markers, no DROP)
# ---------------------------------------------------------------------------


_MIGRATION_0002_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "migrations" / "0002_star_schema_expand.sql"
)
_MIGRATION_0002_DOWN_SQL = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "migrations"
    / "0002_star_schema_expand.down.sql"
)


class TestMigration0002Files:
    """Static checks on 0002 SQL files — no DB required.

    These guard against accidental regression of the 0002 contract:
    forward script must be additive + idempotent, rollback script must
    contain the DROP statements documented in sprint-04 T2 DoD.
    """

    def test_forward_sql_exists(self) -> None:
        assert (
            _MIGRATION_0002_SQL.is_file()
        ), f"0002 forward migration missing at {_MIGRATION_0002_SQL}"

    def test_down_sql_exists(self) -> None:
        assert (
            _MIGRATION_0002_DOWN_SQL.is_file()
        ), f"0002 rollback companion missing at {_MIGRATION_0002_DOWN_SQL}"

    def test_forward_uses_idempotent_patterns(self) -> None:
        """`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`, and a
        `DO $$ BEGIN ... pg_constraint ... END $$` guard for the UNIQUE
        constraint must all be present.
        """
        body = _MIGRATION_0002_SQL.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS elevation_m" in body
        assert "ADD COLUMN IF NOT EXISTS updated_at" in body
        assert "CREATE TABLE IF NOT EXISTS dim_time" in body
        assert "DO $$" in body
        assert "pg_constraint" in body
        assert "fact_measurements_unique_reading" in body

    def test_forward_has_no_drop_or_extension(self) -> None:
        """Sprint-04 ret kriterleri: DROP yok, CREATE EXTENSION yok."""
        body = _MIGRATION_0002_SQL.read_text(encoding="utf-8").upper()
        # Comment block uses the words DROP/EXTENSION descriptively; strip
        # comment lines before scanning so they do not produce false hits.
        code_only = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        assert "DROP TABLE" not in code_only
        assert "DROP COLUMN" not in code_only
        assert "DROP CONSTRAINT" not in code_only
        assert "CREATE EXTENSION" not in code_only

    def test_forward_does_not_self_manage_transaction(self) -> None:
        """Runner wraps each file in a transaction; nested BEGIN/COMMIT
        breaks that contract (psycopg would error on nested begin).
        """
        body = _MIGRATION_0002_SQL.read_text(encoding="utf-8")
        # Strip line comments first.
        code_only = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        # Allow `BEGIN` only inside the `DO $$ BEGIN ... END $$` block.
        # A standalone "BEGIN;" or "COMMIT;" must not appear.
        assert "\nBEGIN;" not in code_only
        assert "\nCOMMIT;" not in code_only

    def test_down_lists_all_rollback_statements(self) -> None:
        body = _MIGRATION_0002_DOWN_SQL.read_text(encoding="utf-8")
        assert "DROP CONSTRAINT IF EXISTS fact_measurements_unique_reading" in body
        assert "DROP TABLE IF EXISTS dim_time" in body
        assert "DROP COLUMN IF EXISTS updated_at" in body
        assert "DROP COLUMN IF EXISTS elevation_m" in body

    def test_runner_skips_down_sql(self) -> None:
        """0002.down.sql must NOT be picked up by discover_migrations."""
        migrations = discover_migrations()
        names = {m.path.name for m in migrations}
        assert "0002_star_schema_expand.sql" in names
        assert "0002_star_schema_expand.down.sql" not in names


# ---------------------------------------------------------------------------
# Migration 0002 — integration tests (real PG 16 container)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMigration0002Integration:
    """End-to-end: 0001 baseline + 0002 expand against a live PG 16."""

    def test_applies_after_baseline(self, pg_container: str) -> None:
        """Sıralı apply: önce 0001, sonra 0002. schema_migrations'ta
        her iki version da kayıtlı olmalı.
        """
        _drop_public_schema(pg_container)
        applied = run(pg_container)
        assert applied >= 2  # at least baseline + 0002

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            versions = [r[0] for r in cur.fetchall()]
        assert "0001" in versions
        assert "0002" in versions

    def test_dim_station_gains_new_columns(self, pg_container: str) -> None:
        """`elevation_m` (NUMERIC, nullable) ve `updated_at` (TIMESTAMPTZ)
        kolonları eklenir; baseline'daki created_at/category bozulmaz.
        """
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'dim_station'
                ORDER BY ordinal_position
                """,
            )
            cols = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

        # Baseline kolonları korunmuş.
        for baseline_col in (
            "station_id",
            "slug",
            "name",
            "district",
            "lat",
            "lon",
            "category",
            "created_at",
        ):
            assert baseline_col in cols, f"baseline column dropped: {baseline_col}"

        # 0002 yeni kolonları.
        assert "elevation_m" in cols
        assert cols["elevation_m"][0] == "numeric"
        assert cols["elevation_m"][1] == "YES"  # nullable
        assert "updated_at" in cols
        # information_schema returns 'timestamp with time zone' for TIMESTAMPTZ.
        assert cols["updated_at"][0] == "timestamp with time zone"
        assert cols["updated_at"][1] == "NO"  # NOT NULL

    def test_dim_time_table_has_expected_shape(self, pg_container: str) -> None:
        """dim_time: 9 kolon (time_id, measured_at + 7 attribute), PK on
        time_id, UNIQUE on measured_at, season CHECK constraint.
        """
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'dim_time'
                ORDER BY ordinal_position
                """,
            )
            cols = [row[0] for row in cur.fetchall()]

        expected_cols = [
            "time_id",
            "measured_at",
            "year",
            "month",
            "day",
            "hour",
            "dow",
            "season",
            "is_holiday",
        ]
        assert cols == expected_cols, f"dim_time shape mismatch: {cols!r}"

        # PK + UNIQUE constraints.
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT conname, contype
                FROM pg_constraint
                WHERE conrelid = 'public.dim_time'::regclass
                ORDER BY conname
                """,
            )
            constraints = {row[0]: row[1] for row in cur.fetchall()}

        # contype: 'p' = primary key, 'u' = unique, 'c' = check.
        contypes = list(constraints.values())
        assert "p" in contypes, "dim_time missing PRIMARY KEY"
        assert "u" in contypes, "dim_time missing UNIQUE constraint"
        assert "c" in contypes, "dim_time missing CHECK constraint (season/month/day/hour/dow)"

    def test_pollutant_seed_survives_expand(self, pg_container: str) -> None:
        """0001 baseline'da seed edilen 6 satır 0002 sonrası KAYBOLMAMALI."""
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM dim_pollutant")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 6, f"pollutant seed lost: count={row[0]}"

            cur.execute("SELECT code FROM dim_pollutant ORDER BY code")
            codes = {r[0] for r in cur.fetchall()}
        assert codes == {"co", "no2", "o3", "pm10", "pm25", "so2"}

    def test_unique_constraint_blocks_duplicate_reading(
        self,
        pg_container: str,
    ) -> None:
        """fact_measurements_unique_reading: aynı (station_id, pollutant_id,
        measured_at, source) ikinci INSERT'ta UniqueViolation atmalı.
        """
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO dim_station (slug, name, district, lat, lon, category)
                    VALUES ('test', 'Test Station', 'Konak', 38.4, 27.1, 'urban')
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

                cur.execute(
                    """
                    INSERT INTO fact_measurements
                        (station_id, pollutant_id, measured_at, value, source)
                    VALUES (%s, %s, '2026-04-25 10:00:00+00', 12.5, 'csv')
                    """,
                    (station_id, pollutant_id),
                )
            conn.commit()

            # Second INSERT with same conflict key → UniqueViolation.
            with (
                pytest.raises(psycopg.errors.UniqueViolation),
                conn.cursor() as cur,
            ):
                cur.execute(
                    """
                    INSERT INTO fact_measurements
                        (station_id, pollutant_id, measured_at, value, source)
                    VALUES (%s, %s, '2026-04-25 10:00:00+00', 99.9, 'csv')
                    """,
                    (station_id, pollutant_id),
                )
            conn.rollback()

            # Different `source` for the same (station, pollutant, ts) IS allowed —
            # source is part of the unique key by design (TD-09 spec).
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fact_measurements
                        (station_id, pollutant_id, measured_at, value, source)
                    VALUES (%s, %s, '2026-04-25 10:00:00+00', 13.7, 'openweather')
                    """,
                    (station_id, pollutant_id),
                )
            conn.commit()

    def test_unique_constraint_has_stable_name(self, pg_container: str) -> None:
        """T4 (csv_loader) `ON CONFLICT ON CONSTRAINT fact_measurements_unique_reading`
        kullanacak — constraint adı sabit kalmalı.
        """
        _drop_public_schema(pg_container)
        run(pg_container)

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'public.fact_measurements'::regclass
                  AND contype = 'u'
                """,
            )
            unique_names = {row[0] for row in cur.fetchall()}
        assert "fact_measurements_unique_reading" in unique_names

    def test_full_chain_is_idempotent(self, pg_container: str) -> None:
        """`make migrate` ikinci çağrıda 0 migration uygular (drift yok)."""
        _drop_public_schema(pg_container)
        first = run(pg_container)
        second = run(pg_container)
        assert first >= 2  # baseline + 0002 (and possibly later sprints)
        assert second == 0

    def test_dim_station_alter_is_idempotent_on_rerun(
        self,
        pg_container: str,
    ) -> None:
        """Drift guard'ı manuel olarak bypass'layıp aynı 0002 SQL'ini iki
        kez ardışık execute etsek bile `ADD COLUMN IF NOT EXISTS` + DO block
        sayesinde hata vermez. Bu, runner'ın checksum guard'ı olmasaydı
        bile şema sözleşmesinin tek başına idempotent olduğunu kanıtlar.
        """
        _drop_public_schema(pg_container)
        run(pg_container)

        sql_body = _MIGRATION_0002_SQL.read_text(encoding="utf-8")
        with psycopg.connect(pg_container) as conn:
            with conn.cursor() as cur:
                cur.execute(sql_body)  # Should be a no-op on second apply.
            conn.commit()
