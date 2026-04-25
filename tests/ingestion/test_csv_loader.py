"""Unit tests for `src.ingestion.csv_loader`.

Strategy:
- Pure cleaning steps (`drop_negative`, `iqr_filter`, `forward_fill`,
  `standardise_units`, `to_long_format`) are tested in isolation with
  small in-memory DataFrames so each rule has a documented invariant.
- `load_csv` orchestration is exercised against the bundled 100-row
  fixture (`fixtures/izmir_sample_utf8.csv`) with a `MagicMock`
  connection. We assert the SQL+payload contract rather than touching
  a real database (psycopg integration is Hafta 4 territory).
- Encoding fallback is validated by writing a tmp cp1254-encoded copy.

The fixture has 100 hourly rows covering 5 days × 6 pollutants and
includes deliberate gaps (PM10 03:00 / 04:00) so forward-fill has
something to fill. Negative or extreme outliers are injected per-test
via DataFrame edits to keep the fixture itself representative of clean
input.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.ingestion import csv_loader as csv_module
from src.ingestion.csv_loader import (
    DEFAULT_FFILL_LIMIT_HOURS,
    INSERT_SQL,
    _build_insert_payload,
    clean,
    drop_negative,
    forward_fill,
    insert_rows,
    iqr_filter,
    load_csv,
    load_pollutant_id_map,
    read_csv,
    standardise_units,
    to_long_format,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "izmir_sample_utf8.csv"
EXPECTED_POLLUTANT_CODES = {"pm10", "pm25", "no2", "so2", "o3", "co"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def long_df() -> pd.DataFrame:
    """A small long-format frame for unit-testing the cleaning steps."""
    times = pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "measured_at": list(times) + list(times),
            "pollutant_code": ["pm10"] * 10 + ["pm25"] * 10,
            "value": [
                10.0,
                11.0,
                12.0,
                13.0,
                14.0,
                15.0,
                16.0,
                17.0,
                18.0,
                19.0,
                5.0,
                6.0,
                7.0,
                8.0,
                9.0,
                10.0,
                11.0,
                12.0,
                13.0,
                14.0,
            ],
        }
    )


@pytest.fixture
def pollutant_id_map() -> dict[str, int]:
    """Mirrors the seed in `schema.sql` (pm25=1, pm10=2, ...)."""
    return {"pm25": 1, "pm10": 2, "no2": 3, "so2": 4, "o3": 5, "co": 6}


@pytest.fixture
def mock_conn(pollutant_id_map: dict[str, int]) -> MagicMock:
    """A `psycopg.Connection` lookalike that serves the seed pollutant map."""
    conn = MagicMock(name="Connection")
    cursor = MagicMock(name="Cursor")
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchall = MagicMock(
        return_value=[(code, pid) for code, pid in pollutant_id_map.items()]
    )
    cursor.execute = MagicMock()
    cursor.executemany = MagicMock()
    conn.cursor = MagicMock(return_value=cursor)
    conn.commit = MagicMock()
    conn._cursor_for_assert = cursor  # convenience handle
    return conn


# ---------------------------------------------------------------------------
# read_csv: encoding fallback
# ---------------------------------------------------------------------------


def test_read_csv_loads_utf8_fixture() -> None:
    df = read_csv(FIXTURE_PATH)
    # Fixture spans 2024-01-01 00:00 → 2024-01-05 04:00 (101 hourly rows).
    assert len(df) == 101
    assert "Tarih" in df.columns


def test_read_csv_handles_cp1254(tmp_path: Path) -> None:
    """A Turkish-Windows-encoded file must decode without error."""
    text = "Tarih,PM10\n2024-01-01 00:00,30\n2024-01-01 01:00,32\n"
    path = tmp_path / "tr.csv"
    path.write_text(text, encoding="cp1254")
    df = read_csv(path)
    assert len(df) == 2
    assert df.iloc[0, 0] == "2024-01-01 00:00"


def test_read_csv_falls_back_to_cp1254_when_utf8_fails(tmp_path: Path) -> None:
    """Bytes that fail UTF-8 must still decode via the cp1254 fallback.

    `0xFC` is a valid cp1254 byte (`ü`) but a stray UTF-8 continuation
    that triggers `UnicodeDecodeError` on the first attempt. We assert
    the loader survives and the value round-trips intact.
    """
    text = "Tarih,PM10\n2024-01-01 00:00,üst-değer\n"
    path = tmp_path / "tr.csv"
    path.write_text(text, encoding="cp1254")
    df = read_csv(path)
    assert len(df) == 1
    assert "ü" in str(df.iloc[0, 1])


# ---------------------------------------------------------------------------
# to_long_format
# ---------------------------------------------------------------------------


def test_to_long_format_pivots_fixture() -> None:
    df = read_csv(FIXTURE_PATH)
    long = to_long_format(df)
    assert {"measured_at", "pollutant_code", "value"} == set(long.columns)
    assert set(long["pollutant_code"].unique()) == EXPECTED_POLLUTANT_CODES
    # 101 rows × 6 pollutants = 606 measurements (NaN preserved at this stage).
    assert len(long) == 606
    # Timestamps round-trip through pandas as UTC-aware.
    assert str(long["measured_at"].dt.tz) == "UTC"


def test_to_long_format_drops_unparseable_dates() -> None:
    df = pd.DataFrame(
        {
            "Tarih": ["2024-01-01 00:00", "not-a-date", "2024-01-01 01:00"],
            "PM10": [10.0, 11.0, 12.0],
        }
    )
    long = to_long_format(df)
    assert len(long) == 2  # the bad-date row is dropped


def test_to_long_format_raises_when_no_pollutant_columns_match() -> None:
    df = pd.DataFrame({"Tarih": ["2024-01-01 00:00"], "weird_col": [1]})
    with pytest.raises(ValueError, match="no pollutant columns matched"):
        to_long_format(df)


def test_to_long_format_raises_when_no_date_column() -> None:
    df = pd.DataFrame({"PM10": [1.0, 2.0], "PM2.5": [0.5, 0.6]})
    with pytest.raises(ValueError, match="csv missing date column"):
        to_long_format(df)


def test_to_long_format_accepts_custom_column_map() -> None:
    df = pd.DataFrame(
        {
            "Tarih": ["2024-01-01 00:00", "2024-01-01 01:00"],
            "PartiklerYuksek": [30.0, 32.0],
        }
    )
    long = to_long_format(df, column_map={"partikleryuksek": "pm10"})
    assert set(long["pollutant_code"].unique()) == {"pm10"}
    assert len(long) == 2


# ---------------------------------------------------------------------------
# standardise_units
# ---------------------------------------------------------------------------


def test_standardise_units_scales_co_when_in_mg(long_df: pd.DataFrame) -> None:
    co_rows = pd.DataFrame(
        {
            "measured_at": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
            "pollutant_code": ["co"] * 3,
            "value": [0.5, 1.0, 2.5],
        }
    )
    df = pd.concat([long_df, co_rows], ignore_index=True)
    standardised = standardise_units(df)
    co_after = standardised.loc[standardised["pollutant_code"] == "co", "value"].tolist()
    assert co_after == [500.0, 1000.0, 2500.0]


def test_standardise_units_leaves_co_when_already_in_ug() -> None:
    df = pd.DataFrame(
        {
            "measured_at": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
            "pollutant_code": ["co"] * 3,
            "value": [250.0, 480.0, 1200.0],  # max ≥ 100 → already µg/m³
        }
    )
    standardised = standardise_units(df)
    assert standardised["value"].tolist() == [250.0, 480.0, 1200.0]


def test_standardise_units_handles_empty_frame() -> None:
    empty = pd.DataFrame(columns=["measured_at", "pollutant_code", "value"])
    out = standardise_units(empty)
    assert out.empty


# ---------------------------------------------------------------------------
# drop_negative
# ---------------------------------------------------------------------------


def test_drop_negative_removes_only_strictly_negative_rows() -> None:
    df = pd.DataFrame(
        {
            "measured_at": pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC"),
            "pollutant_code": ["pm10"] * 4,
            "value": [10.0, -1.0, 0.0, float("nan")],
        }
    )
    out = drop_negative(df)
    assert out["value"].tolist()[:2] == [10.0, 0.0]
    assert pd.isna(out["value"].iloc[2])  # NaN retained for ffill
    assert len(out) == 3


def test_drop_negative_handles_empty_frame() -> None:
    out = drop_negative(pd.DataFrame(columns=["measured_at", "pollutant_code", "value"]))
    assert out.empty


# ---------------------------------------------------------------------------
# iqr_filter
# ---------------------------------------------------------------------------


def test_iqr_filter_removes_extreme_outliers(long_df: pd.DataFrame) -> None:
    spike = pd.DataFrame(
        {
            "measured_at": [pd.Timestamp("2024-01-01 10:00", tz="UTC")],
            "pollutant_code": ["pm10"],
            "value": [9999.0],  # way outside Q3 + 1.5·IQR
        }
    )
    polluted = pd.concat([long_df, spike], ignore_index=True)
    filtered = iqr_filter(polluted)
    pm10_max = filtered.loc[filtered["pollutant_code"] == "pm10", "value"].max()
    assert pm10_max <= 100.0  # spike gone, original samples kept
    assert 9999.0 not in filtered["value"].tolist()


def test_iqr_filter_passes_through_small_groups() -> None:
    df = pd.DataFrame(
        {
            "measured_at": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
            "pollutant_code": ["pm10"] * 3,
            "value": [10.0, 1000.0, 12.0],
        }
    )
    out = iqr_filter(df)
    # Fewer than 4 samples → IQR not applied → spike retained.
    assert 1000.0 in out["value"].tolist()


def test_iqr_filter_handles_empty_frame() -> None:
    empty = pd.DataFrame(columns=["measured_at", "pollutant_code", "value"])
    assert iqr_filter(empty).empty


# ---------------------------------------------------------------------------
# forward_fill
# ---------------------------------------------------------------------------


def test_forward_fill_within_limit_fills_short_gaps() -> None:
    df = pd.DataFrame(
        {
            "measured_at": pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC"),
            "pollutant_code": ["pm10"] * 5,
            "value": [10.0, float("nan"), float("nan"), 13.0, 14.0],
        }
    )
    out = forward_fill(df, limit_hours=DEFAULT_FFILL_LIMIT_HOURS)
    # Two-hour gap < 3h limit → both NaN positions filled with 10.0.
    assert out["value"].tolist() == [10.0, 10.0, 10.0, 13.0, 14.0]


def test_forward_fill_drops_gaps_longer_than_limit() -> None:
    df = pd.DataFrame(
        {
            "measured_at": pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC"),
            "pollutant_code": ["pm10"] * 6,
            "value": [10.0, float("nan"), float("nan"), float("nan"), float("nan"), 14.0],
        }
    )
    out = forward_fill(df, limit_hours=3)
    # Only first 3 NaNs are filled; the 4th remains NaN and is dropped.
    assert len(out) == 5
    assert out["value"].tolist() == [10.0, 10.0, 10.0, 10.0, 14.0]


def test_forward_fill_per_pollutant_isolation() -> None:
    df = pd.DataFrame(
        {
            "measured_at": [
                pd.Timestamp("2024-01-01 00:00", tz="UTC"),
                pd.Timestamp("2024-01-01 01:00", tz="UTC"),
                pd.Timestamp("2024-01-01 00:00", tz="UTC"),
                pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            ],
            "pollutant_code": ["pm10", "pm10", "pm25", "pm25"],
            "value": [10.0, float("nan"), float("nan"), 5.0],
        }
    )
    out = forward_fill(df)
    pm10 = out.loc[out["pollutant_code"] == "pm10", "value"].tolist()
    pm25 = out.loc[out["pollutant_code"] == "pm25", "value"].tolist()
    assert pm10 == [10.0, 10.0]  # filled within group
    assert pm25 == [5.0]  # leading NaN dropped, no value to ffill from


# ---------------------------------------------------------------------------
# clean (composition)
# ---------------------------------------------------------------------------


def test_clean_runs_full_pipeline_on_fixture() -> None:
    df = read_csv(FIXTURE_PATH)
    long = to_long_format(df)
    cleaned = clean(long)
    assert not cleaned.empty
    assert (cleaned["value"] >= 0).all()
    # CO max should now be in µg/m³ space (mg×1000); pre-clean CO max was
    # 3.08 mg/m³ → 3080 µg/m³.
    co_max = cleaned.loc[cleaned["pollutant_code"] == "co", "value"].max()
    assert co_max > 1000


def test_clean_returns_empty_for_empty_input() -> None:
    empty = pd.DataFrame(columns=["measured_at", "pollutant_code", "value"])
    assert clean(empty).empty


# ---------------------------------------------------------------------------
# Persistence (mocked)
# ---------------------------------------------------------------------------


def test_load_pollutant_id_map_returns_seed_codes(mock_conn: MagicMock) -> None:
    out = load_pollutant_id_map(mock_conn)
    assert set(out.keys()) == EXPECTED_POLLUTANT_CODES
    assert all(isinstance(v, int) for v in out.values())


def test_insert_rows_executes_batch_and_commits(mock_conn: MagicMock) -> None:
    rows = [
        (1, 2, datetime(2024, 1, 1, tzinfo=UTC), 32.0),
        (1, 2, datetime(2024, 1, 1, 1, tzinfo=UTC), 33.5),
    ]
    n = insert_rows(rows, conn=mock_conn, source="csv")
    assert n == 2
    cur = mock_conn._cursor_for_assert
    cur.executemany.assert_called_once()
    sql_arg, payload = cur.executemany.call_args.args
    assert sql_arg == INSERT_SQL
    assert payload[0] == (1, 2, datetime(2024, 1, 1, tzinfo=UTC), 32.0, "csv")
    mock_conn.commit.assert_called_once()


def test_insert_rows_short_circuits_on_empty_input(mock_conn: MagicMock) -> None:
    n = insert_rows([], conn=mock_conn)
    assert n == 0
    mock_conn._cursor_for_assert.executemany.assert_not_called()
    mock_conn.commit.assert_not_called()


def test_build_insert_payload_drops_unknown_codes(
    pollutant_id_map: dict[str, int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    df = pd.DataFrame(
        {
            "measured_at": [
                pd.Timestamp("2024-01-01 00:00", tz="UTC"),
                pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            ],
            "pollutant_code": ["pm10", "alien_gas"],
            "value": [30.0, 99.0],
        }
    )
    with caplog.at_level("WARNING", logger="src.ingestion.csv_loader"):
        rows = _build_insert_payload(df, station_id=1, pollutant_ids=pollutant_id_map)
    assert len(rows) == 1
    assert rows[0][1] == pollutant_id_map["pm10"]
    assert any("unknown pollutant" in r.message for r in caplog.records)


def test_load_csv_orchestrates_clean_and_insert(mock_conn: MagicMock) -> None:
    n = load_csv(FIXTURE_PATH, station_id=1, conn=mock_conn)
    assert n > 0

    cur = mock_conn._cursor_for_assert
    cur.executemany.assert_called_once()
    sql_arg, payload = cur.executemany.call_args.args
    assert sql_arg == INSERT_SQL
    # Each tuple: (station_id, pollutant_id, measured_at, value, source)
    assert all(t[0] == 1 for t in payload)
    assert all(t[4] == "csv" for t in payload)
    assert all(isinstance(t[2], datetime) for t in payload)
    # Insert size matches reported count.
    assert len(payload) == n


def test_load_csv_returns_zero_when_clean_produces_nothing(
    monkeypatch: pytest.MonkeyPatch,
    mock_conn: MagicMock,
    tmp_path: Path,
) -> None:
    """If cleaning empties the frame, no insert is attempted."""
    csv_path = tmp_path / "tiny.csv"
    csv_path.write_text("Tarih,PM10\n2024-01-01 00:00,-5\n", encoding="utf-8")

    n = load_csv(csv_path, station_id=1, conn=mock_conn)
    assert n == 0
    mock_conn._cursor_for_assert.executemany.assert_not_called()


def test_load_csv_propagates_unknown_pollutant_warning(
    mock_conn: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    pollutant_id_map: dict[str, int],
    tmp_path: Path,
) -> None:
    """Unknown pollutants in the seed map are dropped, not failed."""
    # Strip pm10 from the mock seed; the loader should drop those rows
    # without crashing the rest.
    partial = {k: v for k, v in pollutant_id_map.items() if k != "pm10"}
    cur = mock_conn._cursor_for_assert
    cur.fetchall.return_value = [(c, p) for c, p in partial.items()]

    n = load_csv(FIXTURE_PATH, station_id=1, conn=mock_conn)
    assert n > 0  # other pollutants still inserted
    sql_arg, payload = cur.executemany.call_args.args
    pollutant_ids = {row[1] for row in payload}
    assert pollutant_id_map["pm10"] not in pollutant_ids


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_invokes_load_csv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)

    fake_psycopg = MagicMock()
    fake_psycopg.connect = MagicMock(return_value=fake_conn)
    monkeypatch.setitem(__import__("sys").modules, "psycopg", fake_psycopg)

    def fake_load(path: Path, station_id: int, *, conn: Any, source: str) -> int:
        captured["path"] = path
        captured["station_id"] = station_id
        captured["source"] = source
        return 7

    monkeypatch.setattr(csv_module, "load_csv", fake_load)

    rc = csv_module.main([str(FIXTURE_PATH), "--station-id", "3", "--source", "csv-test"])

    assert rc == 0
    assert captured["station_id"] == 3
    assert captured["source"] == "csv-test"
    assert "Inserted 7 rows" in capsys.readouterr().err
