"""
Batch worker that processes telemetry messages.

Feature code: F-A1 (Real-time vehicle telemetry ingestion)

The worker processes the queue in periodic batches:
- Every flush_interval seconds OR when the queue has enough batch_size messages
- Takes at most batch_size messages from the queue
- Calls telemetry.service.process_batch to process them
- Stops the worker when the database errors, within MVP scope

Data flow:
    asyncio.Queue → BatchWorker → telemetry.service.process_batch → Database

Note:
    - The task exists only in RAM.
    - Shutdown cancels the worker immediately; messages still in the queue
      are discarded.
"""

import asyncio
import logging
from collections.abc import Sequence

import app.domains.telemetry.service as telemetry_service
from app.domains.telemetry.ingestion.mqtt_consumer import message_queue
from app.domains.telemetry.schemas import TelemetryEnvelope
from app.libs.common.config import settings
from app.libs.db.session import async_session_factory

logger = logging.getLogger(__name__)


class BatchWorker:
    """
    Batch worker that processes telemetry messages from the queue.

    The worker runs periodically and processes messages in batches:
    - Every flush_interval seconds OR when the queue has enough batch_size messages
    - Takes at most batch_size messages from the queue
    - Calls telemetry.service.process_batch to process them
    - Stops the worker when the database errors, within MVP scope

    Attributes:
        queue: Queue holding validated telemetry envelopes, owned by the
            ingestion process.
        batch_size: Maximum number of messages in one database transaction.
        flush_interval: Maximum number of seconds to wait from the first
            message before flushing a batch that isn't full.
        _running: Whether the worker should keep accepting new loop iterations.
        _task: Background task that owns consuming the queue, or ``None``
            before startup.

    Example:
        >>> worker = BatchWorker(
        ...     queue=message_queue,
        ... )
        >>> await worker.start()
        >>> # ... running ...
        >>> await worker.stop()
    """

    def __init__(
        self,
        queue: asyncio.Queue[TelemetryEnvelope] | None = None,
        batch_size: int | None = None,
        flush_interval: float | None = None,
    ) -> None:
        """
        Initialize the batch worker.

        Args:
            queue: Queue to consume from. Uses the module-level ingestion
                queue when left empty.
            batch_size: Maximum number of messages in each database transaction.
            flush_interval: Maximum batch time window in seconds.

        Side Effects:
            Stores queue ownership and initializes the worker's lifecycle
            state. No background task or database session is created until
            ``start`` is called.
        """
        self.queue = message_queue if queue is None else queue
        self.batch_size = (
            settings.TELEMETRY_BATCH_SIZE if batch_size is None else batch_size
        )
        self.flush_interval = (
            settings.TELEMETRY_FLUSH_INTERVAL
            if flush_interval is None
            else flush_interval
        )

        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """
        Start the task that consumes the queue.

        Calling this method while the worker is already running does not
        change state and only logs a warning, preventing two tasks from
        consuming the same queue.

        Side Effects:
            Creates an asyncio task that processes the queue in the background.
        """
        if self._running:
            logger.warning("Batch worker is already running")
            return

        self._running = True
        self._task = asyncio.create_task(
            self._run_loop(),
            name="telemetry-batch-worker",
        )
        logger.info(
            "Batch worker started",
            extra={
                "batch_size": self.batch_size,
                "flush_interval": self.flush_interval,
            },
        )

    async def stop(self) -> None:
        """
        Stop the batch worker.

        The method cancels the task immediately and does not drain the
        queue, within MVP scope.

        Side Effects:
            The running transaction is cancelled and rolled back; unprocessed
            messages still in the in-RAM queue are lost when the process ends.
        """
        if self._task is None:
            return

        logger.info("Stopping batch worker")
        self._running = False
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        logger.info("Batch worker stopped")

    async def _run_loop(self) -> None:
        """
        Main loop of the batch worker.

        This loop runs until stop() is called.
        Each iteration:
        1. Wait for a message on the queue with timeout = flush_interval
        2. If a message arrives, take at most batch_size messages
        3. Process the batch

        Raises:
            Exception: Re-raises unexpected errors encountered while
                collecting or processing a batch, after marking the worker
                as stopped. This behavior intentionally ends telemetry
                ingestion within the no-retry MVP.
        """
        logger.info("Batch worker loop started")

        while self._running:
            try:
                first_message = await asyncio.wait_for(
                    self.queue.get(), timeout=self.flush_interval
                )
            except asyncio.TimeoutError:
                continue

            try:
                messages = await self._collect_batch(first_message)
                await self._process_batch(messages)
            except Exception:
                self._running = False
                raise

    async def _collect_batch(
        self, first_message: TelemetryEnvelope
    ) -> list[TelemetryEnvelope]:
        """
        Collect messages until the batch is full or the wait window elapses.

        The time window begins when the first message arrives.

        Args:
            first_message: The message that starts the batch collection window.

        Returns:
            The messages collected for one database transaction.

        Side Effects:
            Removes the returned messages from the in-RAM queue.
        """
        messages = [first_message]
        # Use the event loop's monotonic clock so that wall clock adjustments
        # don't make the batch window shorter or longer than intended.
        deadline = asyncio.get_running_loop().time() + self.flush_interval

        while len(messages) < self.batch_size:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break

            try:
                message = await asyncio.wait_for(self.queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            messages.append(message)

        return messages

    async def _process_batch(self, messages: Sequence[TelemetryEnvelope]) -> None:
        """
        Process one batch within a single database transaction.

        Args:
            messages: The envelopes processed atomically within one database
                transaction.

        Raises:
            Exception: Re-raises database or service errors after logging
                the traceback.

        Side Effects:
            Commits when the context exits successfully, rolls back on
            error, and emits a structured log.
        """
        try:
            # The worker is the transaction boundary: the service and
            # repository execute/flush but never commit or roll back.
            async with async_session_factory.begin() as db:
                result = await telemetry_service.process_batch(db, messages)

            logger.info(
                "Batch processed successfully",
                extra={
                    "batch_size": len(messages),
                    "processed": result["processed"],
                    "skipped": result["skipped"],
                    "errors": result["errors"],
                },
            )
        except Exception:
            logger.exception(
                "Batch processing failed; stopping worker",
                extra={"batch_size": len(messages)},
            )
            raise
