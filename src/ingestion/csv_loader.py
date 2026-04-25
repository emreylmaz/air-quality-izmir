"""Historical CSV loader — Çevre Bakanlığı open data → PostgreSQL.

Reads a wide-format hourly CSV (one column per pollutant), normalises to
the long format `(measured_at, pollutant_code, value)` expected by
`fact_measurements`, applies the cleaning pipeline documented in
sprint-03 (negative drop → IQR outlier filter → forward-fill ≤ 3h), and
batch-inserts via `psycopg.Cursor.executemany`.

The cleaning steps are split into pure functions so each rule can be
unit-tested in isolation. `load_csv` is the orchestrator used by the
CLI entry point.

Encoding:
    Çevre Bakanlığı CSV exports are typically `cp1254` (Windows-1254,
    Turkish) or `utf-8-sig` (BOM-prefixed UTF-8). We try both before
    giving up so the same loader works on either dump format.

Unit normalisation:
    The schema stores all pollutants in µg/m³. Most ministry CSVs already
    use µg/m³, but CO is sometimes reported in mg/m³ — when the observed
    CO maximum is below 100 we treat it as mg/m³ and multiply by 1000.
    This heuristic is conservative: real µg/m³ CO readings rarely fall
    under 100 outside very clean rural sites, where loss of those tiny
    values is preferable to silently keeping mg/m³ in a µg/m³ column.

Outlier filter:
    Tukey fence (Q1 - 1.5·IQR, Q3 + 1.5·IQR) per pollutant. Groups with
    fewer than 4 samples are passed through unchanged because the IQR is
    not stable on tiny samples.

Forward-fill:
    Per-pollutant, capped at 3 hourly slots. The CSV cadence is assumed
    hourly — gaps longer than 3 hours stay as NaN and are dropped before
    insert. Cadence is not interpolated; if the input is sparse, callers
    should resample upstream.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pandas as pd

from src.config.settings import get_settings

if TYPE_CHECKING:
    import psycopg

_LOG = logging.getLogger(__name__)

# Default mapping from CSV header (lowercased) → dim_pollutant.code. The
# matcher also accepts headers that *start with* a key followed by space
# or "(" so suffixes like "(µg/m³)" are tolerated.
DEFAULT_POLLUTANT_COLUMN_MAP: Final[Mapping[str, str]] = {
    "pm10": "pm10",
    "pm 10": "pm10",
    "pm2.5": "pm25",
    "pm 2.5": "pm25",
    "pm25": "pm25",
    "no2": "no2",
    "so2": "so2",
    "o3": "o3",
    "co": "co",
}

# Date column candidates (lowercased). First match wins.
DATE_COLUMN_CANDIDATES: Final[tuple[str, ...]] = (
    "tarih",
    "tarih saat",
    "datetime",
    "date",
    "timestamp",
    "date_time",
    "olcum tarihi",
    "ölçüm tarihi",
)

# Encoding fallback chain — utf-8-sig first because it also handles plain
# UTF-8 (BOM-aware), cp1254 for Turkish Windows exports.
ENCODING_CANDIDATES: Final[tuple[str, ...]] = ("utf-8-sig", "cp1254", "utf-8")

CO_MG_TO_UG_FACTOR: Final[float] = 1000.0
DEFAULT_FFILL_LIMIT_HOURS: Final[int] = 3
IQR_MULTIPLIER: Final[float] = 1.5
INSERT_SQL: Final[str] = (
    "INSERT INTO fact_measurements "
    "(station_id, pollutant_id, measured_at, value, source) "
    "VALUES (%s, %s, %s, %s, %s)"
)


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def read_csv(path: Path) -> pd.DataFrame:
    """Read CSV with encoding fallback. Auto-detects delimiter."""
    last_err: Exception | None = None
    for encoding in ENCODING_CANDIDATES:
        try:
            return pd.read_csv(path, encoding=encoding, sep=None, engine="python")
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
    raise UnicodeDecodeError(
        "csv_loader",
        b"",
        0,
        1,
        f"could not decode {path} with any of {ENCODING_CANDIDATES}: {last_err}",
    )


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = pd.Index([str(c).strip().lower() for c in df.columns])
    return df


def _find_date_column(columns: Sequence[str]) -> str:
    for cand in DATE_COLUMN_CANDIDATES:
        if cand in columns:
            return cand
    raise ValueError(
        f"csv missing date column; expected one of {DATE_COLUMN_CANDIDATES}, "
        f"found {list(columns)}"
    )


def _resolve_pollutant_columns(
    columns: Sequence[str],
    *,
    date_col: str,
    column_map: Mapping[str, str],
) -> dict[str, str]:
    """Return {csv_column → pollutant_code} for matching headers."""
    resolved: dict[str, str] = {}
    for col in columns:
        if col == date_col:
            continue
        if col in column_map:
            resolved[col] = column_map[col]
            continue
        for header, code in column_map.items():
            if col.startswith(f"{header} ") or col.startswith(f"{header}("):
                resolved[col] = code
                break
    return resolved


# ---------------------------------------------------------------------------
# Pipeline steps (pure)
# ---------------------------------------------------------------------------


def to_long_format(
    df: pd.DataFrame,
    *,
    column_map: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Pivot wide CSV → long `(measured_at, pollutant_code, value)`.

    Rows whose date column fails parsing are dropped. Pollutant columns
    are coerced numeric; non-numeric cells become NaN and are kept for
    the cleaning pipeline to handle (negative-drop ignores NaN; ffill
    can fill them).
    """
    mapping = dict(column_map or DEFAULT_POLLUTANT_COLUMN_MAP)
    df = _normalise_columns(df)
    date_col = _find_date_column(list(df.columns))

    pollutant_cols = _resolve_pollutant_columns(
        list(df.columns), date_col=date_col, column_map=mapping
    )
    if not pollutant_cols:
        raise ValueError(
            f"no pollutant columns matched; mapping_keys={sorted(mapping)}, "
            f"csv_columns={list(df.columns)}"
        )

    # `dayfirst=True` covers `01.01.2024 00:00` (TR) without breaking ISO.
    parsed_dt = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True, utc=True)
    df = df.assign(**{date_col: parsed_dt}).dropna(subset=[date_col])

    for col in pollutant_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    long = df.melt(
        id_vars=[date_col],
        value_vars=list(pollutant_cols),
        var_name="csv_column",
        value_name="value",
    ).rename(columns={date_col: "measured_at"})
    long["pollutant_code"] = long["csv_column"].map(pollutant_cols)
    long = long.drop(columns=["csv_column"])
    return long[["measured_at", "pollutant_code", "value"]].reset_index(drop=True)


