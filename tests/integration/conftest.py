"""Shared fixtures for the `tests/integration/` package.

We run the full migration chain plus `seed_dim_station` once per test
module via a *package-scoped* PG container so the cold-start cost
(image pull + initdb) is amortised across the schema-apply suite (T7)
and the load-performance suite (T8).

Why package-scope and not session-scope?
    The migration suites in `tests/infra/test_migration_0003.py` /
    `_0004.py` already stand up their own module-scoped containers; if
    we promoted *this* fixture to ``session`` it would still run side-
    by-side with those because pytest scopes are per-fixture, not per-
    container. Package scope keeps the integration-suite container
    isolated from the unit-suite ones while still being shared inside
    `tests/integration/`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="package")
def monkeypatch_package() -> Iterator[pytest.MonkeyPatch]:
    """Package-scoped MonkeyPatch (default fixture is function-scoped)."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="package")
def pg_container(monkeypatch_package: pytest.MonkeyPatch) -> Iterator[str]:
    """Spin up PG 16 via testcontainers, yield psycopg-compatible DSN.

    Mirrors the pattern in `tests/infra/test_migration_0003.py` so a
    Docker-Desktop-on-Windows host can rely on the same Ryuk-disable
    workaround. `testcontainers.postgres` is imported lazily so the
    unit suite (which never imports this package) stays free of the
    extra dependency at collection time.
    """
    pytest.importorskip("testcontainers.postgres")
    monkeypatch_package.setenv("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.4-alpine") as pg:
        # `get_connection_url()` returns a SQLAlchemy URL with the
        # `+psycopg2` driver suffix; psycopg3 accepts the bare
        # `postgresql://` scheme.
        url = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        yield url


def reset_public_schema(dsn: str) -> None:
    """Hard reset the `public` schema so each test starts blank.

    Exposed as a helper (not a fixture) so individual tests can call it
    *between* assertions when they need to re-apply migrations to a
    fresh slate inside the same module-scoped container.
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
        conn.commit()
