"""Data quality framework — check definitions, runner, audit persistence.

Sprint 12 deliverable: pluggable DQ check framework that runs after
every batch load and streaming micro-batch, persists results to the
`data_quality_runs` audit table (created in Sprint 4 migration 0004),
and exposes a programmatic API for Grafana alerting hooks
(Sprint 13 panel rules).

Architecture:

- `QualityCheckResult` — frozen dataclass holding one check's outcome.
- `QualityCheck` (Protocol) — runtime interface: `name`, `dimension`,
  `run(conn) -> QualityCheckResult`.
- 4 concrete check classes:
    * `CompletenessCheck` — expected hourly row count for a window.
    * `FreshnessCheck` — staleness gauge (`now() - max(measured_at)`).
    * `ValidityCheck` — per-pollutant plausibility band coverage.
    * `UniquenessCheck` — duplicate guard on UNIQUE constraint columns.
- `DataQualityRunner` — orchestrates a list of checks, persists each
  result into `data_quality_runs`, returns the full result list +
  overall suite status.

Persistence contract:

The `data_quality_runs` table (0004 migration) holds one row per
**suite invocation** with a JSONB `payload` containing the per-check
breakdown. We use JSONB rather than a normalised child table because
(a) check definitions evolve over time (new checks added in Sprint 13+),
(b) audit reads are append-only, so JSONB's query overhead is
acceptable, (c) Grafana's PG datasource can extract JSONB fields with
`->>'metric_value'` and chart them per check name.

Idempotency: a runner invocation creates exactly one audit row.
Re-running the suite produces a new row (not an upsert) — DQ history
is intentionally append-only for trend analysis.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, Protocol

if TYPE_CHECKING:
    import psycopg

_LOG = logging.getLogger(__name__)

CheckStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class QualityCheckResult:
    """Single quality check outcome.

    Attributes:
        check_name: Stable identifier for the check (e.g.
            ``"completeness_last_hour"``). Grafana groups time series
            by this name.
        dimension: One of "completeness", "freshness", "validity",
            "uniqueness", "consistency" — Kimball-style DQ taxonomy.
        status: "pass" (metric meets the strict threshold), "warn"
            (between strict and tolerance), "fail" (below tolerance).
        metric_value: Numerical measurement (e.g. completeness ratio,
            staleness seconds, duplicate count).
        threshold: The strict (pass) threshold for the check.
        message: Human-readable summary; appears in Grafana alert
            payloads.
        checked_at: Timestamp the check ran (UTC).
    """

    check_name: str
    dimension: str
    status: CheckStatus
    metric_value: float
    threshold: float
    message: str
    checked_at: datetime

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation for JSONB persistence."""
        payload = asdict(self)
        # datetime needs explicit ISO conversion — asdict leaves it as obj.
        payload["checked_at"] = self.checked_at.isoformat()
        return payload


class QualityCheck(Protocol):
    """Interface for a single quality check.

    Implementations must declare a stable `name` and `dimension` and
    accept a psycopg connection (the runner manages connection
    lifecycle; checks must not commit or close it).
    """

    name: str
    dimension: str

    def run(self, conn: psycopg.Connection) -> QualityCheckResult: ...


# ---------------------------------------------------------------------------
# Concrete checks
# ---------------------------------------------------------------------------

# Per-pollutant plausibility bands — the same ranges used by the perf
# test synthetic generator (`test_load_performance.POLLUTANT_RANGES`).
# Tightening these requires updating both files in lockstep.
_PLAUSIBILITY_BANDS: Final[dict[str, tuple[float, float]]] = {
    "pm25": (0.0, 500.0),
    "pm10": (0.0, 700.0),
    "no2": (0.0, 400.0),
    "so2": (0.0, 200.0),
    "o3_8h": (0.0, 600.0),
    "co": (0.0, 50_000.0),
}


@dataclass
class CompletenessCheck:
    """Expected hourly row count for `(stations × pollutants × hours)`.

    For a 1-hour window with 6 stations and 6 pollutants, the expected
    row count is 36. The check computes the actual count and compares
    against `expected × strict_threshold`. Below tolerance fails the
    check; in between strict and tolerance yields a warn.
    """

    name: str = "completeness_last_hour"
    dimension: str = "completeness"
    window_hours: int = 1
    expected_stations: int = 6
    expected_pollutants: int = 6
    strict_threshold: float = 0.95
    tolerance: float = 0.80

    def run(self, conn: psycopg.Connection) -> QualityCheckResult:
        cutoff_sql = "now() - %s::interval"
        sql = f"""
            SELECT COUNT(*)::int AS row_count
            FROM fact_measurements
            WHERE measured_at >= {cutoff_sql}
        """
        with conn.cursor() as cur:
            cur.execute(sql, (f"{self.window_hours} hours",))
            row = cur.fetchone()
        actual = int(row[0]) if row and row[0] is not None else 0
        expected = self.expected_stations * self.expected_pollutants * self.window_hours
        ratio = actual / expected if expected else 0.0
        status: CheckStatus = (
            "pass"
            if ratio >= self.strict_threshold
            else "warn"
            if ratio >= self.tolerance
            else "fail"
        )
        return QualityCheckResult(
            check_name=self.name,
            dimension=self.dimension,
            status=status,
            metric_value=ratio,
            threshold=self.strict_threshold,
            message=f"actual={actual} expected={expected} ratio={ratio:.3f}",
            checked_at=datetime.now(UTC),
        )


