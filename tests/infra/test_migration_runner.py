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
