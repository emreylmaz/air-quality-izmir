"""PostgreSQL migration runner — pure psycopg, idempotent.

Uygulama sözleşmesi (H4 sprint-04 T1, blocker B2 onaylı):

* Migration dosyaları bu paket dizininde, `NNNN_<slug>.sql` formatında.
* `*.down.sql` dosyaları rollback amaçlıdır; runner bunları **uygulamaz**.
* `schema_migrations(version, applied_at, duration_ms, checksum)` tablosu
  ilk çalıştırmada bootstrap edilir (CREATE TABLE IF NOT EXISTS — idempotent).
* Her migration **ayrı transaction**'da uygulanır; başarısız bir migration
  yalnızca kendi değişikliklerini geri alır, önceki başarılı migration'lar
  kalıcıdır.
* Tekrar çalıştırma idempotent: uygulanmış migration'lar atlanır, kayıttaki
  checksum hâlâ uyuşuyorsa "0 migrations applied" döner.
* Daha önce uygulanmış bir migration dosyasının içeriği değişirse runner
  `MigrationChecksumError` fırlatır — bu, geçmişte uygulanmış SQL'in sessizce
  düzenlenmesine karşı güvenlik korumasıdır.
* Structured log (`logging`): her başarılı uygulamada `version`, `duration_ms`
  ve checksum prefix'i `key=value` formatında loglanır.

CLI:

    python -m infra.migrations.run                # apply pending
    python -m infra.migrations.run --dry-run      # list pending, do not apply
    python -m infra.migrations.run --dsn URL      # override Settings.database_url

DSN varsayılanı `src.config.settings.get_settings().database_url`. CI'da
testcontainers tarafı `--dsn` flag'iyle override eder.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import psycopg

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOG = logging.getLogger(__name__)

MIGRATIONS_DIR: Final[Path] = Path(__file__).resolve().parent

_MIGRATION_FILE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<version>\d{4})_(?P<slug>[a-z0-9_]+)\.sql$"
)

_BOOTSTRAP_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_ms INTEGER NOT NULL,
    checksum    TEXT NOT NULL
);
"""

_CHECKSUM_PREFIX_LEN: Final[int] = 12


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MigrationError(RuntimeError):
    """Base class for migration runner failures."""


class MigrationChecksumError(MigrationError):
    """Raised when an already-applied migration has been edited on disk."""


class MigrationDiscoveryError(MigrationError):
    """Raised when the migrations directory contains malformed filenames."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    """A single migration file: version + path + content checksum."""

    version: str
    slug: str
    path: Path
    checksum: str

    @property
    def short_checksum(self) -> str:
        """First 12 hex chars of sha256 — sufficient for log identification."""
        return self.checksum[:_CHECKSUM_PREFIX_LEN]


# ---------------------------------------------------------------------------
# Discovery & checksum
# ---------------------------------------------------------------------------


def _compute_checksum(path: Path) -> str:
    """sha256 of the file's bytes — deterministic, content-addressed."""
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    """Return migrations sorted by version. `.down.sql` files are skipped.

    The default `directory` is resolved at call time from the module-level
    `MIGRATIONS_DIR` attribute so tests can `monkeypatch.setattr` it on a
    per-test basis (Python freezes default arguments at function-definition
    time, which would otherwise capture the original path).

    Raises:
        MigrationDiscoveryError: if a `.sql` file does not match the
            `NNNN_<slug>.sql` naming convention (rollback files exempted).
    """
    if directory is None:
        directory = MIGRATIONS_DIR
    if not directory.is_dir():
        raise MigrationDiscoveryError(f"migrations directory missing: {directory}")

    migrations: list[Migration] = []
    seen_versions: set[str] = set()

    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.suffix != ".sql":
            continue
        if entry.name.endswith(".down.sql"):
            continue
        match = _MIGRATION_FILE_RE.match(entry.name)
        if match is None:
            raise MigrationDiscoveryError(
                f"migration filename does not match NNNN_<slug>.sql: {entry.name}"
            )
        version = match.group("version")
        if version in seen_versions:
            raise MigrationDiscoveryError(
                f"duplicate migration version detected: {version} (file={entry.name})"
            )
        seen_versions.add(version)
        migrations.append(
            Migration(
                version=version,
                slug=match.group("slug"),
                path=entry,
                checksum=_compute_checksum(entry),
            )
        )
    return migrations


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def ensure_bootstrap(conn: psycopg.Connection) -> None:
    """Create `schema_migrations` if absent. Idempotent.

    Bootstrap runs *before* any baseline migration so the version table
    is the very first object in the schema. Wrapped in its own transaction
    via psycopg's autocommit-off default + explicit `commit()`.
    """
    with conn.cursor() as cur:
        cur.execute(_BOOTSTRAP_SQL)
    conn.commit()


