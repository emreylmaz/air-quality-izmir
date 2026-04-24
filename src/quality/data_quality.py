"""Data quality framework — check classes, runner, result model.

TODO (Hafta 12): Full implementation by `data-quality-engineer` agent.

Dimensions:
- Completeness: expected vs actual record count
- Freshness: latest ingestion age
- Validity: value range per pollutant
- Consistency: inter-station delta
- Uniqueness: duplicate detection
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

CheckStatus = Literal["pass", "warn", "fail"]


@dataclass
class QualityCheckResult:
    """Single quality check outcome."""

    check_name: str
    dimension: str
    status: CheckStatus
    metric_value: float
    threshold: float
    message: str
    checked_at: datetime


class QualityCheck(Protocol):
    """Interface for a single quality check."""

    name: str
    dimension: str

    def run(self) -> QualityCheckResult: ...


def run_all_checks(checks: list[QualityCheck]) -> list[QualityCheckResult]:
    """Execute all checks, log, persist results.

    TODO: implement in Hafta 12.
    """
    raise NotImplementedError("Hafta 12: data-quality-engineer agent implements this")
