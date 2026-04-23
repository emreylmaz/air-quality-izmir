"""Spark batch processing — daily/weekly/monthly aggregations, rolling windows.

TODO (Hafta 6): Full implementation by `spark-engineer` agent.

Responsibilities:
- Read fact_measurements via JDBC
- Hourly → daily/weekly/monthly aggregations
- 7-day and 30-day moving averages
- Cross-station correlation matrix
- Write back to aggregation tables
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_daily_aggregation(date_from: str, date_to: str) -> None:
    """Run daily aggregation job for a date range.

    TODO: implement in Hafta 6.
    """
    raise NotImplementedError("Hafta 6: spark-engineer agent implements this")