def applied_migrations(conn: psycopg.Connection) -> dict[str, str]:
    """Return `{version: checksum}` map of already-applied migrations."""
    with conn.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migrations")
        rows = cur.fetchall()
    return {str(version): str(checksum) for version, checksum in rows}


def apply_migration(conn: psycopg.Connection, migration: Migration) -> int:
    """Apply one migration in a single transaction.

    Returns:
        Wall-clock duration in milliseconds (capped at INT range — long
        migrations get truncated rather than overflowing the column type).

    Raises:
        psycopg.Error: re-raised after rollback so callers can decide
            whether to abort the run or continue with the next migration
            (the runner aborts; partial sequences are dangerous).
    """
    sql = migration.path.read_text(encoding="utf-8")
    started = time.perf_counter()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations "
                "(version, duration_ms, checksum) VALUES (%s, %s, %s)",
                (
                    migration.version,
                    int((time.perf_counter() - started) * 1000),
                    migration.checksum,
                ),
            )
        conn.commit()
    except psycopg.Error:
        conn.rollback()
        raise
    return int((time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _verify_no_drift(
    discovered: Sequence[Migration],
    applied: dict[str, str],
) -> None:
    """Refuse to continue if an applied migration's checksum changed.

    A drift means somebody edited 0001_baseline.sql (or similar) after it
    was already applied — the new content was never executed against the DB,
    so the schema state and the file diverge. Manual reconciliation needed.
    """
    for migration in discovered:
        recorded = applied.get(migration.version)
        if recorded is None:
            continue
        if recorded != migration.checksum:
            raise MigrationChecksumError(
                f"migration {migration.version} ({migration.slug}) has been edited "
                f"after apply: recorded_sha256={recorded[:_CHECKSUM_PREFIX_LEN]} "
                f"current_sha256={migration.short_checksum}. "
                "Refusing to continue. Investigate manually before re-running."
            )


def run(dsn: str, *, dry_run: bool = False) -> int:
    """Apply all pending migrations against `dsn`. Returns count applied.

    Args:
        dsn: PostgreSQL connection string. Caller resolves from settings.
        dry_run: If True, print pending plan and exit without applying.
    """
    discovered = discover_migrations()
    if not discovered:
        _LOG.warning("no migrations found in %s", MIGRATIONS_DIR)
        return 0

    with psycopg.connect(dsn) as conn:
        ensure_bootstrap(conn)
        applied = applied_migrations(conn)
        _verify_no_drift(discovered, applied)

        pending = [m for m in discovered if m.version not in applied]

        if dry_run:
            if not pending:
                _LOG.info(
                    "migration dry-run: 0 pending (applied=%d total=%d)",
                    len(applied),
                    len(discovered),
                )
            else:
                for m in pending:
                    _LOG.info(
                        "migration dry-run pending: version=%s slug=%s checksum=%s",
                        m.version,
                        m.slug,
                        m.short_checksum,
                    )
            return 0

        if not pending:
            _LOG.info(
                "0 migrations applied (already up-to-date, applied=%d total=%d)",
                len(applied),
                len(discovered),
            )
            return 0

        applied_count = 0
        for migration in pending:
            duration_ms = apply_migration(conn, migration)
            applied_count += 1
            _LOG.info(
                "migration applied: version=%s slug=%s duration_ms=%d checksum=%s",
                migration.version,
                migration.slug,
                duration_ms,
                migration.short_checksum,
            )

        _LOG.info(
            "%d migrations applied (total_now=%d)",
            applied_count,
            len(applied) + applied_count,
        )
        return applied_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_dsn(cli_dsn: str | None) -> str:
    """Pick the DSN: CLI flag > Settings.database_url. SecretStr unwrapped."""
    if cli_dsn:
        return cli_dsn
    # Local import keeps the runner importable without pydantic-settings
    # in environments where only the migrations package is shipped.
    from src.config.settings import get_settings

    return get_settings().database_url.get_secret_value()


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m infra.migrations.run [--dry-run] [--dsn ...]`."""
    parser = argparse.ArgumentParser(
        description="Apply pending PostgreSQL migrations (idempotent).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List pending migrations and exit without applying.",
    )
    parser.add_argument(
        "--dsn",
        type=str,
        default=None,
        help="Override Settings.database_url (testcontainers / CI use).",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Root logger level for the runner (default: INFO).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    dsn = _resolve_dsn(args.dsn)
    try:
        run(dsn, dry_run=args.dry_run)
    except MigrationError as exc:
        _LOG.error("migration runner aborted: %s", exc)
        return 2
    except psycopg.Error as exc:
        _LOG.error("migration runner DB error: %s", exc)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "MIGRATIONS_DIR",
    "Migration",
    "MigrationChecksumError",
    "MigrationDiscoveryError",
    "MigrationError",
    "applied_migrations",
    "apply_migration",
    "discover_migrations",
    "ensure_bootstrap",
    "main",
    "run",
]