@dataclass
class FreshnessCheck:
    """Staleness gauge — seconds since the most recent measurement.

    A 2-hour ceiling reflects the API collector's 60-minute cron plus
    a buffer for network/queue delays. Below 1 hour is pass, 1-2 hours
    warn, above 2 hours fail (data pipeline is broken).
    """

    name: str = "freshness_max_measured_at"
    dimension: str = "freshness"
    strict_threshold_seconds: int = 60 * 60  # 1 hour
    tolerance_seconds: int = 2 * 60 * 60  # 2 hours

    def run(self, conn: psycopg.Connection) -> QualityCheckResult:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(measured_at) FROM fact_measurements")
            row = cur.fetchone()
        max_measured_at = row[0] if row else None
        if max_measured_at is None:
            return QualityCheckResult(
                check_name=self.name,
                dimension=self.dimension,
                status="fail",
                metric_value=float("inf"),
                threshold=float(self.strict_threshold_seconds),
                message="fact_measurements is empty — no measurements ingested",
                checked_at=datetime.now(UTC),
            )
        staleness = (datetime.now(UTC) - max_measured_at).total_seconds()
        status: CheckStatus = (
            "pass"
            if staleness <= self.strict_threshold_seconds
            else "warn"
            if staleness <= self.tolerance_seconds
            else "fail"
        )
        return QualityCheckResult(
            check_name=self.name,
            dimension=self.dimension,
            status=status,
            metric_value=staleness,
            threshold=float(self.strict_threshold_seconds),
            message=f"max_measured_at={max_measured_at.isoformat()} staleness={staleness:.0f}s",
            checked_at=datetime.now(UTC),
        )


@dataclass
class ValidityCheck:
    """Per-pollutant plausibility band coverage.

    For each pollutant code, computes the ratio of recent rows whose
    `value` falls inside the published plausibility band. Pre-processing
    (`csv_loader._clean_dataframe`) already drops negatives and IQR
    outliers — this check is the last-line guard.
    """

    name: str = "validity_plausibility_band"
    dimension: str = "validity"
    window_hours: int = 24
    strict_threshold: float = 0.99
    tolerance: float = 0.95

    def run(self, conn: psycopg.Connection) -> QualityCheckResult:
        sql = """
            SELECT dp.code,
                   COUNT(*) AS total_rows,
                   COUNT(*) FILTER (
                       WHERE fm.value >= %s AND fm.value <= %s
                   ) AS in_band_rows
            FROM fact_measurements fm
            JOIN dim_pollutant dp ON dp.pollutant_id = fm.pollutant_id
            WHERE fm.measured_at >= now() - %s::interval
              AND dp.code = %s
            GROUP BY dp.code
        """
        offenders: list[str] = []
        worst_ratio = 1.0
        with conn.cursor() as cur:
            for code, (lo, hi) in _PLAUSIBILITY_BANDS.items():
                cur.execute(sql, (lo, hi, f"{self.window_hours} hours", code))
                row = cur.fetchone()
                if row is None:
                    continue
                total, in_band = int(row[1]), int(row[2])
                if total == 0:
                    continue
                ratio = in_band / total
                if ratio < worst_ratio:
                    worst_ratio = ratio
                if ratio < self.tolerance:
                    offenders.append(f"{code}:{ratio:.3f}")
        status: CheckStatus = (
            "pass"
            if worst_ratio >= self.strict_threshold
            else "warn"
            if worst_ratio >= self.tolerance
            else "fail"
        )
        msg = f"worst_pollutant_ratio={worst_ratio:.3f}" + (
            f" offenders=[{','.join(offenders)}]" if offenders else ""
        )
        return QualityCheckResult(
            check_name=self.name,
            dimension=self.dimension,
            status=status,
            metric_value=worst_ratio,
            threshold=self.strict_threshold,
            message=msg,
            checked_at=datetime.now(UTC),
        )


