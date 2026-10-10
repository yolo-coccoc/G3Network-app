"""Entrypoint process for the OCPP (2.0.1 and 1.6J) WebSocket gateway."""

import asyncio
import logging
import signal

# The gateway writes rows that reference other domains' tables (a command's
# requesting user), so every model must be registered in this process.
import app.libs.db.model_registry  # noqa: F401
from app.domains.charging_stations.ocpp.ocpp_server import run_server
from app.libs.common.logging import configure_logging
from app.libs.db.session import close_db

logger = logging.getLogger(__name__)


async def run() -> None:
    """Start the gateway and shut down gracefully on SIGINT/SIGTERM.

    Side Effects:
        Binds the WebSocket listener, registers signal handlers, and closes
        the shared database engine after the gateway stops.
    """
    configure_logging()
    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    # The signal only wakes up the coroutine waiting on the event; run_server
    # itself owns closing the listener so every connection and socket shuts
    # down in an orderly way.
    for handled_signal in handled_signals:
        event_loop.add_signal_handler(handled_signal, stop_event.set)

    try:
        await run_server(stop_event)
    finally:
        # The database engine is a shared resource of the process, so it is
        # closed only after the gateway has stopped and no handshake still
        # needs to query a station.
        await close_db()
        for handled_signal in handled_signals:
            event_loop.remove_signal_handler(handled_signal)
        logger.info("OCPP gateway process stopped")


def main() -> None:
    """Run the event loop for the OCPP gateway and return an error code on failure."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("OCPP gateway interrupted during startup")
    except Exception:
        logger.exception("OCPP gateway process exited with failure")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
