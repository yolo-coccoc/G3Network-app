"""Entrypoint process for the driving-session auto-end worker (DR-07).

Run on the host with ``make driving-sessions-autoend-dev``
(``python -m app.domains.drivers.monitoring.entrypoint``). It owns the
process lifecycle only: logging setup, SIGINT/SIGTERM handling and closing
the shared database engine; the periodic sweep itself lives in
``auto_end_worker``. A failed tick stops the process (exit code 1)
rather than being retried.
"""

import asyncio
import logging
import signal

from app.api.startup import register_all_hooks
from app.domains.drivers.monitoring.auto_end_worker import run_worker
from app.libs.common.logging import configure_logging
from app.libs.db.session import close_db

logger = logging.getLogger(__name__)


async def run() -> None:
    """Start the worker and shut down gracefully on SIGINT/SIGTERM.

    Raises:
        Exception: Whatever a worker tick raised, propagated unchanged
            after cleanup so ``main`` can exit with a failure code.

    Side Effects:
        Configures logging, registers SIGINT/SIGTERM handlers that set the
        worker's stop event, and - whether the worker stops cleanly or
        fails - closes the shared database engine and removes the handlers.
    """
    configure_logging()
    # Every cross-domain hook, the same in every process (CV-21).
    register_all_hooks()
    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        event_loop.add_signal_handler(handled_signal, stop_event.set)

    try:
        await run_worker(stop_event)
    finally:
        await close_db()
        for handled_signal in handled_signals:
            event_loop.remove_signal_handler(handled_signal)
        logger.info("Driving-session auto-end worker stopped")


def main() -> None:
    """Run the event loop for the auto-end worker.

    A ``KeyboardInterrupt`` before the signal handlers are installed is
    logged and treated as a normal stop.

    Raises:
        SystemExit: With code 1 if the worker exits due to an unexpected
            error (fail-fast, matching every other entrypoint's policy).
    """
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Driving-session auto-end worker interrupted during startup")
    except Exception:
        logger.exception("Driving-session auto-end worker exited with failure")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
