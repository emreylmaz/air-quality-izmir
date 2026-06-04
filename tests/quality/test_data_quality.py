"""Tests for `src.quality.data_quality`.

The DQ framework is psycopg-coupled but every check accepts a
connection-shaped object. We use `unittest.mock.MagicMock` so the
checks can be exercised in isolation without spinning up a database.

Integration tests (testcontainers PG) live in
`tests/integration/test_dq_runner.py` (planned for Sprint 12 T3).

DoD source: RAPOR_H16.md section 3F (data quality framework) and the
implicit Sprint 12 task list — completeness/freshness/validity/
uniqueness must each be exercised at unit level.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.quality.data_quality import (
    CompletenessCheck,
    DataQualityRunner,
    FreshnessCheck,
    QualityCheckResult,
    UniquenessCheck,
    ValidityCheck,
    _aggregate_status,
    default_post_ingestion_suite,
)


def _mock_conn_returning(*fetchone_results: object) -> MagicMock:
    """Build a connection mock whose successive `fetchone()` calls return
    `fetchone_results` in order.

    Each check executes one or more cursor queries; the mock's cursor
    plays back the prepared rows so test assertions can target the
    check's classification logic rather than SQL plumbing.
    """
    conn = MagicMock(name="psycopg.Connection")
    cursor = MagicMock(name="cursor")
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchone.side_effect = list(fetchone_results)
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# QualityCheckResult — serialisation
# ---------------------------------------------------------------------------


class TestQualityCheckResult:
    """The frozen dataclass must round-trip through JSON for JSONB persistence."""

    def test_to_dict_serialises_datetime_iso(self) -> None:
        ts = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
        result = QualityCheckResult(
            check_name="x",
            dimension="completeness",
            status="pass",
            metric_value=0.97,
            threshold=0.95,
            message="ok",
            checked_at=ts,
        )
        payload = result.to_dict()
        assert payload["checked_at"] == "2026-06-04T12:00:00+00:00"
        # The output must be JSON-serialisable (no remaining datetime obj).
        json.dumps(payload)

    def test_frozen_attribute_assignment_raises(self) -> None:
        result = QualityCheckResult(
            check_name="x",
            dimension="d",
            status="pass",
            metric_value=0.0,
            threshold=0.0,
            message="",
            checked_at=datetime.now(UTC),
        )
        with pytest.raises((AttributeError, Exception)):
            result.check_name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CompletenessCheck — ratio classification
# ---------------------------------------------------------------------------


class TestCompletenessCheck:
    """Expected = stations × pollutants × hours. Actual ÷ Expected → status."""

    def test_pass_when_ratio_above_strict(self) -> None:
        # Expected = 6 × 6 × 1 = 36; actual 35 → ratio 0.972 ≥ 0.95.
        conn = _mock_conn_returning((35,))
        result = CompletenessCheck().run(conn)
        assert result.status == "pass"
        assert result.metric_value == pytest.approx(35 / 36)

    def test_warn_between_strict_and_tolerance(self) -> None:
        # 30/36 = 0.833 — below strict 0.95, above tolerance 0.80.
        conn = _mock_conn_returning((30,))
        result = CompletenessCheck().run(conn)
        assert result.status == "warn"

    def test_fail_below_tolerance(self) -> None:
        # 20/36 = 0.555 < 0.80.
        conn = _mock_conn_returning((20,))
        result = CompletenessCheck().run(conn)
        assert result.status == "fail"

    def test_empty_table_fails(self) -> None:
        conn = _mock_conn_returning((0,))
        result = CompletenessCheck().run(conn)
        assert result.status == "fail"
        assert result.metric_value == 0.0


# ---------------------------------------------------------------------------
# FreshnessCheck — staleness gauge
# ---------------------------------------------------------------------------


class TestFreshnessCheck:
    """Staleness in seconds since the latest measured_at."""

    def test_pass_when_fresh(self) -> None:
        fresh = datetime.now(UTC) - timedelta(minutes=30)
        conn = _mock_conn_returning((fresh,))
        result = FreshnessCheck().run(conn)
        assert result.status == "pass"
        # ~1800 seconds ± a few.
        assert 1700 <= result.metric_value <= 1900

    def test_warn_between_thresholds(self) -> None:
        stale = datetime.now(UTC) - timedelta(hours=1, minutes=30)
        conn = _mock_conn_returning((stale,))
        result = FreshnessCheck().run(conn)
        assert result.status == "warn"

    def test_fail_when_very_stale(self) -> None:
        very_stale = datetime.now(UTC) - timedelta(hours=5)
        conn = _mock_conn_returning((very_stale,))
        result = FreshnessCheck().run(conn)
        assert result.status == "fail"

    def test_empty_table_fails_with_infinite_staleness(self) -> None:
        conn = _mock_conn_returning((None,))
        result = FreshnessCheck().run(conn)
        assert result.status == "fail"
        assert result.metric_value == float("inf")


# ---------------------------------------------------------------------------
# ValidityCheck — plausibility band coverage
# ---------------------------------------------------------------------------


class TestValidityCheck:
    """Per-pollutant in-band ratio; worst pollutant defines status."""

    def test_all_in_band_passes(self) -> None:
        # All 6 pollutants return 100 total / 100 in-band → ratio 1.0.
        rows = [("pm25", 100, 100)] * 6
        conn = _mock_conn_returning(*rows)
        result = ValidityCheck().run(conn)
        assert result.status == "pass"
        assert result.metric_value == 1.0

    def test_one_pollutant_below_strict_warns(self) -> None:
        # 5 pollutants 100/100, 1 pollutant 95/100 → worst ratio 0.95.
        rows = [("pm25", 100, 95)] + [("pm10", 100, 100)] * 5
        conn = _mock_conn_returning(*rows)
        result = ValidityCheck().run(conn)
        # 0.95 is exactly tolerance, just below strict 0.99 → warn.
        assert result.status == "warn"

    def test_severe_violation_fails(self) -> None:
        # One pollutant 100/100, another 50/100 → worst 0.50, well below tolerance.
        rows = [("pm25", 100, 50), ("pm10", 100, 100)] + [("no2", 0, 0)] * 4
        conn = _mock_conn_returning(*rows)
        result = ValidityCheck().run(conn)
        assert result.status == "fail"


# ---------------------------------------------------------------------------
# UniquenessCheck — duplicate count
# ---------------------------------------------------------------------------


class TestUniquenessCheck:
    """Zero duplicates expected; any positive count fails."""

    def test_no_duplicates_passes(self) -> None:
        conn = _mock_conn_returning((0,))
        result = UniquenessCheck().run(conn)
        assert result.status == "pass"
        assert result.metric_value == 0.0

    def test_any_duplicate_fails(self) -> None:
        conn = _mock_conn_returning((3,))
        result = UniquenessCheck().run(conn)
        assert result.status == "fail"
        assert result.metric_value == 3.0


# ---------------------------------------------------------------------------
# _aggregate_status — worst-of rule
# ---------------------------------------------------------------------------


class TestAggregateStatus:
    """Suite status = worst across all checks."""

    def _make_result(self, status: str) -> QualityCheckResult:
        return QualityCheckResult(
            check_name="x",
            dimension="d",
            status=status,  # type: ignore[arg-type]
            metric_value=0.0,
            threshold=0.0,
            message="",
            checked_at=datetime.now(UTC),
        )

    def test_empty_results_pass(self) -> None:
        assert _aggregate_status([]) == "pass"

    def test_all_pass(self) -> None:
        assert _aggregate_status([self._make_result("pass")] * 3) == "pass"

    def test_any_warn_warns(self) -> None:
        results = [self._make_result("pass"), self._make_result("warn")]
        assert _aggregate_status(results) == "warn"

    def test_any_fail_fails(self) -> None:
        results = [self._make_result("pass"), self._make_result("warn"), self._make_result("fail")]
        assert _aggregate_status(results) == "fail"


# ---------------------------------------------------------------------------
# DataQualityRunner — orchestration + persistence
# ---------------------------------------------------------------------------


class TestDataQualityRunner:
    """Runner executes checks, persists payload, returns results + status."""

    def test_run_persists_audit_row(self) -> None:
        conn = MagicMock(name="psycopg.Connection")
        cursor = MagicMock(name="cursor")
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        # No fetchone needed because the only check we add is a stub.
        cursor.fetchone.return_value = None
        conn.cursor.return_value = cursor

        runner = DataQualityRunner(suite_name="unit_test")

        class _StubPass:
            name = "stub"
            dimension = "completeness"

            def run(self, _conn: object) -> QualityCheckResult:
                return QualityCheckResult(
                    check_name=self.name,
                    dimension=self.dimension,
                    status="pass",
                    metric_value=1.0,
                    threshold=1.0,
                    message="ok",
                    checked_at=datetime.now(UTC),
                )

        runner.add_check(_StubPass())
        results, overall = runner.run(conn)
        assert overall == "pass"
        assert len(results) == 1
        # The INSERT must have been issued exactly once.
        execute_calls = [c for c in cursor.execute.call_args_list]
        assert any("INSERT INTO data_quality_runs" in str(call) for call in execute_calls)
        # commit() must be called to persist.
        conn.commit.assert_called_once()

    def test_check_exception_recorded_as_fail(self) -> None:
        conn = MagicMock(name="psycopg.Connection")
        cursor = MagicMock(name="cursor")
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cursor

        runner = DataQualityRunner(suite_name="unit_test")

        class _StubRaise:
            name = "broken"
            dimension = "validity"

            def run(self, _conn: object) -> QualityCheckResult:
                msg = "synthetic failure"
                raise RuntimeError(msg)

        runner.add_check(_StubRaise())
        results, overall = runner.run(conn)
        assert overall == "fail"
        assert results[0].status == "fail"
        assert "RuntimeError" in results[0].message

    def test_default_suite_has_four_checks(self) -> None:
        runner = default_post_ingestion_suite()
        assert len(runner.checks) == 4
        names = [c.name for c in runner.checks]
        assert "completeness_last_hour" in names
        assert "freshness_max_measured_at" in names
        assert "validity_plausibility_band" in names
        assert "uniqueness_natural_key" in names
