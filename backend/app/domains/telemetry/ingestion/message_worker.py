"""Worker that processes telemetry messages from the queue one at a time.

Feature code: AD-02 (Receive real-time data)

This worker is the active flow of the telemetry ingestion MVP: each time it
takes a ``TelemetryEnvelope`` off the queue, the worker opens a transaction,
calls the service to process the message, and commits as soon as the
operation succeeds. The old batch implementation lives in
``batch_worker.py`` and is not part of the current process lifecycle.
"""

import asyncio
import logging

import app.domains.telemetry.service as telemetry_service
from app.domains.telemetry.ingestion.mqtt_consumer import message_queue
from app.domains.telemetry.schemas import TelemetryEnvelope
from app.libs.db.session import async_session_factory

logger = logging.getLogger(__name__)


class MessageWorker:
    """Worker that consumes and persists each telemetry envelope.

    Attributes:
        queue: Queue holding envelopes validated by the MQTT consumer.
        _running: Whether the consume loop should keep accepting messages.
        _task: Background task that owns the consume loop, or ``None`` before
            the worker is started.
    """

    def __init__(self, queue: asyncio.Queue[TelemetryEnvelope] | None = None) -> None:
        """Initialize the worker and take ownership of the passed-in queue.

        Args:
            queue: Queue to consume from. Uses the default module-level queue
                when left empty.

        Side Effects:
            Initializes in-memory lifecycle state; no task or database
            session is created until ``start`` is called.
        """
        self.queue = message_queue if queue is None else queue
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background task that processes each message in the queue.

        Calling this again while the worker is running only logs a warning
        and does not create a second task.

        Side Effects:
            Creates an asyncio task that owns the consume loop.
        """
        if self._running:
            logger.warning("Message worker is already running")
            return

        self._running = True
        self._task = asyncio.create_task(
            self._run_loop(),
            name="telemetry-message-worker",
        )
        logger.info("Message worker started")

    async def stop(self) -> None:
        """Stop the worker immediately and discard remaining messages in the in-RAM queue.

        Side Effects:
            Cancels the background task. If a transaction is running, the
            session context manager rolls back; messages not yet taken off
            the queue are not drained, per MVP policy.
        """
        if self._task is None:
            return

        logger.info("Stopping message worker")
        self._running = False
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        logger.info("Message worker stopped")

    async def _run_loop(self) -> None:
        """Take and process messages sequentially, one at a time, until the worker stops.

        Raises:
            Exception: Re-raises unexpected or database errors after marking
                the worker as stopped, so the entrypoint can end the process
                per MVP policy.

        Side Effects:
            Takes each envelope off the queue and invokes the transaction
            boundary for each message; remaining messages are not drained
            when the worker stops.
        """
        logger.info("Message worker loop started")

        while self._running:
            message = await self.queue.get()
            try:
                await self._process_message(message)
            except Exception:
                self._running = False
                raise

    async def _process_message(self, envelope: TelemetryEnvelope) -> None:
        """Process one envelope within its own independent transaction.

        Args:
            envelope: Message already validated by the consumer.

        Raises:
            Exception: Re-raises service/database errors after logging the
                traceback.

        Side Effects:
            Commits the transaction if the service succeeds; rolls back on
            exception and emits a summary structured log for the message.
        """
        try:
            # The worker is the transaction boundary; the service/repository
            # only execute, never commit or roll back on their own.
            async with async_session_factory.begin() as db:
                result = await telemetry_service.process_message(db, envelope)

            logger.info(
                "Telemetry message processed",
                extra={
                    "message_uuid": str(envelope.message.message_uuid),
                    "processed": result["processed"],
                    "skipped": result["skipped"],
                    "errors": result["errors"],
                },
            )
        except Exception:
            logger.exception(
                "Telemetry message processing failed; stopping worker",
                extra={"message_uuid": str(envelope.message.message_uuid)},
            )
            raise
