"""Entrypoint of the process that receives T-Box status reports over MQTT (DEV-03).

Run with ``make telematics-status-dev``. Same lifecycle as the telemetry
ingestion process (RAM queue, a stop signal exits 0, a consumer or worker that
ends on its own exits 1), but with its own MQTT client id so the two
processes never evict each other's session.
"""

import asyncio
import logging
import signal

from app.domains.telematics.ingestion.message_worker import StatusReportWorker
from app.domains.telematics.ingestion.mqtt_consumer import StatusReportConsumer
from app.domains.telematics.schemas import TelematicStatusEnvelope
from app.libs.common.config import settings
from app.libs.common.logging import configure_logging
from app.libs.db.session import close_db

logger = logging.getLogger(__name__)


def raise_unless_stopped_by_signal(
    done_tasks: set[asyncio.Task[object]], signal_task: asyncio.Task[object]
) -> None:
    """Decide whether the run ended cleanly, given the tasks that finished.

    Only the shutdown signal is a clean end. A consumer or worker task that
    finished first means ingestion stopped by itself: its exception is
    re-raised, and a task that returned without one still counts as a failure.

    Args:
        done_tasks: The tasks `asyncio.wait` reported as done.
        signal_task: The task that completes when SIGINT/SIGTERM arrives.

    Raises:
        Exception: The failed task's own exception, or `RuntimeError` when
            it ended without one.
    """
    if signal_task in done_tasks:
        return
    stopped_task = next(iter(done_tasks))
    error = stopped_task.exception()
    if error is not None:
        raise error
    raise RuntimeError(f"Task '{stopped_task.get_name()}' stopped unexpectedly")


async def run() -> None:
    """Run the consumer and worker until a signal arrives or a task fails.

    Raises:
        Exception: Errors from connecting the consumer or starting the
            worker, and - after cleanup - the error of a task that stopped
            before any shutdown signal, so ``main()`` exits with code 1.

    Side Effects:
        Creates the in-RAM queue, opens the MQTT connection and closes the
        resources when the process stops.
    """
    configure_logging()
    queue: asyncio.Queue[TelematicStatusEnvelope] = asyncio.Queue(
        maxsize=settings.TELEMATICS_STATUS_QUEUE_SIZE
    )
    consumer = StatusReportConsumer(queue=queue)
    worker = StatusReportWorker(queue=queue)

    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        event_loop.add_signal_handler(handled_signal, stop_event.set)

    try:
        await consumer.connect()
        worker_task = await worker.start()
        signal_task: asyncio.Task[object] = asyncio.create_task(
            stop_event.wait(), name="telematics-status-shutdown-signal"
        )
        tasks: list[asyncio.Task[object]] = [
            asyncio.create_task(
                consumer.start_consuming(), name="telematics-status-mqtt-consumer"
            ),
            worker_task,
            signal_task,
        ]
        logger.info(
            "Status-report ingestion started",
            extra={"queue_size": settings.TELEMATICS_STATUS_QUEUE_SIZE},
        )
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise_unless_stopped_by_signal(done, signal_task)
    finally:
        await consumer.disconnect()
        await worker.stop()
        await close_db()
        for handled_signal in handled_signals:
            event_loop.remove_signal_handler(handled_signal)
        logger.info("Status-report ingestion stopped")


def main() -> None:
    """Run the event loop and exit with an error code on failure.

    Raises:
        SystemExit: With code 1 when the process fails (fail-fast).
    """
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Status-report ingestion interrupted during startup")
    except Exception:
        logger.exception("Status-report ingestion process exited with failure")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
