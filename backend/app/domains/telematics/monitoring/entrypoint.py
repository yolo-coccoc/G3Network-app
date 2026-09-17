"""Entrypoint process for the telematics device-health monitor (F-J1, F-J3 partial)."""

import asyncio
import logging
import signal

from app.domains.telematics.monitoring.device_health_monitor import run_monitor
from app.libs.common.logging import configure_logging
from app.libs.db.session import close_db

logger = logging.getLogger(__name__)


async def run() -> None:
    """Start the monitor and shut down gracefully on SIGINT/SIGTERM.

    Side Effects:
        Registers signal handlers and closes the shared database engine
        after the monitor stops.
    """
    configure_logging()
    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        event_loop.add_signal_handler(handled_signal, stop_event.set)

    try:
        await run_monitor(stop_event)
    finally:
        await close_db()
        for handled_signal in handled_signals:
            event_loop.remove_signal_handler(handled_signal)
        logger.info("Telematics device health monitor stopped")


def main() -> None:
    """Run the event loop for the device health monitor.

    Raises:
        SystemExit: With code 1 if the monitor exits due to an unexpected
            error (fail-fast, matching every other entrypoint's policy).
    """
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Telematics device health monitor interrupted during startup")
    except Exception:
        logger.exception("Telematics device health monitor exited with failure")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
