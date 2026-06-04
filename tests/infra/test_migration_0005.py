"""Migration 0005 — planner tuning test suite.

Two layers (mirror of `test_migration_0004.py`):

* **Unit / static** — file-shape contract checks. No DB; always run.
  Verify forward + down files exist, that the forward script only
  touches `random_page_cost` and `effective_cache_size`, that
  `ALTER SYSTEM` is **not** used (B3 sprint-05 decision), and that the
  rollback issues `RESET` for the same two parameters.
* **Integration** (`@pytest.mark.integration`) — real PG 16 via
  `testcontainers`. Apply the full 0001..0005 chain, assert the two
  parameters land in `pg_db_role_setting` for the current database, and
  that re-applying the migration is idempotent.

DoD source: `docs/sprints/sprint-05.md` T5.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest

from infra.migrations.run import discover_migrations, run

if TYPE_CHECKING:
    from collections.abc import Iterator


_MIGRATION_0005_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "migrations" / "0005_planner_tuning.sql"
)
_MIGRATION_0005_DOWN_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "migrations" / "0005_planner_tuning.down.sql"
)


# ---------------------------------------------------------------------------
# Static / unit tests — file contract
# ---------------------------------------------------------------------------


class TestMigration0005Files:
    """Static checks on 0005 SQL files — no DB required.

    These guard against accidental regression of the sprint-05 B2/B3
    decisions: only `random_page_cost` + `effective_cache_size` are set,
    via `ALTER DATABASE` (not `ALTER SYSTEM`), and rollback issues the
    matching `RESET` clauses.
    """

    def test_forward_sql_exists(self) -> None:
        assert (
            _MIGRATION_0005_SQL.is_file()
        ), f"0005 forward migration missing at {_MIGRATION_0005_SQL}"

    def test_down_sql_exists(self) -> None:
        assert (
            _MIGRATION_0005_DOWN_SQL.is_file()
        ), f"0005 rollback companion missing at {_MIGRATION_0005_DOWN_SQL}"

    def test_forward_sets_random_page_cost(self) -> None:
        """SSD profili — sprint-05 B2 kararı."""
        body = _MIGRATION_0005_SQL.read_text(encoding="utf-8")
        assert "random_page_cost = 1.1" in body

    def test_forward_sets_effective_cache_size(self) -> None:
        """Local container profili — sprint-05 B2 kararı."""
        body = _MIGRATION_0005_SQL.read_text(encoding="utf-8")
        # 2GB literal SQL string'i içinde tek tırnaklı.
        assert "effective_cache_size = ''2GB''" in body

    def test_forward_uses_alter_database_not_alter_system(self) -> None:
        """B3 kararı: managed PG'de uygulama superuser değil, ALTER SYSTEM
        reddedilir. Yalnız ALTER DATABASE kullanılmalı.
        """
        body = _MIGRATION_0005_SQL.read_text(encoding="utf-8")
        code_only = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        assert "ALTER DATABASE" in code_only
        assert "ALTER SYSTEM" not in code_only

    def test_forward_does_not_set_out_of_scope_params(self) -> None:
        """B2 kararı: `shared_buffers` + `work_mem` H5 scope dışı —
        restart riski + session bütçesi kapsam dışı.
        """
        body = _MIGRATION_0005_SQL.read_text(encoding="utf-8").lower()
        code_only = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        assert "shared_buffers" not in code_only
        assert "work_mem" not in code_only

    def test_forward_has_no_drop_or_extension(self) -> None:
        """Sprint-05 ret kriterleri: DROP yok, CREATE EXTENSION yok."""
        body = _MIGRATION_0005_SQL.read_text(encoding="utf-8").upper()
        code_only = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        assert "DROP TABLE" not in code_only
        assert "DROP COLUMN" not in code_only
        assert "CREATE EXTENSION" not in code_only

    def test_forward_does_not_self_manage_transaction(self) -> None:
        """Runner wraps each file in a transaction; nested BEGIN/COMMIT
        breaks that contract."""
        body = _MIGRATION_0005_SQL.read_text(encoding="utf-8")
        code_only = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("--")
        )
        assert "\nBEGIN;" not in code_only
        assert "\nCOMMIT;" not in code_only

    def test_down_resets_both_parameters(self) -> None:
        body = _MIGRATION_0005_DOWN_SQL.read_text(encoding="utf-8")
        assert "RESET random_page_cost" in body
        assert "RESET effective_cache_size" in body

    def test_runner_skips_down_sql(self) -> None:
        """0005.down.sql must NOT be picked up by discover_migrations."""
        migrations = discover_migrations()
        names = {m.path.name for m in migrations}
        assert "0005_planner_tuning.sql" in names
        assert "0005_planner_tuning.down.sql" not in names

    def test_full_chain_versions_present(self) -> None:
        """0001..0005 hepsi discover'da görünmeli, sırayla."""
        migrations = discover_migrations()
        versions = [m.version for m in migrations]
        for v in ("0001", "0002", "0003", "0004", "0005"):
            assert v in versions, f"missing {v} in discovered migrations"
        # Sıra koruması.
        assert versions.index("0004") < versions.index("0005")


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

    Mirrors `test_migration_0004.pg_container` — module-scoped so all
    tests share one cold-start. Ryuk disabled for Windows + Docker
    Desktop port mapping reliability.
    """
    pytest.importorskip("testcontainers.postgres")
    monkeypatch_session.setenv("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.4-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        yield url


def _settings_for_current_db(dsn: str) -> dict[str, str]:
    """Read `pg_db_role_setting` rows for the current database.

    Returns a `{param: value}` map by splitting the `setconfig` array
    entries (each is `name=value`). `setrole = 0` row holds the
    database-wide defaults (the only thing `ALTER DATABASE ... SET`
    writes — role-specific settings need an explicit role and are
    out of scope here).
    """
    sql = """
        SELECT s.setconfig
        FROM pg_db_role_setting s
        JOIN pg_database d ON d.oid = s.setdatabase
        WHERE d.datname = current_database() AND s.setrole = 0
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if row is None or row[0] is None:
        return {}
    settings: dict[str, str] = {}
    for entry in row[0]:
        if "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        settings[key] = value
    return settings


