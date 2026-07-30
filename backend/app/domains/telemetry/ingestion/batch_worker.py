"""
Batch worker xử lý các message telemetry.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Worker xử lý queue theo batch định kỳ:
- Mỗi flush_interval giây HOẶC khi queue có đủ batch_size messages
- Lấy tối đa batch_size messages từ queue
- Gọi telemetry.service.process_batch để xử lý
- Dừng worker khi database gặp lỗi trong phạm vi MVP

Luồng dữ liệu:
    asyncio.Queue → BatchWorker → telemetry.service.process_batch → Database

Lưu ý:
    - Task chỉ tồn tại trong RAM.
    - Shutdown cancel worker ngay; message còn trong queue được bỏ qua.
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
    Batch worker xử lý các message telemetry từ queue.

    Worker chạy định kỳ và xử lý messages theo batch:
    - Mỗi flush_interval giây HOẶC khi queue có đủ batch_size messages
    - Lấy tối đa batch_size messages từ queue
    - Gọi telemetry.service.process_batch để xử lý
    - Dừng worker khi database gặp lỗi trong phạm vi MVP

    Attributes:
        queue: Queue chứa telemetry envelope đã validate do ingestion process
            sở hữu.
        batch_size: Số message tối đa trong một database transaction.
        flush_interval: Số giây tối đa chờ từ message đầu tiên trước khi flush
            một batch chưa đầy.
        _running: Worker có tiếp tục nhận vòng lặp mới hay không.
        _task: Background task sở hữu việc consume queue, hoặc ``None`` trước
            khi khởi động.

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
        Khởi tạo batch worker.

        Args:
            queue: Queue cần consume. Dùng ingestion queue cấp module khi bỏ trống.
            batch_size: Số message tối đa trong mỗi database transaction.
            flush_interval: Khoảng thời gian tối đa của batch tính bằng giây.

        Side Effects:
            Lưu quyền sở hữu queue và khởi tạo state lifecycle của worker. Chưa
            tạo background task hoặc database session cho tới khi gọi ``start``.
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
        Khởi động task consume queue.

        Gọi method khi worker đang chạy sẽ không thay đổi state và chỉ ghi
        warning, qua đó ngăn hai task cùng consume một queue.

        Side Effects:
            Tạo một asyncio task xử lý queue trong background.
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
        Dừng batch worker.

        Phương thức cancel task ngay và không drain queue theo phạm vi MVP.

        Side Effects:
            Transaction đang chạy bị cancel và rollback; message chưa xử lý còn
            trong queue RAM sẽ mất khi process kết thúc.
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
        Vòng lặp chính của batch worker.

        Vòng lặp này sẽ chạy cho đến khi stop() được gọi.
        Mỗi iteration:
        1. Đợi message trong queue với timeout = flush_interval
        2. Nếu có message, lấy tối đa batch_size messages
        3. Xử lý batch

        Raises:
            Exception: Raise lại lỗi bất ngờ khi gom hoặc xử lý batch sau khi đánh
                dấu worker đã dừng. Hành vi này chủ ý kết thúc telemetry ingestion
                trong MVP không retry.
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
        Gom message cho tới khi batch đầy hoặc hết khoảng thời gian chờ.

        Khoảng thời gian bắt đầu khi message đầu tiên tới.

        Args:
            first_message: Message bắt đầu khoảng thời gian gom batch.

        Returns:
            Các message được gom cho một database transaction.

        Side Effects:
            Lấy các message trả về khỏi queue trong RAM.
        """
        messages = [first_message]
        # Dùng monotonic clock của event loop để việc hiệu chỉnh wall clock không
        # làm batch window ngắn hoặc dài hơn dự kiến.
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
        Xử lý một batch trong duy nhất một database transaction.

        Args:
            messages: Các envelope được xử lý atomic trong một database
                transaction.

        Raises:
            Exception: Raise lại lỗi database hoặc service sau khi log traceback.

        Side Effects:
            Commit khi thoát context thành công, rollback khi lỗi và xuất
            structured log.
        """
        try:
            # Worker là transaction boundary: service và repository được
            # execute/flush nhưng không bao giờ commit hoặc rollback.
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
