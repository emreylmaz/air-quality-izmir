"""Seed `dim_time` with hourly rows for the 2024-2025 window (idempotent UPSERT).

`dim_time` is the time dimension of the star schema (`0002_star_schema_expand.sql`).
This script generates one row per hour over a two-year window and upserts each
into `dim_time`, deriving `season` from the calendar month and `is_holiday`
from the static TR holiday catalog (`config/tr_holidays.yaml`).

Why a Python seed instead of an SQL migration?

* The holiday flag depends on `config/tr_holidays.yaml`; a pure-SQL
  `generate_series` seed would have to hardcode the holiday dates into the
  migration, creating drift the moment the YAML edits.
* `season`/`dow` derivation and the YAML join are trivially unit-testable in
  Python; re-implementing them in SQL CHECK/CASE is harder to verify.
* Migrations are content-checksummed (`schema_migrations.checksum`); editing
  the holiday set would otherwise be flagged as drift.

UPSERT semantics:

* `ON CONFLICT (time_id) DO UPDATE` re-runs propagate `is_holiday` and
  `season` changes (e.g. a corrected holiday date) without inserting
  duplicates. The `xmax = 0` hint on `RETURNING` distinguishes a fresh
  INSERT from an UPDATE so the script can report accurate counts.

CLI:

    python -m infra.postgres.seed_dim_time                     # apply
    python -m infra.postgres.seed_dim_time --dsn postgresql:// # override

DSN defaults to `Settings.database_url`. Logs are structured key=value and
never echo the DSN password (see `_mask_dsn`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import psycopg
import yaml
from psycopg.rows import tuple_row
from pydantic import BaseModel, ConfigDict

from src.config.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

_LOG = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOLIDAYS_PATH: Final[Path] = _REPO_ROOT / "config" / "tr_holidays.yaml"
"""Repo-root anchored so the catalog resolves regardless of process cwd."""

# Seed window: 2024-01-01 00:00 .. 2025-12-31 23:00 inclusive (hourly).
SEED_START: Final[dt.datetime] = dt.datetime(2024, 1, 1, 0, 0)
SEED_END: Final[dt.datetime] = dt.datetime(2025, 12, 31, 23, 0)


# `xmax = 0` discriminates fresh INSERT vs UPDATE on UPSERT — see module docstring.
_UPSERT_SQL: Final[str] = """
INSERT INTO dim_time (time_id, measured_at, year, month, day, hour, dow, season, is_holiday)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (time_id) DO UPDATE SET
    is_holiday = EXCLUDED.is_holiday,
    season = EXCLUDED.season
RETURNING (xmax = 0) AS inserted
"""

# Insert rows in batches to keep a single round-trip cheap without buffering
# the whole 17.5K-row set in one statement.
_BATCH_SIZE: Final[int] = 1000


class HolidayEntry(BaseModel):
    """A single TR holiday from `config/tr_holidays.yaml`.

    `type` is constrained to the two catalogued kinds so a typo in the YAML
    fails validation early rather than silently flowing into the seed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: dt.date
    name: str
    type: Literal["official", "religious"]


def _mask_dsn(dsn: str) -> str:
    """Return a host:port/dbname-only label safe for logs.

    `psycopg.conninfo.conninfo_to_dict` parses both URI and key=value DSN
    formats, so the non-secret pieces are extractable without a regex. On a
    parse failure the password is never echoed — a placeholder is returned.
    """
    try:
        parts = psycopg.conninfo.conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        return "<unparseable dsn>"
    host = parts.get("host", "?")
    port = parts.get("port", "?")
    dbname = parts.get("dbname", "?")
    return f"{host}:{port}/{dbname}"


def load_holidays(path: Path = DEFAULT_HOLIDAYS_PATH) -> set[dt.date]:
    """Load and validate `tr_holidays.yaml`, returning the set of holiday dates.

    Args:
        path: Path to the YAML catalog (defaults to `config/tr_holidays.yaml`).

    Returns:
        Set of `date` objects for which `is_holiday` must be true.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If the YAML payload is not a mapping with a `holidays`
            list.
        pydantic.ValidationError: If any entry fails `HolidayEntry`
            validation.
    """
    if not path.exists():
        msg = f"holiday catalog not found: {path}"
        raise FileNotFoundError(msg)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "holidays" not in payload:
        msg = f"holiday catalog must be a mapping with a 'holidays' key: {path}"
        raise ValueError(msg)
    raw = payload["holidays"]
    if not isinstance(raw, list):
        msg = f"'holidays' must be a list: {path}"
        raise ValueError(msg)
    entries = [HolidayEntry.model_validate(item) for item in raw]
    return {entry.date for entry in entries}