@pytest.mark.integration
class TestMigration0005Integration:
    """End-to-end: apply 0001..0005 → assert catalog → re-apply idempotent."""

    def test_full_chain_applies(self, pg_container: str) -> None:
        """Runner uygulanınca 0005 schema_migrations'a yazılır."""
        applied = run(pg_container)
        # En az 5 migration (0001..0005) uygulanmış olmalı (modül-scope
        # ilk run; sonraki testler zaten uygulanmış varsayar).
        assert applied >= 5

    def test_random_page_cost_in_catalog(self, pg_container: str) -> None:
        """ALTER DATABASE etkisi `pg_db_role_setting` katalogunda görünür."""
        run(pg_container)
        settings = _settings_for_current_db(pg_container)
        assert settings.get("random_page_cost") == "1.1"

    def test_effective_cache_size_in_catalog(self, pg_container: str) -> None:
        run(pg_container)
        settings = _settings_for_current_db(pg_container)
        assert settings.get("effective_cache_size") == "2GB"

    def test_second_run_is_idempotent(self, pg_container: str) -> None:
        """İkinci run 0 uygular (zaten schema_migrations'da)."""
        run(pg_container)
        second = run(pg_container)
        assert second == 0
        # Katalog hâlâ doğru — değer drift etmemiş.
        settings = _settings_for_current_db(pg_container)
        assert settings.get("random_page_cost") == "1.1"
        assert settings.get("effective_cache_size") == "2GB"

    def test_new_session_picks_up_setting(self, pg_container: str) -> None:
        """ALTER DATABASE SET yeni bağlantılarda otomatik geçerli olur."""
        run(pg_container)
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute("SHOW random_page_cost")
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "1.1"
