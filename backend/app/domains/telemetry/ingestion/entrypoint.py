"""Minimal entrypoint for the process that receives telemetry over MQTT.

Feature code: F-A1 (Real-time vehicle telemetry ingestion)

The MVP process keeps the queue and tasks only in RAM. When it receives a
stop signal or a task fails, the process disconnects the consumer, cancels
the worker, and discards any remaining messages in the queue.
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


async def run() -> None:
    """Run the consumer and worker until a signal is received or a task fails.

    Side Effects:
        Creates the in-RAM queue, opens the MQTT connection, and closes
        resources when the process stops.

    Raises:
        RuntimeError: When the worker fails to create a background task or a
            task stops unexpectedly.
        Exception: Re-raises errors from the consumer or worker.
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
        await worker.start()
        if worker._task is None:
            raise RuntimeError("Batch worker task was not created during startup")

        tasks = [
            asyncio.create_task(
                consumer.start_consuming(), name="telemetry-mqtt-consumer"
            ),
            worker._task,
            asyncio.create_task(stop_event.wait(), name="telemetry-shutdown-signal"),
        ]

        logger.info(
            "Telemetry ingestion started",
            extra={"queue_size": settings.TELEMETRY_QUEUE_SIZE},
        )

        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
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
