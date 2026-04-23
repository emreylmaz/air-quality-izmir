"""Historical CSV loader — Çevre Bakanlığı open data → PostgreSQL.

TODO (Hafta 3): Full implementation by `data-engineer` agent.

Responsibilities:
- Parse historical CSV files (encoding, delimiter)
- Clean: forward-fill ≤ 3h gaps, IQR outlier filter, drop negative
- Unit standardization → µg/m³
- Batch insert to fact_measurements (psycopg3 execute_batch)
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_csv(path: Path, station_id: int) -> int:
    """Load and clean a historical CSV, insert into PostgreSQL.

    Returns:
        Number of rows successfully inserted.

    TODO: implement in Hafta 3.
    """
    raise NotImplementedError("Hafta 3: data-engineer agent implements this")
