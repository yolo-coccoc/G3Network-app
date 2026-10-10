"""Worker that stores status reports from the queue, one message per transaction.

Counterpart of the telemetry message worker (DEV-03): each envelope taken off
the queue is processed in its own transaction, which the worker commits when
``telematics.service.record_status_report`` succeeds. A database error rolls
back that message, is logged with its traceback and stops the worker (the
process then exits non-zero), as for telemetry.
"""

import asyncio
import logging

import app.domains.telematics.service as telematics_service
from app.domains.telematics.schemas import TelematicStatusEnvelope
from app.libs.db.session import async_session_factory

logger = logging.getLogger(__name__)


class StatusReportWorker:
    """Consumes and persists each status envelope.

    Attributes:
        queue: Queue holding envelopes validated by the MQTT consumer.
        _running: Whether the consume loop should keep accepting messages.
        _task: Background task that owns the consume loop, or ``None`` before
            the worker is started.
    """

    def __init__(self, queue: asyncio.Queue[TelematicStatusEnvelope]) -> None:
        """Take ownership of the queue the consumer fills.

        Args:
            queue: The same instance the entrypoint gives the consumer.
        """
        self.queue = queue
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> asyncio.Task[None]:
        """Start the background task that processes the queue.

        Returns:
            The task that owns the consume loop; it ends with the loop's
            exception if processing a message fails.
        """
        if self._running and self._task is not None:
            logger.warning("Status-report worker is already running")
            return self._task
        self._running = True
        self._task = asyncio.create_task(
            self._run_loop(), name="telematics-status-report-worker"
        )
        logger.info("Status-report worker started")
        return self._task

    async def stop(self) -> None:
        """Cancel the task now; messages left in the in-RAM queue are dropped."""
        if self._task is None:
            return
        logger.info("Stopping status-report worker")
        self._running = False
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        logger.info("Status-report worker stopped")

    async def _run_loop(self) -> None:
        """Take and process envelopes one at a time until the worker stops.

        Raises:
            Exception: Re-raises a processing error after marking the worker
                stopped, so the entrypoint can end the process.
        """
        while self._running:
            envelope = await self.queue.get()
            try:
                await self._process_message(envelope)
            except Exception:
                self._running = False
                raise

    async def _process_message(self, envelope: TelematicStatusEnvelope) -> None:
        """Process one envelope within its own transaction.

        Args:
            envelope: Message already validated by the consumer.

        Raises:
            Exception: Re-raises the database error after logging the
                traceback (a process boundary, see the module docstring).

        Side Effects:
            Commits when the service succeeds, rolls back otherwise; logs one
            INFO summary line per message.
        """
        try:
            async with async_session_factory.begin() as db_session:
                process_result = await telematics_service.record_status_report(
                    db_session, envelope
                )
            logger.info(
                "Status message processed",
                extra={
                    "telematic_serial": envelope.telematic_serial,
                    "processed": process_result["processed"],
                    "skipped": process_result["skipped"],
                    "errors": process_result["errors"],
                },
            )
        except Exception:
            logger.exception(
                "Status message processing failed; stopping worker",
                extra={"telematic_serial": envelope.telematic_serial},
            )
            raise
