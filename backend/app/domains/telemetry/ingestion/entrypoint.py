"""Minimal entrypoint for the process that receives telemetry over MQTT.

Feature code: F-A1 (Real-time vehicle telemetry ingestion)

The MVP process keeps the queue and tasks only in RAM. When it receives a
stop signal or a task fails, the process disconnects the consumer, cancels
the worker, and discards any remaining messages in the queue. A stop signal
exits with code 0; a consumer or worker that ends on its own exits with
code 1, so a process supervisor sees the failure and can restart it.
"""

import asyncio
import logging
import signal

from app.domains.telemetry.ingestion.message_worker import MessageWorker
from app.domains.telemetry.ingestion.mqtt_consumer import MQTTConsumer
from app.domains.telemetry.schemas import TelemetryEnvelope
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
    re-raised, and a task that returned without one (e.g. the consumer after
    logging a lost broker connection) still counts as a failure.

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
    """Run the consumer and worker until a signal is received or a task fails.

    Whichever finishes first - the consumer task, the worker task (which
    ends with its exception when a message fails, per MVP policy), or the
    shutdown signal - ends the run; the others are cancelled.

    Side Effects:
        Creates the in-RAM queue, opens the MQTT connection, and closes
        resources when the process stops.

    Raises:
        Exception: Errors from connecting the consumer or starting the
            worker, and - after cleanup - the error of a consumer or worker
            task that stopped before any shutdown signal (`RuntimeError` if
            it stopped without one), so `main()` exits with code 1.
    """
    configure_logging()

    queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue(
        maxsize=settings.TELEMETRY_QUEUE_SIZE
    )
    consumer = MQTTConsumer(queue=queue)
    worker = MessageWorker(queue=queue)

    stop_event = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        event_loop.add_signal_handler(handled_signal, stop_event.set)

    try:
        await consumer.connect()
        worker_task = await worker.start()

        signal_task: asyncio.Task[object] = asyncio.create_task(
            stop_event.wait(), name="telemetry-shutdown-signal"
        )
        tasks: list[asyncio.Task[object]] = [
            asyncio.create_task(
                consumer.start_consuming(), name="telemetry-mqtt-consumer"
            ),
            worker_task,
            signal_task,
        ]

        logger.info(
            "Telemetry ingestion started",
            extra={"queue_size": settings.TELEMETRY_QUEUE_SIZE},
        )

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        # Let the cancellations finish before cleanup touches the resources.
        await asyncio.gather(*pending, return_exceptions=True)
        raise_unless_stopped_by_signal(done, signal_task)
    finally:
        await consumer.disconnect()
        await worker.stop()
        await close_db()
        for handled_signal in handled_signals:
            event_loop.remove_signal_handler(handled_signal)
        logger.info("Telemetry ingestion stopped")


def main() -> None:
    """Run the event loop for telemetry ingestion and exit with an error code when needed.

    Side Effects:
        Creates the event loop for the whole process and logs when the
        process fails.
    """
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Telemetry ingestion interrupted during startup")
    except Exception:
        logger.exception("Telemetry ingestion process exited with failure")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
