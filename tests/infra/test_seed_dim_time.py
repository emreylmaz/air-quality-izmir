"""Tests for `infra.postgres.seed_dim_time`.

Layered like `test_seed_dim_station.py`:

* **Unit** — DSN masking, holiday YAML loading/validation, season
  derivation, and row-building math. No DB; always run.
* **Integration** (`@pytest.mark.integration`) — Real PG 16 via
  `testcontainers`. Apply the full 0001..0005 chain via the runner
  (auto-discover), then exercise the seed for the insert path,
  idempotency, holiday flagging, and season boundaries.

DoD source: `docs/sprints/sprint-05.md` T2.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
import yaml
from pydantic import ValidationError

from infra.migrations.run import run as run_migrations
from infra.postgres import seed_dim_time as seed_module
from infra.postgres.seed_dim_time import (
    SEED_END,
    SEED_START,
    HolidayEntry,
    _build_rows,
    _mask_dsn,
    _season_for_month,
    load_holidays,
    main,
    seed,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


# Expected hourly row count: 2024-01-01 00:00 .. 2025-12-31 23:00 inclusive.
# 2024 leap year (366d) + 2025 (365d) = 731 days * 24h = 17544 hours.
_EXPECTED_ROWS = 731 * 24


# ---------------------------------------------------------------------------
# Unit tests — DSN masking
# ---------------------------------------------------------------------------


class TestMaskDsn:
    """`_mask_dsn` strips credentials and tolerates malformed input."""

    def test_uri_form_strips_password(self) -> None:
        dsn = "postgresql://app:supersecret@db.example.com:5432/air_quality"  # pragma: allowlist secret
        masked = _mask_dsn(dsn)
        assert "supersecret" not in masked
        assert "db.example.com:5432/air_quality" in masked

    def test_kv_form_strips_password(self) -> None:
        dsn = "host=localhost port=5432 dbname=air_quality user=app password=hunter2"  # pragma: allowlist secret
        masked = _mask_dsn(dsn)
        assert "hunter2" not in masked
        assert "localhost:5432/air_quality" in masked

    def test_unparseable_dsn_returns_placeholder(self) -> None:
        masked = _mask_dsn("\x00not a dsn at all\x00")
        assert "\x00" not in masked


# ---------------------------------------------------------------------------
# Unit tests — holiday YAML loading + validation
# ---------------------------------------------------------------------------


class TestLoadHolidays:
    """`load_holidays` parses the catalog and surfaces validation errors."""

    def test_real_catalog_loads(self) -> None:
        """The repo's `config/tr_holidays.yaml` must parse without error."""
        holidays = load_holidays()
        # Sanity: known fixed official holidays present for both years.
        assert dt.date(2024, 10, 29) in holidays
        assert dt.date(2025, 4, 23) in holidays
        # Religious holiday from the Diyanet calendar.
        assert dt.date(2024, 4, 10) in holidays
        assert dt.date(2025, 6, 6) in holidays

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_holidays(tmp_path / "nope.yaml")

    def test_payload_without_holidays_key_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "h.yaml"
        bad.write_text(yaml.safe_dump({"other": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="holidays"):
            load_holidays(bad)

    def test_holidays_not_a_list_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "h.yaml"
        bad.write_text(yaml.safe_dump({"holidays": "oops"}), encoding="utf-8")
        with pytest.raises(ValueError, match="list"):
            load_holidays(bad)

    def test_invalid_type_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "h.yaml"
        bad.write_text(
            yaml.safe_dump(
                {"holidays": [{"date": "2024-01-01", "name": "X", "type": "bogus"}]},
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            load_holidays(bad)

    def test_extra_field_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "h.yaml"
        bad.write_text(
            yaml.safe_dump(
                {
                    "holidays": [
                        {
                            "date": "2024-01-01",
                            "name": "X",
                            "type": "official",
                            "surprise": 1,
                        },
                    ],
                },
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            load_holidays(bad)


class TestHolidayEntry:
    """`HolidayEntry` coerces ISO date strings and constrains `type`."""

    def test_parses_iso_date(self) -> None:
        entry = HolidayEntry(date="2024-04-23", name="Çocuk Bayramı", type="official")  # type: ignore[arg-type]
        assert entry.date == dt.date(2024, 4, 23)

    def test_religious_type_accepted(self) -> None:
        entry = HolidayEntry(date="2024-04-10", name="Ramazan", type="religious")  # type: ignore[arg-type]
        assert entry.type == "religious"


# ---------------------------------------------------------------------------
# Unit tests — season derivation
# ---------------------------------------------------------------------------


class TestSeasonForMonth:
    """TR meteorological season mapping must hit all four buckets."""

    @pytest.mark.parametrize(
        ("month", "expected"),
        [
            (12, "winter"),
            (1, "winter"),
            (2, "winter"),
            (3, "spring"),
            (4, "spring"),
            (5, "spring"),
            (6, "summer"),
            (7, "summer"),
            (8, "summer"),
            (9, "autumn"),
            (10, "autumn"),
            (11, "autumn"),
        ],
    )
    def test_month_maps_to_season(self, month: int, expected: str) -> None:
        assert _season_for_month(month) == expected


# ---------------------------------------------------------------------------
# Unit tests — row building
# ---------------------------------------------------------------------------


class TestBuildRows:
    """`_build_rows` derives time_id / dow / season / is_holiday correctly."""

    def test_full_window_row_count(self) -> None:
        rows = _build_rows(set())
        assert len(rows) == _EXPECTED_ROWS

    def test_window_endpoints(self) -> None:
        rows = _build_rows(set())
        assert rows[0][1] == SEED_START
        assert rows[-1][1] == SEED_END

    def test_time_id_formula(self) -> None:
        rows = _build_rows(
            set(), start=dt.datetime(2024, 4, 25, 14, 0), end=dt.datetime(2024, 4, 25, 14, 0)
        )
        # year*1e6 + month*1e4 + day*100 + hour
        assert rows[0][0] == 2024042514

    def test_dow_uses_postgres_convention(self) -> None:
        # 2024-01-07 is a Sunday → PG DOW 0.
        rows = _build_rows(
            set(), start=dt.datetime(2024, 1, 7, 0, 0), end=dt.datetime(2024, 1, 7, 0, 0)
        )
        assert rows[0][6] == 0
        # 2024-01-08 is a Monday → PG DOW 1.
        rows = _build_rows(
            set(), start=dt.datetime(2024, 1, 8, 0, 0), end=dt.datetime(2024, 1, 8, 0, 0)
        )
        assert rows[0][6] == 1

    def test_is_holiday_flag(self) -> None:
        holidays = {dt.date(2024, 4, 23)}
        rows = _build_rows(
            holidays, start=dt.datetime(2024, 4, 23, 0, 0), end=dt.datetime(2024, 4, 24, 23, 0)
        )
        holiday_rows = [r for r in rows if r[1].date() == dt.date(2024, 4, 23)]
        non_holiday_rows = [r for r in rows if r[1].date() == dt.date(2024, 4, 24)]
        assert len(holiday_rows) == 24
        assert all(r[8] is True for r in holiday_rows)
        assert all(r[8] is False for r in non_holiday_rows)


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
    """PG 16 testcontainer; mirrors `test_seed_dim_station.pg_container`."""
    pytest.importorskip("testcontainers.postgres")
    monkeypatch_session.setenv("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.4-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")
        yield url


def _reset_and_migrate(dsn: str) -> None:
    """Drop public schema then re-apply the full 0001..0004 migration chain."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
        conn.commit()
    run_migrations(dsn)


def _row_count(dsn: str) -> int:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM dim_time")
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _holiday_hours(dsn: str, day: dt.date) -> list[bool]:
    """Return the `is_holiday` flags for every hourly row of `day`."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT is_holiday FROM dim_time "
            "WHERE year = %s AND month = %s AND day = %s ORDER BY hour",
            (day.year, day.month, day.day),
        )
        rows = cur.fetchall()
    return [bool(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# Integration tests — real PG 16 with the full migration chain
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSeedDimTimeIntegration:
    """End-to-end: migrate → seed → re-seed → assert holiday + season data."""

    def test_first_run_inserts_full_window(self, pg_container: str) -> None:
        _reset_and_migrate(pg_container)
        inserted, updated = seed(pg_container)
        assert inserted == _EXPECTED_ROWS
        assert updated == 0
        assert _row_count(pg_container) == _EXPECTED_ROWS

    def test_second_run_is_idempotent(self, pg_container: str) -> None:
        """Re-running must not insert new rows — only conflicting updates."""
        _reset_and_migrate(pg_container)
        first_inserted, first_updated = seed(pg_container)
        second_inserted, second_updated = seed(pg_container)
        assert first_inserted == _EXPECTED_ROWS
        assert first_updated == 0
        assert second_inserted == 0
        assert second_updated == _EXPECTED_ROWS
        assert _row_count(pg_container) == _EXPECTED_ROWS

    def test_official_holiday_all_24_hours_flagged(self, pg_container: str) -> None:
        """A fixed official holiday (29 Ekim) has all 24 rows is_holiday=true."""
        _reset_and_migrate(pg_container)
        seed(pg_container)
        flags = _holiday_hours(pg_container, dt.date(2024, 10, 29))
        assert len(flags) == 24
        assert all(flags)

    def test_religious_holiday_all_24_hours_flagged(self, pg_container: str) -> None:
        """A Diyanet religious holiday (Ramazan Bayramı 2025) is flagged."""
        _reset_and_migrate(pg_container)
        seed(pg_container)
        flags = _holiday_hours(pg_container, dt.date(2025, 3, 30))
        assert len(flags) == 24
        assert all(flags)

    def test_non_holiday_day_not_flagged(self, pg_container: str) -> None:
        """An ordinary working day must have all 24 rows is_holiday=false."""
        _reset_and_migrate(pg_container)
        seed(pg_container)
        flags = _holiday_hours(pg_container, dt.date(2024, 2, 14))
        assert len(flags) == 24
        assert not any(flags)

    def test_season_boundary_feb_mar(self, pg_container: str) -> None:
        """Winter→spring boundary: 28 Feb winter, 1 Mar spring."""
        _reset_and_migrate(pg_container)
        seed(pg_container)
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT season FROM dim_time WHERE year=2024 AND month=2 AND day=28 AND hour=0",
            )
            feb = cur.fetchone()
            cur.execute(
                "SELECT season FROM dim_time WHERE year=2024 AND month=3 AND day=1 AND hour=0",
            )
            mar = cur.fetchone()
        assert feb is not None and feb[0] == "winter"
        assert mar is not None and mar[0] == "spring"

    def test_season_boundary_aug_sep(self, pg_container: str) -> None:
        """Summer→autumn boundary: 31 Aug summer, 1 Sep autumn."""
        _reset_and_migrate(pg_container)
        seed(pg_container)
        with psycopg.connect(pg_container) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT season FROM dim_time WHERE year=2025 AND month=8 AND day=31 AND hour=0",
            )
            aug = cur.fetchone()
            cur.execute(
                "SELECT season FROM dim_time WHERE year=2025 AND month=9 AND day=1 AND hour=0",
            )
            sep = cur.fetchone()
        assert aug is not None and aug[0] == "summer"
        assert sep is not None and sep[0] == "autumn"

    def test_reseed_propagates_holiday_change(self, pg_container: str) -> None:
        """An edited holiday YAML must flip is_holiday on the next run."""
        _reset_and_migrate(pg_container)
        seed(pg_container)
        # 2024-03-15 is not a holiday in the real catalog.
        assert not any(_holiday_hours(pg_container, dt.date(2024, 3, 15)))

        edited = Path(self._write_holiday_yaml(pg_container))
        seed(pg_container, holidays_path=edited)
        flags = _holiday_hours(pg_container, dt.date(2024, 3, 15))
        assert len(flags) == 24
        assert all(flags)

    @staticmethod
    def _write_holiday_yaml(_dsn: str) -> str:
        import tempfile

        fd = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8",
        )
        yaml.safe_dump(
            {"holidays": [{"date": "2024-03-15", "name": "Test", "type": "official"}]},
            fd,
        )
        fd.close()
        return fd.name

    def test_main_returns_zero(
        self,
        pg_container: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """CLI smoke: `--dsn` override + exit code 0 + stderr summary."""
        _reset_and_migrate(pg_container)
        rc = main(["--dsn", pg_container])
        assert rc == 0
        assert "dim_time" in capsys.readouterr().err

    def test_main_uses_settings_when_dsn_omitted(
        self,
        pg_container: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without `--dsn`, main resolves the DSN from Settings."""
        _reset_and_migrate(pg_container)

        from pydantic import SecretStr

        from src.config.settings import Settings

        fake_settings = Settings(database_url=SecretStr(pg_container))
        monkeypatch.setattr(seed_module, "get_settings", lambda: fake_settings)

        rc = main([])
        assert rc == 0
        assert _row_count(pg_container) == _EXPECTED_ROWS