def _season_for_month(month: int) -> str:
    """Map a calendar month to a TR season label.

    Northern-hemisphere meteorological seasons: Dec-Feb winter, Mar-May
    spring, Jun-Aug summer, Sep-Nov autumn. Matches the `season` CHECK in
    `dim_time` (`winter|spring|summer|autumn`).
    """
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def _iter_hours(start: dt.datetime, end: dt.datetime) -> Iterator[dt.datetime]:
    """Yield each hourly timestamp from `start` to `end` inclusive."""
    current = start
    step = dt.timedelta(hours=1)
    while current <= end:
        yield current
        current += step


def _build_rows(
    holidays: set[dt.date],
    *,
    start: dt.datetime = SEED_START,
    end: dt.datetime = SEED_END,
) -> list[tuple[int, dt.datetime, int, int, int, int, int, str, bool]]:
    """Build the full `dim_time` row tuples for the seed window.

    `time_id` follows the schema formula
    ``year*1000000 + month*10000 + day*100 + hour``. `dow` uses the
    PostgreSQL `EXTRACT(DOW)` convention (0=Sunday..6=Saturday); Python's
    `weekday()` is 0=Monday so it is shifted by ``(weekday() + 1) % 7``.
    """
    rows: list[tuple[int, dt.datetime, int, int, int, int, int, str, bool]] = []
    for ts in _iter_hours(start, end):
        time_id = ts.year * 1_000_000 + ts.month * 10_000 + ts.day * 100 + ts.hour
        dow = (ts.weekday() + 1) % 7
        season = _season_for_month(ts.month)
        is_holiday = ts.date() in holidays
        rows.append(
            (
                time_id,
                ts,
                ts.year,
                ts.month,
                ts.day,
                ts.hour,
                dow,
                season,
                is_holiday,
            ),
        )
    return rows


def _upsert_rows(
    conn: psycopg.Connection,
    rows: Sequence[tuple[int, dt.datetime, int, int, int, int, int, str, bool]],
) -> tuple[int, int]:
    """Upsert every row in batches, returning ``(inserted, updated)`` counts.

    Caller owns the connection lifecycle. We commit once at the end so the
    seed is atomic — a partial window on failure is avoided.
    """
    inserted = 0
    updated = 0
    with conn.cursor(row_factory=tuple_row) as cur:
        for offset in range(0, len(rows), _BATCH_SIZE):
            batch = rows[offset : offset + _BATCH_SIZE]
            for row in batch:
                cur.execute(_UPSERT_SQL, row)
                result = cur.fetchone()
                if result is None:
                    # INSERT … ON CONFLICT always returns a row.
                    continue
                if result[0]:
                    inserted += 1
                else:
                    updated += 1
    conn.commit()
    return inserted, updated


def seed(
    dsn: str,
    *,
    holidays_path: Path = DEFAULT_HOLIDAYS_PATH,
) -> tuple[int, int]:
    """Load holidays → build rows → connect → UPSERT. Returns ``(inserted, updated)``.

    Args:
        dsn: PostgreSQL connection string. Caller is expected to source it
            from `Settings.database_url` (or override for tests/CI).
        holidays_path: Override the holiday YAML path. Defaults to the
            repo's `config/tr_holidays.yaml`.

    Raises:
        FileNotFoundError / ValueError / pydantic.ValidationError: bubbled
            up from `load_holidays` when the YAML is invalid.
        psycopg.Error: on database failures (the transaction is rolled back
            automatically by the context manager).
    """
    holidays = load_holidays(holidays_path)
    rows = _build_rows(holidays)
    _LOG.info(
        "dim_time seed start: rows=%d holidays=%d dsn=%s yaml=%s",
        len(rows),
        len(holidays),
        _mask_dsn(dsn),
        holidays_path,
    )
    with psycopg.connect(dsn) as conn:
        inserted, updated = _upsert_rows(conn, rows)
    _LOG.info("dim_time: %d inserted, %d updated", inserted, updated)
    return inserted, updated


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m infra.postgres.seed_dim_time [--dsn URL]` entry point."""
    parser = argparse.ArgumentParser(
        description="Seed dim_time with hourly rows for 2024-2025 (idempotent UPSERT)",
    )
    parser.add_argument(
        "--dsn",
        type=str,
        default=None,
        help="PostgreSQL connection string. Defaults to Settings.database_url.",
    )
    parser.add_argument(
        "--holidays-path",
        type=Path,
        default=DEFAULT_HOLIDAYS_PATH,
        help=f"TR holiday YAML path (default: {DEFAULT_HOLIDAYS_PATH})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    dsn = args.dsn if args.dsn is not None else get_settings().database_url.get_secret_value()

    inserted, updated = seed(dsn, holidays_path=args.holidays_path)
    print(f"dim_time: {inserted} inserted, {updated} updated", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "HolidayEntry",
    "load_holidays",
    "main",
    "seed",
]
