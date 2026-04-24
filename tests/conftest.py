"""Shared pytest fixtures.

Owner: data-quality-engineer agent.
Markers (defined in pyproject): slow, integration, e2e
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def test_settings() -> dict[str, str]:
    """Test-safe settings overrides. No real secrets."""
    return {
        "APP_ENV": "local",
        "DATABASE_URL": "postgresql://app:test@localhost:5432/air_quality_test",
        "KAFKA_BOOTSTRAP_SERVERS": "localhost:19092",
        "OPENWEATHER_API_KEY": "test_key_not_real",
        "LOG_LEVEL": "DEBUG",
    }


@pytest.fixture(scope="session")
def spark_session() -> Iterator[object]:
    """Session-scoped local SparkSession.

    Marked @pytest.mark.slow because cold-start adds ~5s.
    TODO (Hafta 6): spark-engineer fills in — lazy import to avoid pyspark
    dependency during unit runs.
    """
    pytest.skip("SparkSession fixture not yet implemented (Hafta 6)")
    yield None
