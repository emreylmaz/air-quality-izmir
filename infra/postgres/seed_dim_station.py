"""Seed `dim_station` from `config/stations.yaml` (idempotent UPSERT).

The Izmir station catalog (`config/stations.yaml`) is the single source of
truth for station identity. This script reads + validates the file via the
existing `src.ingestion.stations.load_stations` helper (pydantic v2) and
upserts each row into `dim_station`. Re-running is safe: changes in name /
district / lat / lon / category propagate, and `updated_at` is bumped on
every actual change.

Why a Python seed instead of an SQL migration?

* The YAML is the operational catalog; pinning lat/lon into a versioned
  migration would create drift the moment the YAML edits in dev.
* Validation (lat/lon bounding box, slug regex) lives in pydantic — easier
  to test than re-implementing in SQL CHECK.
* Migrations are content-checksummed (`schema_migrations.checksum`); editing
  station coordinates would otherwise be flagged as drift.

UPSERT semantics:

* `ON CONFLICT (slug) DO UPDATE` triggers per row, even when the new and
  existing values are identical. We therefore use the `xmax = 0` hint on
  `RETURNING` to distinguish *real* inserts from updates: PostgreSQL writes
  ``xmax = 0`` for a freshly-inserted tuple, non-zero for an update path.

CLI:

    python -m infra.postgres.seed_dim_station                     # apply
    python -m infra.postgres.seed_dim_station --dsn postgresql:// # override

DSN defaults to `Settings.database_url`. Logs are structured key=value so a
downstream observability layer can trivially scrape ``inserted=`` /
``updated=`` counts.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import psycopg

from src.config.settings import get_settings
from src.ingestion.stations import DEFAULT_STATIONS_PATH, Station, load_stations

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOG = logging.getLogger(__name__)


# `xmax = 0` discriminates fresh INSERT vs UPDATE on UPSERT — see module docstring.
# `updated_at` is set unconditionally so an actual data change is observable
# even when only one column shifted; UPSERT touches every conflicting row.
_UPSERT_SQL: Final[str] = """
INSERT INTO dim_station (slug, name, district, lat, lon, category)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    district = EXCLUDED.district,
    lat = EXCLUDED.lat,
    lon = EXCLUDED.lon,
    category = EXCLUDED.category,
    updated_at = now()
RETURNING (xmax = 0) AS inserted
"""


def _mask_dsn(dsn: str) -> str:
    """Return a host:port/dbname-only label safe for logs.

    `psycopg.conninfo.conninfo_to_dict` parses both URI and key=value DSN
    formats, so we can extract the non-secret pieces without a regex. If
    parsing fails (malformed DSN), we fall back to ``"<unparseable>"`` so
    the password is never echoed even on error paths.
    """
    try:
        parts = psycopg.conninfo.conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        return "<unparseable dsn>"
    host = parts.get("host", "?")
    port = parts.get("port", "?")
    dbname = parts.get("dbname", "?")
    return f"{host}:{port}/{dbname}"


def _upsert_stations(
    conn: psycopg.Connection,
    stations: Sequence[Station],
) -> tuple[int, int]:
    """Upsert each station, returning ``(inserted, updated)`` counts.

    Caller owns the connection lifecycle (commit/rollback). We commit
    once at the end so the script is atomic — partial state on failure is
    avoided.
    """
    inserted = 0
    updated = 0
    with conn.cursor() as cur:
        for station in stations:
            cur.execute(
                _UPSERT_SQL,
                (
                    station.id,
                    station.name,
                    station.district,
                    station.lat,
                    station.lon,
                    station.category,
                ),
            )
            row = cur.fetchone()
            if row is None:
                # Should not happen — INSERT … ON CONFLICT always returns a row.
                continue
            if row[0]:
                inserted += 1
            else:
                updated += 1
    conn.commit()
    return inserted, updated


def seed(
    dsn: str,
    *,
    stations_path: Path = DEFAULT_STATIONS_PATH,
) -> tuple[int, int]:
    """Load YAML → connect → UPSERT. Returns ``(inserted, updated)``.

    Args:
        dsn: PostgreSQL connection string. Caller is expected to source it
            from `Settings.database_url` (or override for tests/CI).
        stations_path: Override the YAML path. Defaults to the repo's
            `config/stations.yaml`.

    Raises:
        FileNotFoundError / ValueError / pydantic.ValidationError: bubbled
            up from `load_stations` when the YAML is invalid.
        psycopg.Error: on database failures (transaction is rolled back
            automatically by the context manager).
    """
    stations = load_stations(stations_path)
    _LOG.info(
        "dim_station seed start: stations=%d dsn=%s yaml=%s",
        len(stations),
        _mask_dsn(dsn),
        stations_path,
    )
    with psycopg.connect(dsn) as conn:
        inserted, updated = _upsert_stations(conn, stations)
    _LOG.info("dim_station: %d inserted, %d updated", inserted, updated)
    return inserted, updated


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m infra.postgres.seed_dim_station [--dsn URL]` entry point."""
    parser = argparse.ArgumentParser(
        description="Seed dim_station from config/stations.yaml (idempotent UPSERT)",
    )
    parser.add_argument(
        "--dsn",
        type=str,
        default=None,
        help="PostgreSQL connection string. Defaults to Settings.database_url.",
    )
    parser.add_argument(
        "--stations-path",
        type=Path,
        default=DEFAULT_STATIONS_PATH,
        help=f"YAML catalog path (default: {DEFAULT_STATIONS_PATH})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    dsn = args.dsn if args.dsn is not None else get_settings().database_url.get_secret_value()

    inserted, updated = seed(dsn, stations_path=args.stations_path)
    print(f"dim_station: {inserted} inserted, {updated} updated", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "main",
    "seed",
]
