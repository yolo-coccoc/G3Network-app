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
    - Dùng asyncio.wait_for để timeout
    - Graceful shutdown: xử lý nốt batch hiện tại
    - Metrics để monitor performance
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timezone

import app.domains.telemetry.service as telemetry_service
from app.domains.telemetry.ingestion.mqtt_consumer import message_queue
from app.domains.telemetry.schemas import TelemetryEnvelope
from app.libs.common.logging import configure_logging
from app.libs.db.session import async_session_factory

logger = logging.getLogger(__name__)


class BatchMetrics:
    """
    Lưu các counter cục bộ của process cho hoạt động xử lý telemetry batch.

    Trong MVP, metric chủ ý chỉ nằm trong bộ nhớ và reset mỗi khi ingestion
    process khởi động lại. Batch thành công ghi nhận kết quả message và thời gian
    xử lý; transaction thất bại chỉ tăng counter lỗi batch.

    Attributes:
        batches_processed_total: Số transaction hoàn thành thành công.
        messages_processed_total: Số row được insert thành công.
        messages_skipped_total: Số message bị loại bởi rule mapping nghiệp vụ.
        message_errors_total: Số message hợp lệ nhưng lỗi khi chuyển đổi DB.
        batch_processing_time_seconds: Tổng thời gian xử lý các batch thành công.
        batch_errors_total: Số batch transaction phát sinh exception.
    """

    def __init__(self) -> None:
        """Khởi tạo toàn bộ metric cục bộ của process bằng không."""
        self.batches_processed_total = 0
        self.messages_processed_total = 0
        self.messages_skipped_total = 0
        self.message_errors_total = 0
        self.batch_processing_time_seconds = 0.0
        self.batch_errors_total = 0

    def record_batch(
        self,
        processed_count: int,
        skipped_count: int,
        error_count: int,
        processing_time: float,
    ) -> None:
        """
        Ghi nhận các counter và thời gian của một database batch thành công.

        Args:
            processed_count: Số message được insert vào database.
            skipped_count: Số message bị skip theo rule nghiệp vụ.
            error_count: Số message lỗi khi chuyển đổi.
            processing_time: Thời gian xử lý batch tính bằng giây.
        """
        self.batches_processed_total += 1
        self.messages_processed_total += processed_count
        self.messages_skipped_total += skipped_count
        self.message_errors_total += error_count
        self.batch_processing_time_seconds += processing_time

    def record_error(self) -> None:
        """
        Ghi nhận một batch transaction thất bại.

        Method không đếm lỗi theo từng message vì database failure trong
        transaction atomic khiến toàn bộ message của batch đều không thành công.
        """
        self.batch_errors_total += 1


# Ingestion process sở hữu một metrics instance trong toàn bộ lifecycle. Counter
# chủ ý reset khi restart vì persistent metrics nằm ngoài phạm vi MVP.
batch_metrics = BatchMetrics()


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
        ...     batch_size=100,
        ...     flush_interval=30.0,
        ... )
        >>> await worker.start()
        >>> # ... running ...
        >>> await worker.stop()
    """

    def __init__(
        self,
        queue: asyncio.Queue[TelemetryEnvelope] | None = None,
        batch_size: int = 100,
        flush_interval: float = 30.0,
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
        self.queue = queue or message_queue
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """
        Cấu hình logging của process và khởi động task consume queue.

        Gọi method khi worker đang chạy sẽ không thay đổi state và chỉ ghi
        warning, qua đó ngăn hai task cùng consume một queue.

        Side Effects:
            Cấu hình root JSON logger dùng chung và tạo một asyncio task.
        """
        if self._running:
            logger.warning("Batch worker is already running")
            return

        # Khởi tạo logging tại lifecycle boundary thay vì lúc import để việc
        # import module trong API process hoặc test không gây global side effect.
        configure_logging()
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Batch worker started",
            extra={
                "batch_size": self.batch_size,
                "flush_interval": self.flush_interval,
            },
        )

    async def stop(self, timeout: float = 60.0) -> None:
        """
        Dừng batch worker.

        Phương thức này được gọi khi graceful shutdown.
        Worker sẽ xử lý nốt batch hiện tại trước khi dừng.

        Args:
            timeout: Thời gian tối đa đợi worker dừng (giây)

        Side Effects:
            Ngừng nhận batch window mới, drain message đang có trong queue và chỉ
            cancel worker task khi hết graceful timeout.
        """
        if self._task is None:
            return

        logger.info("Stopping batch worker")
        self._running = False

        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Batch worker did not stop gracefully, cancelling task")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

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

        while self._running or not self.queue.empty():
            try:
                # Chờ message đầu tiên; không busy-loop khi queue rỗng.
                first_message = await asyncio.wait_for(
                    self.queue.get(), timeout=self.flush_interval
                )
            except asyncio.TimeoutError:
                # Queue không có message trong flush_interval; tiếp tục chờ.
                continue

            try:
                messages = await self._collect_batch(first_message)
                await self._process_batch(messages)
            except Exception:
                self._running = False
                logger.exception("Batch worker stopped after an unexpected error")
                raise

    async def _collect_batch(
        self, first_message: TelemetryEnvelope
    ) -> list[TelemetryEnvelope]:
        """
        Gom message cho tới khi batch đầy hoặc hết khoảng thời gian chờ.

        Khoảng thời gian bắt đầu khi message đầu tiên tới. Trong lúc shutdown,
        worker chỉ drain các message đã có trong queue thay vì chờ message mới.

        Args:
            first_message: Message bắt đầu khoảng thời gian gom batch.

        Returns:
            Các message được gom cho một database transaction.

        Side Effects:
            Lấy từng message trả về khỏi ``queue``. Lệnh ``task_done`` tương ứng
            được hoãn tới khi xử lý transaction hoàn tất.
        """
        messages = [first_message]
        # Dùng monotonic clock của event loop để việc hiệu chỉnh wall clock không
        # làm batch window ngắn hoặc dài hơn dự kiến.
        deadline = asyncio.get_running_loop().time() + self.flush_interval

        while len(messages) < self.batch_size:
            if not self._running:
                try:
                    messages.append(self.queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
                continue

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
            Exception: Raise lại lỗi database hoặc service sau khi ghi nhận batch
                thất bại cùng traceback.

        Side Effects:
            Commit khi thoát context thành công, rollback khi lỗi, cập nhật metric
            trong bộ nhớ, xuất structured log và acknowledge mỗi message đã lấy
            khỏi queue đúng một lần.
        """
        start_time = datetime.now(timezone.utc)
        try:
            # Worker là transaction boundary: service và repository được
            # execute/flush nhưng không bao giờ commit hoặc rollback.
            async with async_session_factory.begin() as db:
                result = await telemetry_service.process_batch(db, messages)

            processing_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            batch_metrics.record_batch(
                result["processed"],
                result["skipped"],
                result["errors"],
                processing_time,
            )
            logger.info(
                "Batch processed successfully",
                extra={
                    "batch_size": len(messages),
                    "processed": result["processed"],
                    "skipped": result["skipped"],
                    "errors": result["errors"],
                    "processing_time_ms": round(processing_time * 1000, 2),
                },
            )
        except Exception:
            batch_metrics.record_error()
            logger.exception(
                "Batch processing failed; stopping worker",
                extra={"batch_size": len(messages)},
            )
            raise
        finally:
            # Queue accounting không phụ thuộc database có thành công hay không.
            # Nếu không giữ invariant này, ``queue.join`` có thể block vô hạn khi
            # shutdown.
            for _ in messages:
                self.queue.task_done()
