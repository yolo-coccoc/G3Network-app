"""
Batch worker for telemetry messages.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Worker xử lý queue theo batch định kỳ:
- Mỗi flush_interval giây HOẶC khi queue có đủ batch_size messages
- Lấy tối đa batch_size messages từ queue
- Gọi telemetry.service.process_batch để xử lý
- Retry khi gặp lỗi (tối đa 3 lần)

Luồng dữ liệu:
    asyncio.Queue → BatchWorker → telemetry.service.process_batch → Database

Lưu ý:
    - Dùng asyncio.wait_for để timeout
    - Graceful shutdown: xử lý nốt batch hiện tại
    - Metrics để monitor performance
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.libs.common.config import settings
from app.domains.telemetry import service as telemetry_service
from app.domains.telemetry.ingestion.mqtt_consumer import message_queue

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from app.domains.telemetry.schemas import TelemetryMessage

logger = logging.getLogger(__name__)


class BatchMetrics:
    """Simple metrics counter for batch worker."""

    def __init__(self) -> None:
        """Initialize metrics counters."""
        self.batches_processed_total = 0
        self.messages_processed_total = 0
        self.batch_processing_time_seconds = 0.0
        self.batch_errors_total = 0

    def record_batch(self, message_count: int, processing_time: float) -> None:
        """Record a successful batch."""
        self.batches_processed_total += 1
        self.messages_processed_total += message_count
        self.batch_processing_time_seconds += processing_time

    def record_error(self) -> None:
        """Record a batch error."""
        self.batch_errors_total += 1


batch_metrics = BatchMetrics()


class BatchWorker:
    """
    Batch worker xử lý telemetry messages từ queue.
    
    Worker chạy định kỳ và xử lý messages theo batch:
    - Mỗi flush_interval giây HOẶC khi queue có đủ batch_size messages
    - Lấy tối đa batch_size messages từ queue
    - Gọi telemetry.service.process_batch để xử lý
    - Retry khi gặp lỗi (tối đa max_retries lần với exponential backoff)
    
    Attributes:
        queue: asyncio.Queue chứa TelemetryMessage
        batch_size: Số message tối đa trong 1 batch (default 100)
        flush_interval: Khoảng thời gian flush batch (giây, default 30.0)
        max_retries: Số lần retry khi gặp lỗi (default 3)
        retry_base_delay: Thời gian chờ cơ bản giữa các retry (giây, default 1.0)
        
    Example:
        >>> worker = BatchWorker(
        ...     queue=message_queue,
        ...     batch_size=100,
        ...     flush_interval=30.0,
        ... )
        >>> await worker.start()
        >>> # ... running ...
        >>> await worker.stop()
    """

    def __init__(
        self,
        queue: asyncio.Queue["TelemetryMessage"] | None = None,
        batch_size: int = 100,
        flush_interval: float = 30.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        """
        Khởi tạo batch worker.
        
        Args:
            queue: asyncio.Queue chứa TelemetryMessage (default module-level queue)
            batch_size: Số message tối đa trong 1 batch
            flush_interval: Khoảng thời gian flush batch (giây)
            max_retries: Số lần retry khi gặp lỗi
            retry_base_delay: Thời gian chờ cơ bản giữa các retry (giây)
        """
        self.queue = queue or message_queue
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

        self._running = False
        self._task: asyncio.Task | None = None
        self._engine = create_async_engine(settings.DATABASE_URL, echo=False)
        self._session_factory = sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    async def start(self) -> None:
        """Bắt đầu batch worker."""
        if self._running:
            logger.warning("Batch worker is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Batch worker started",
            extra={
                "batch_size": self.batch_size,
                "flush_interval": self.flush_interval,
            }
        )

    async def stop(self, timeout: float = 60.0) -> None:
        """
        Dừng batch worker.
        
        Phương thức này được gọi khi graceful shutdown.
        Worker sẽ xử lý nốt batch hiện tại trước khi dừng.
        
        Args:
            timeout: Thời gian tối đa đợi worker dừng (giây)
        """
        if not self._running:
            return

        logger.info("Stopping batch worker")
        self._running = False

        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "Batch worker did not stop gracefully, cancelling task"
                )
                self._task.cancel()

        await self._engine.dispose()
        logger.info("Batch worker stopped")

    async def _run_loop(self) -> None:
        """
        Vòng lặp chính của batch worker.
        
        Vòng lặp này sẽ chạy cho đến khi stop() được gọi.
        Mỗi iteration:
        1. Đợi message trong queue với timeout = flush_interval
        2. Nếu có message, lấy tối đa batch_size messages
        3. Xử lý batch
        """
        logger.info("Batch worker loop started")

        while self._running:
            try:
                # Đợi message đầu tiên với timeout
                first_message = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=self.flush_interval
                )

                # Thu thập batch
                messages = [first_message]
                while len(messages) < self.batch_size:
                    try:
                        msg = self.queue.get_nowait()
                        messages.append(msg)
                    except asyncio.QueueEmpty:
                        break

                # Xử lý batch
                await self._process_batch_with_retry(messages)

            except asyncio.TimeoutError:
                # Timeout: không có message trong flush_interval
                # Kiểm tra nếu queue không rỗng thì flush
                if self.queue.qsize() > 0:
                    messages = []
                    while len(messages) < self.batch_size:
                        try:
                            msg = self.queue.get_nowait()
                            messages.append(msg)
                        except asyncio.QueueEmpty:
                            break

                    if messages:
                        await self._process_batch_with_retry(messages)

            except Exception as e:
                logger.error(f"Unexpected error in batch worker loop: {e}")
                await asyncio.sleep(1.0)

    async def _process_batch_with_retry(
        self, messages: "Sequence[TelemetryMessage]"
    ) -> None:
        """
        Xử lý batch với retry logic.
        
        Args:
            messages: Danh sách TelemetryMessage cần xử lý
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                start_time = datetime.now(timezone.utc)

                async with self._session_factory() as db:
                    result = await telemetry_service.process_batch(db, messages)

                processing_time = (datetime.now(timezone.utc) - start_time).total_seconds()
                batch_metrics.record_batch(len(messages), processing_time)

                logger.info(
                    "Batch processed successfully",
                    extra={
                        "batch_size": len(messages),
                        "processed": result["processed"],
                        "skipped": result["skipped"],
                        "errors": result["errors"],
                        "processing_time_ms": round(processing_time * 1000, 2),
                        "attempt": attempt,
                    }
                )
                return

            except Exception as e:
                batch_metrics.record_error()
                logger.error(
                    f"Batch processing failed (attempt {attempt}/{self.max_retries}): {e}",
                    extra={
                        "batch_size": len(messages),
                        "error": str(e),
                    }
                )

                if attempt < self.max_retries:
                    # Exponential backoff
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    # Max retries reached: log and drop batch
                    logger.error(
                        "Batch processing failed after max retries, dropping batch",
                        extra={
                            "batch_size": len(messages),
                            "max_retries": self.max_retries,
                        }
                    )
                    # TODO: Implement dead-letter queue (file or DB table)
                    # For MVP, we just log and drop
