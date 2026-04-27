"""Tests for `infra.postgres.seed_dim_station`.

Layered like `test_migration_runner.py` and `test_migration_0003.py`:

* **Unit** — DSN masking and YAML validation paths. No DB; always run.
* **Integration** (`@pytest.mark.integration`) — Real PG 16 via
  `testcontainers`. Apply 0001 + 0002 + 0003, then exercise the seed
  script for insert path, update path, edited-row propagation, and the
  pydantic validation error surface.

DoD source: `docs/sprints/sprint-04.md` T5.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import yaml
from pydantic import ValidationError

from infra.migrations.run import run as run_migrations
from infra.postgres import seed_dim_station as seed_module
from infra.postgres.seed_dim_station import _mask_dsn, main, seed

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Unit tests — DSN masking + YAML validation
# ---------------------------------------------------------------------------


class TestMaskDsn:
    """`_mask_dsn` strips credentials and tolerates malformed input.

    The seed script logs a host:port/db label so an accidental container
    log scrape never leaks the password. We assert on representative DSN
    flavours psycopg accepts.
    """

    def test_uri_form_strips_password(self) -> None:
        dsn = "postgresql://app:supersecret@db.example.com:5432/air_quality"  # pragma: allowlist secret
        masked = _mask_dsn(dsn)
        assert "supersecret" not in masked
        assert "app" not in masked  # username also stripped — only host:port/db kept
        assert "db.example.com:5432/air_quality" in masked

    def test_kv_form_strips_password(self) -> None:
        dsn = "host=localhost port=5432 dbname=air_quality user=app password=hunter2"  # pragma: allowlist secret
        masked = _mask_dsn(dsn)
        assert "hunter2" not in masked
        assert "localhost:5432/air_quality" in masked

    def test_unparseable_dsn_returns_placeholder(self) -> None:
        # An entirely garbage string has no = and no scheme — psycopg raises.
        masked = _mask_dsn("\x00not a dsn at all\x00")
        # Either the placeholder or a benign best-effort label; the only
        # contract we care about is that the raw garbage is not echoed.
        assert "\x00" not in masked


# ---------------------------------------------------------------------------
# Unit tests — YAML validation surface
# ---------------------------------------------------------------------------


class TestSeedValidation:
    """Validation errors must surface before we open a connection.

    We point `seed()` at a malformed YAML and expect pydantic to raise
    *before* the DSN is even dialled — otherwise the test would need a
    live database to assert the error path.
    """

    def test_seed_raises_on_invalid_lat(self, tmp_path: Path) -> None:
        bad = tmp_path / "stations.yaml"
        bad.write_text(
            yaml.safe_dump(
                {
                    "stations": [
                        {
                            "id": "konak",
                            "name": "Konak",
                            "district": "Konak",
                            "lat": 99.9,  # outside Izmir bbox (38.0..38.8)
                            "lon": 27.13,
                            "category": "urban_traffic",
                        },
                    ],
                },
            ),
            encoding="utf-8",
        )
        # DSN is irrelevant — validation fires first.
        with pytest.raises(ValidationError):
            seed("postgresql://nobody@127.0.0.1:1/none", stations_path=bad)

    def test_seed_raises_on_missing_field(self, tmp_path: Path) -> None:
        bad = tmp_path / "stations.yaml"
        bad.write_text(
            yaml.safe_dump(
                {
                    "stations": [
                        {
                            "id": "konak",
                            # missing 'name'
                            "district": "Konak",
                            "lat": 38.4,
                            "lon": 27.13,
                            "category": "urban_traffic",
                        },
                    ],
                },
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            seed("postgresql://nobody@127.0.0.1:1/none", stations_path=bad)


# ---------------------------------------------------------------------------
# Integration fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def monkeypatch_session() -> Iterator[pytest.MonkeyPatch]:
    """Module-scoped MonkeyPatch (default fixture is function-scoped)."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def pg_container(monkeypatch_session: pytest.MonkeyPatch) -> Iterator[str]:
    """PG 16 testcontainer; mirrors `test_migration_0003.pg_container`.

    Module-scoped so all tests in this file share one cold-start. Ryuk
    disabled for Windows + Docker Desktop reliability (see
    test_migration_runner.py docstring).
    """
    pytest.importorskip("testcontainers.postgres")
    monkeypatch_session.setenv("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.4-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        yield url


def _reset_and_migrate(dsn: str) -> None:
    """Drop public schema then re-apply the 0001..0003 migration chain."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
        conn.commit()
    run_migrations(dsn)


def _row_count(dsn: str) -> int:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM dim_station")
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# Integration tests — real PG 16 with full migration chain
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSeedDimStationIntegration:
    """End-to-end: 0001..0003 migrate → seed → re-seed → edit YAML → re-seed."""

    def test_first_run_inserts_all_stations(self, pg_container: str) -> None:
        _reset_and_migrate(pg_container)
        inserted, updated = seed(pg_container)
        assert inserted == 6
        assert updated == 0
        assert _row_count(pg_container) == 6

    def test_second_run_only_updates(self, pg_container: str) -> None:
        """Re-running the seed against the same YAML must not insert new
        rows; UPSERT bumps `updated_at` on every conflicting tuple even
        if values are byte-identical."""
        _reset_and_migrate(pg_container)
        first_inserted, first_updated = seed(pg_container)
        second_inserted, second_updated = seed(pg_container)
        assert first_inserted == 6
        assert first_updated == 0
        assert second_inserted == 0
        assert second_updated == 6
        # Row count still 6 — no duplicates.
        assert _row_count(pg_container) == 6

    def test_edited_yaml_propagates_to_db(
        self,
        pg_container: str,
        tmp_path: Path,
    ) -> None:
        """A name change in the YAML must update `dim_station.name` on
        the next seed run."""
        _reset_and_migrate(pg_container)
        # Bootstrap from the real catalog.
        seed(pg_container)

        # Prepare an edited copy: change Konak's display name.
        edited = tmp_path / "stations.yaml"
        edited.write_text(
            yaml.safe_dump(
                {
                    "stations": [
                        {
                            "id": "konak",
                            "name": "Konak (RENAMED)",
                            "district": "Konak",
                            "lat": 38.4192,
                            "lon": 27.1287,
                            "category": "urban_traffic",
                        },
                    ],
                },
            ),
            encoding="utf-8",
        )
        inserted, updated = seed(pg_container, stations_path=edited)
        assert inserted == 0
        assert updated == 1

        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT name FROM dim_station WHERE slug = 'konak'")
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "Konak (RENAMED)"

    def test_updated_at_is_bumped_on_update(self, pg_container: str) -> None:
        """`updated_at = now()` clause must move the timestamp forward."""
        _reset_and_migrate(pg_container)
        seed(pg_container)
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT updated_at FROM dim_station WHERE slug = 'konak'")
            row = cur.fetchone()
            assert row is not None
            first_ts = row[0]

        # Sleep would be flaky on fast systems; instead pin a known earlier
        # value then re-seed and assert the timestamp advanced.
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE dim_station SET updated_at = '2000-01-01T00:00+00' WHERE slug = 'konak'",
            )
            conn.commit()

        seed(pg_container)
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SELECT updated_at FROM dim_station WHERE slug = 'konak'")
            row = cur.fetchone()
            assert row is not None
            second_ts = row[0]
        assert second_ts > first_ts, (first_ts, second_ts)

    def test_main_returns_zero(
        self,
        pg_container: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """CLI smoke: `--dsn` override + exit code 0 + stderr summary."""
        _reset_and_migrate(pg_container)
        rc = main(["--dsn", pg_container])
        assert rc == 0
        assert "dim_station" in capsys.readouterr().err

    def test_main_uses_settings_when_dsn_omitted(
        self,
        pg_container: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When `--dsn` is not passed, main resolves DSN from Settings.

        We patch `get_settings` so we don't depend on env state, then
        assert the seed actually wrote rows (which means the DSN path
        was honoured end-to-end).
        """
        _reset_and_migrate(pg_container)

        from pydantic import SecretStr

        from src.config.settings import Settings

        fake_settings = Settings(database_url=SecretStr(pg_container))
        monkeypatch.setattr(seed_module, "get_settings", lambda: fake_settings)

        rc = main([])
        assert rc == 0
        assert _row_count(pg_container) == 6