def standardise_units(df: pd.DataFrame) -> pd.DataFrame:
    """Convert mg/m³ CO to µg/m³ when the magnitude indicates mg input.

    Heuristic: real µg/m³ CO readings are typically in the hundreds to
    low thousands. If the observed CO max is below 100 we treat the
    column as mg/m³ and multiply by 1000.
    """
    if df.empty:
        return df.copy()
    df = df.copy()
    co_mask = df["pollutant_code"] == "co"
    if co_mask.any():
        co_values = df.loc[co_mask, "value"].dropna()
        if not co_values.empty and co_values.max() < 100:
            df.loc[co_mask, "value"] = df.loc[co_mask, "value"] * CO_MG_TO_UG_FACTOR
            _LOG.info(
                "co values look like mg/m³ (max=%.3f); multiplied by %d to reach µg/m³",
                float(co_values.max()),
                int(CO_MG_TO_UG_FACTOR),
            )
    return df


def drop_negative(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows whose `value` is strictly negative. NaN is preserved."""
    if df.empty:
        return df.copy()
    keep = df["value"].isna() | (df["value"] >= 0)
    return df.loc[keep].reset_index(drop=True)


def iqr_filter(df: pd.DataFrame, *, multiplier: float = IQR_MULTIPLIER) -> pd.DataFrame:
    """Tukey-fence outlier filter, per pollutant code.

    Groups with < 4 non-NaN samples are passed through unchanged — the
    IQR is meaningless on tiny samples and we would otherwise over-prune
    sparse pollutants.
    """
    if df.empty:
        return df.copy()
    pieces: list[pd.DataFrame] = []
    for _code, group in df.groupby("pollutant_code", sort=False):
        values = group["value"].dropna()
        if len(values) < 4:
            pieces.append(group)
            continue
        q1 = float(values.quantile(0.25))
        q3 = float(values.quantile(0.75))
        iqr = q3 - q1
        lower = q1 - multiplier * iqr
        upper = q3 + multiplier * iqr
        in_range = group["value"].between(lower, upper) | group["value"].isna()
        pieces.append(group.loc[in_range])
    return pd.concat(pieces, ignore_index=True) if pieces else df.iloc[0:0].copy()


def forward_fill(
    df: pd.DataFrame,
    *,
    limit_hours: int = DEFAULT_FFILL_LIMIT_HOURS,
) -> pd.DataFrame:
    """Forward-fill NaN gaps ≤ `limit_hours` per pollutant.

    Assumes hourly cadence. Rows still NaN after fill are dropped so the
    insert never sees nullable values (the schema is NOT NULL).
    """
    if df.empty:
        return df.copy()
    df = df.sort_values(["pollutant_code", "measured_at"]).reset_index(drop=True)
    df["value"] = df.groupby("pollutant_code", sort=False)["value"].ffill(limit=limit_hours)
    return df.dropna(subset=["value"]).reset_index(drop=True)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Compose: standardise → drop_negative → iqr_filter → forward_fill."""
    return forward_fill(iqr_filter(drop_negative(standardise_units(df))))


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_pollutant_id_map(conn: psycopg.Connection) -> dict[str, int]:
    """Return `{code → pollutant_id}` from `dim_pollutant`."""
    with conn.cursor() as cur:
        cur.execute("SELECT code, pollutant_id FROM dim_pollutant")
        rows = cur.fetchall()
    return {str(code): int(pid) for code, pid in rows}


def insert_rows(
    rows: Sequence[tuple[int, int, datetime, float]],
    *,
    conn: psycopg.Connection,
    source: str = "csv",
) -> int:
    """Batch insert prepared `(station_id, pollutant_id, ts, value)` tuples.

    Wraps `cursor.executemany` (the psycopg3 idiomatic batch insert; the
    legacy `psycopg2.extras.execute_batch` was renamed and is no longer
    a separate API in psycopg3).
    """
    if not rows:
        return 0
    payload = [(s, p, t, v, source) for (s, p, t, v) in rows]
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, payload)
    conn.commit()
    return len(payload)


