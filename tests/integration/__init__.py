"""Integration tests — real Docker services (PostgreSQL, Kafka, ...).

Mark each test with ``@pytest.mark.integration``. The default Make target
filters them out (`make test` runs only fast unit tests); the dedicated
``make test-integration`` target opts in.

Sub-packages live alongside this module by feature area; today only the
schema apply (T7) and load performance (T8) suites exist.
"""
