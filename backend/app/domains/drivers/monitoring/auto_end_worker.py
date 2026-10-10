"""Periodic sweep that ends driving sessions whose truck stopped moving (DR-07).

Like the telematics device-health monitor, this is a timer-driven process: "the
truck has not moved for a while" has no message to react to. Each tick opens one
transaction and calls ``drivers.service.end_idle_driving_sessions``, which holds
the rule (the organization's auto-end time, the truck's last movement from the
telemetry service). A session ended this way gets the end cause `AUTO_ENDED`
and closes its running trip like a check-out does.
"""

import asyncio
import logging

import app.domains.drivers.service as driver_service
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.db.session import async_session_factory

logger = logging.getLogger(__name__)


async def run_worker(stop_event: asyncio.Event) -> None:
    """Run auto-end ticks on an interval until told to stop.

    Args:
        stop_event: Event set by the entrypoint on SIGINT/SIGTERM.

    Side Effects:
        Sleeps via a cancellable wait (a shutdown signal during the wait is
        honored at once) and runs one tick per elapsed interval. A signal
        arriving mid-tick is honored once that tick finishes. An exception
        from a tick propagates (fail-fast, like every other entrypoint).
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.DRIVERS_AUTO_END_CHECK_INTERVAL_SECONDS,
            )
            return  # stop_event was set - exit without another tick
        except TimeoutError:
            pass  # interval elapsed - time to sweep
        await run_auto_end_tick()


async def run_auto_end_tick() -> None:
    """Open one transaction for a sweep and run it.

    Side Effects:
        Opens one atomic transaction for the whole sweep, calls
        ``end_idle_driving_sessions`` and logs the metrics: ``checked``
        (open sessions), ``ended`` (auto-ended) and ``skipped`` (no telemetry,
        or none after the last movement).
    """
    async with async_session_factory.begin() as db_session:
        result = await driver_service.end_idle_driving_sessions(
            db_session, now=utc_now()
        )
    logger.info(
        "driving session auto-end tick completed",
        extra={
            "checked": result.checked,
            "ended": result.ended,
            "skipped": result.skipped,
        },
    )