def _build_insert_payload(
    df: pd.DataFrame,
    *,
    station_id: int,
    pollutant_ids: Mapping[str, int],
) -> list[tuple[int, int, datetime, float]]:
    """Map cleaned long-form df → list of insert tuples.

    Rows with unknown pollutant codes are dropped with a warning so an
    unexpected column never silently fails the whole batch.
    """
    if df.empty:
        return []
    mapped = df.assign(pollutant_id=df["pollutant_code"].map(pollutant_ids))
    unknown = mapped["pollutant_id"].isna()
    if unknown.any():
        codes = sorted(set(mapped.loc[unknown, "pollutant_code"].tolist()))
        _LOG.warning(
            "dropping %d rows with unknown pollutant codes: %s",
            int(unknown.sum()),
            codes,
        )
        mapped = mapped.loc[~unknown]

    rows: list[tuple[int, int, datetime, float]] = []
    for ts, pid, val in zip(
        mapped["measured_at"], mapped["pollutant_id"], mapped["value"], strict=True
    ):
        # `pd.Timestamp` → stdlib datetime (psycopg accepts both, but the
        # stdlib type keeps mypy happy and preserves UTC tzinfo).
        py_ts = ts.to_pydatetime() if isinstance(ts, pd.Timestamp) else ts
        rows.append((station_id, int(pid), py_ts, float(val)))
    return rows


def load_csv(
    path: Path,
    station_id: int,
    *,
    conn: psycopg.Connection,
    column_map: Mapping[str, str] | None = None,
    source: str = "csv",
) -> int:
    """Read → clean → insert. Returns number of rows persisted.

    Args:
        path: Filesystem path to the CSV dump.
        station_id: Foreign key into `dim_station.station_id`.
        conn: Open psycopg connection. Caller owns lifecycle.
        column_map: Optional override of the header→code mapping.
        source: Value written to `fact_measurements.source` (default
            `'csv'` so historical loads are distinguishable from the
            live OpenWeather stream).
    """
    df = read_csv(Path(path))
    long = to_long_format(df, column_map=column_map)
    cleaned = clean(long)
    if cleaned.empty:
        _LOG.info("csv had no rows after cleaning: path=%s", path)
        return 0

    pollutant_ids = load_pollutant_id_map(conn)
    rows = _build_insert_payload(cleaned, station_id=station_id, pollutant_ids=pollutant_ids)
    inserted = insert_rows(rows, conn=conn, source=source)
    _LOG.info("csv loaded: path=%s station_id=%d rows=%d", path, station_id, inserted)
    return inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m src.ingestion.csv_loader <path> --station-id <int>`."""
    import psycopg  # local import keeps test imports lightweight

    parser = argparse.ArgumentParser(description="Load a historical CSV into PostgreSQL")
    parser.add_argument("path", type=Path, help="Path to CSV file")
    parser.add_argument("--station-id", type=int, required=True, help="dim_station FK")
    parser.add_argument(
        "--source",
        type=str,
        default="csv",
        help="Value for fact_measurements.source (default: csv)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    dsn = settings.database_url.get_secret_value()
    with psycopg.connect(dsn) as conn:
        n = load_csv(args.path, args.station_id, conn=conn, source=args.source)
    print(f"Inserted {n} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DEFAULT_POLLUTANT_COLUMN_MAP",
    "clean",
    "drop_negative",
    "forward_fill",
    "insert_rows",
    "iqr_filter",
    "load_csv",
    "load_pollutant_id_map",
    "main",
    "read_csv",
    "standardise_units",
    "to_long_format",
]
