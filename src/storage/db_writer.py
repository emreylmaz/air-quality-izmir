"""PostgreSQL writer helpers — psycopg3 based.

TODO (Hafta 4): Full implementation by `database-architect` agent.

Responsibilities:
- Connection pooling (psycopg_pool)
- Batch insert for fact_measurements (execute_values / copy)
- Dimension upsert helpers
- Transaction context manager
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


@contextmanager
def get_connection(dsn: str) -> Any:  # noqa: ANN401
    """Yield a psycopg connection. TODO: swap for psycopg_pool."""
    # TODO: implement in Hafta 4
    raise NotImplementedError("Hafta 4: database-architect agent implements this")
    yield None


def batch_insert_measurements(rows: list[dict[str, Any]]) -> int:
    """Batch insert measurement rows. Returns inserted count.

    TODO: implement in Hafta 4.
    """
    raise NotImplementedError("Hafta 4")
