"""APScheduler entrypoint for the ingestion worker.

Runs as `python -m src.ingestion.main` (the Dockerfile.ingestion CMD).

Lifecycle:
1. Build a `KafkaProducerWrapper` (one per process — librdkafka background
   threads make a process-level singleton the cheapest correct choice).
2. Schedule `collect_and_publish` on a fixed interval driven by
   `settings.ingestion_interval_minutes`. The first tick fires immediately
   on boot so the freshly-deployed container does not sit idle for an
   hour before producing its first message.
3. Wait on a `stop_event`. SIGINT / SIGTERM (where supported) flip the
   event; the scheduler is shut down without waiting for in-flight jobs
   and the producer is flushed with a generous timeout so no message is
   silently dropped on container restart.

Each tick is one async fan-out via `collect_all_stations`; per-station
publish failures are logged and do not abort sibling stations. The
producer is shared across ticks and `flush()` is called at the end of
each tick to surface delivery callbacks promptly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config.settings import get_settings
from src.ingestion.api_collector import collect_all_stations
from src.ingestion.kafka_producer import KafkaProducerWrapper

_LOG = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _configure_logging(level: str | None = None) -> None:
    """Configure stdlib logging once at startup.

    `LOG_LEVEL` env var wins over the Settings default so operators can
    bump verbosity without redeploying. We never log secrets — the
    API-collector helpers already mask `appid=` and the producer wrapper
    never logs payload bytes.
    """
    resolved = (level or os.getenv("LOG_LEVEL") or get_settings().log_level).upper()
    logging.basicConfig(
        level=resolved,
        format=_LOG_FORMAT,
        # `force=True` lets us reconfigure if the host runtime already
        # installed a default handler (Docker base images sometimes do).
        force=True,
    )


async def collect_and_publish(producer: KafkaProducerWrapper) -> int:
    """Single scheduler tick: fetch every station then publish to Kafka.

    Returns:
        Number of readings successfully handed to the producer (i.e.
        passed serialization). librdkafka delivery confirmations arrive
        asynchronously via the wrapper's delivery callback.
    """
    readings = await collect_all_stations()
    total = len(readings)
    published = 0
    for reading in readings:
        try:
            producer.publish_reading(reading)
        except Exception:
            # Single-station publish failure must not poison sibling
            # stations. The wrapper already routes serialization issues
            # to the DLQ; this catches buffer-full / closed-producer and
            # any other surprises.
            _LOG.exception("publish failed for station=%s", reading.station.id)
            continue
        published += 1

    # Flush so delivery callbacks fire before we yield back to the loop.
    # 5 s is generous for ~10 stations on a healthy local broker.
    producer.flush(timeout_seconds=5.0)
    _LOG.info("tick complete: published=%d/%d", published, total)
    return published


async def run() -> None:
    """Main async entrypoint: schedule, wait for signal, shut down cleanly."""
    settings = get_settings()
    _configure_logging()
    _LOG.info(
        "starting aqi-ingestion: env=%s interval_minutes=%d bootstrap=%s",
        settings.app_env,
        settings.ingestion_interval_minutes,
        settings.kafka_bootstrap_servers,
    )

    producer = KafkaProducerWrapper()
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        collect_and_publish,
        trigger="interval",
        minutes=settings.ingestion_interval_minutes,
        args=[producer],
        # Fire one tick immediately so the freshly-booted container
        # produces data without waiting a full interval.
        next_run_time=datetime.now(tz=UTC),
        # Skip overlapping ticks rather than queueing them; if a tick
        # runs long, we want to keep cadence aligned to wall-clock hours.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        id="collect_and_publish",
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows event loops do not implement add_signal_handler for
        # SIGTERM (and sometimes SIGINT under ProactorEventLoop). On
        # Linux containers both are wired up. KeyboardInterrupt still
        # propagates via asyncio.run on Windows so Ctrl+C works.
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop_event.set)

    scheduler.start()
    try:
        await stop_event.wait()
    finally:
        _LOG.info("shutdown signal received; stopping scheduler and flushing producer")
        # `wait=False` cancels in-flight ticks rather than blocking
        # SIGTERM → SIGKILL window (Coolify gives ~10 s by default).
        scheduler.shutdown(wait=False)
        producer.close(timeout_seconds=10.0)
        _LOG.info("clean shutdown complete")


def main() -> int:
    """Sync wrapper around `run()` for `python -m src.ingestion.main`."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        # 128 + SIGINT(2) — POSIX convention for Ctrl+C exits.
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "collect_and_publish",
    "main",
    "run",
]