@dataclass
class UniquenessCheck:
    """Duplicate guard on the UNIQUE constraint columns.

    The UNIQUE constraint `fact_measurements_unique_reading` on
    `(station_id, pollutant_id, measured_at, source)` already prevents
    duplicate inserts at write time. This check is a post-hoc safety
    net that catches any drift if the constraint is ever dropped or
    if data lands through a parallel path bypassing the constraint
    (e.g. a manual `COPY`).
    """

    name: str = "uniqueness_natural_key"
    dimension: str = "uniqueness"
    window_hours: int = 24
    strict_threshold: float = 0.0  # zero duplicates expected

    def run(self, conn: psycopg.Connection) -> QualityCheckResult:
        sql = """
            SELECT COUNT(*) - COUNT(DISTINCT
                       (station_id, pollutant_id, measured_at, source)
                   ) AS duplicate_count
            FROM fact_measurements
            WHERE measured_at >= now() - %s::interval
        """
        with conn.cursor() as cur:
            cur.execute(sql, (f"{self.window_hours} hours",))
            row = cur.fetchone()
        duplicate_count = float(row[0]) if row and row[0] is not None else 0.0
        status: CheckStatus = "pass" if duplicate_count <= self.strict_threshold else "fail"
        return QualityCheckResult(
            check_name=self.name,
            dimension=self.dimension,
            status=status,
            metric_value=duplicate_count,
            threshold=self.strict_threshold,
            message=f"duplicates={int(duplicate_count)} in last {self.window_hours}h",
            checked_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# Runner + persistence
# ---------------------------------------------------------------------------


@dataclass
class DataQualityRunner:
    """Orchestrate a suite of checks and persist results to `data_quality_runs`.

    Usage::

        runner = DataQualityRunner(suite_name="post_ingestion")
        runner.add_check(CompletenessCheck())
        runner.add_check(FreshnessCheck())
        results, overall = runner.run(conn)

    `overall` is the worst status across all checks (any `fail` →
    `fail`; otherwise any `warn` → `warn`; else `pass`).
    """

    suite_name: str
    checks: list[QualityCheck] = field(default_factory=list)

    def add_check(self, check: QualityCheck) -> None:
        """Append a check to the suite. Order is preserved in the audit row."""
        self.checks.append(check)

    def run(self, conn: psycopg.Connection) -> tuple[list[QualityCheckResult], CheckStatus]:
        """Execute every check, write one audit row, return results + overall.

        Args:
            conn: Open psycopg connection. The runner does not commit
                or close it — the caller owns the lifecycle. The audit
                INSERT is committed automatically by psycopg's
                context-managed cursor.
        """
        results: list[QualityCheckResult] = []
        for check in self.checks:
            try:
                result = check.run(conn)
            except Exception as exc:  # noqa: BLE001 — DQ failure must not crash caller
                _LOG.exception("DQ check %s raised", check.name)
                result = QualityCheckResult(
                    check_name=check.name,
                    dimension=check.dimension,
                    status="fail",
                    metric_value=0.0,
                    threshold=0.0,
                    message=f"check raised: {type(exc).__name__}: {exc}",
                    checked_at=datetime.now(UTC),
                )
            results.append(result)
            _LOG.info(
                "dq check: suite=%s name=%s status=%s metric=%.3f",
                self.suite_name,
                result.check_name,
                result.status,
                result.metric_value,
            )

        overall = _aggregate_status(results)
        self._persist_run(conn, results, overall)
        return results, overall

    def _persist_run(
        self,
        conn: psycopg.Connection,
        results: list[QualityCheckResult],
        overall: CheckStatus,
    ) -> None:
        """Insert one row into `data_quality_runs` with JSONB payload."""
        passed = sum(1 for r in results if r.status == "pass")
        failed = sum(1 for r in results if r.status == "fail")
        payload = {
            "overall_status": overall,
            "results": [r.to_dict() for r in results],
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_quality_runs
                    (suite_name, total_checks, passed, failed, payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (self.suite_name, len(results), passed, failed, json.dumps(payload)),
            )
        conn.commit()


def _aggregate_status(results: list[QualityCheckResult]) -> CheckStatus:
    """Worst-of aggregation: any `fail` → `fail`; any `warn` → `warn`; else `pass`."""
    if not results:
        return "pass"
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warn" for r in results):
        return "warn"
    return "pass"


def default_post_ingestion_suite() -> DataQualityRunner:
    """Build the standard 4-check post-ingestion suite.

    Used after every CSV batch load and every streaming micro-batch.
    Custom suites can be assembled via `DataQualityRunner` directly.
    """
    runner = DataQualityRunner(suite_name="post_ingestion")
    runner.add_check(CompletenessCheck())
    runner.add_check(FreshnessCheck())
    runner.add_check(ValidityCheck())
    runner.add_check(UniquenessCheck())
    return runner


# Re-export for legacy import compatibility — `data_quality.run_all_checks`
# was the public API in the H8 stub. We keep a thin wrapper for callers
# that haven't migrated to the runner-based API yet.
def run_all_checks(
    conn: psycopg.Connection,
    checks: list[QualityCheck] | None = None,
    suite_name: str = "ad_hoc",
) -> list[QualityCheckResult]:
    """Run all provided checks, persist, return results.

    If `checks` is None, the default post-ingestion suite is used.
    """
    runner = DataQualityRunner(suite_name=suite_name)
    if checks is None:
        runner = default_post_ingestion_suite()
        runner.suite_name = suite_name
    else:
        for check in checks:
            runner.add_check(check)
    results, _overall = runner.run(conn)
    return results


# Public type re-exports for `timedelta` consumers (rare; kept for
# downstream code that builds custom thresholds).
__all__ = [
    "CheckStatus",
    "CompletenessCheck",
    "DataQualityRunner",
    "FreshnessCheck",
    "QualityCheck",
    "QualityCheckResult",
    "UniquenessCheck",
    "ValidityCheck",
    "default_post_ingestion_suite",
    "run_all_checks",
    "timedelta",
]
