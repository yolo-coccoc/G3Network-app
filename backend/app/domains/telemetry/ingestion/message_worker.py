"""Worker xử lý từng message telemetry trong queue.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Worker này là luồng active của telemetry ingestion MVP: mỗi lần lấy một
``TelemetryEnvelope`` khỏi queue, worker mở một transaction, gọi service xử lý
message và commit ngay khi operation thành công. Implementation batch cũ nằm
ở ``batch_worker.py`` và không thuộc lifecycle của process hiện tại.
"""

import asyncio
import logging

import app.domains.telemetry.service as telemetry_service
from app.domains.telemetry.ingestion.mqtt_consumer import message_queue
from app.domains.telemetry.schemas import TelemetryEnvelope
from app.libs.db.session import async_session_factory

logger = logging.getLogger(__name__)


class MessageWorker:
    """Worker consume và persist từng telemetry envelope.

    Attributes:
        queue: Queue chứa envelope đã được MQTT consumer validate.
        _running: Cho biết vòng lặp consume có tiếp tục nhận message hay không.
        _task: Background task sở hữu vòng lặp consume, hoặc ``None`` trước khi
            worker được khởi động.
    """

    def __init__(self, queue: asyncio.Queue[TelemetryEnvelope] | None = None) -> None:
        """Khởi tạo worker và nhận quyền sử dụng queue được truyền vào.

        Args:
            queue: Queue cần consume. Dùng queue mặc định cấp module khi bỏ
                trống.

        Side Effects:
            Khởi tạo state lifecycle trong memory; chưa tạo task hoặc database
            session cho tới khi gọi ``start``.
        """
        self.queue = message_queue if queue is None else queue
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Khởi động background task xử lý từng message trong queue.

        Gọi lại khi worker đang chạy chỉ ghi warning và không tạo task thứ hai.

        Side Effects:
            Tạo một asyncio task sở hữu vòng lặp consume.
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
        """Dừng worker ngay và bỏ qua message còn lại trong queue RAM.

        Side Effects:
            Hủy background task. Nếu transaction đang chạy, context manager
            session sẽ rollback; message chưa lấy khỏi queue không được drain
            theo policy MVP.
        """
        if self._task is None:
            return

        logger.info("Stopping message worker")
        self._running = False
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        logger.info("Message worker stopped")

    async def _run_loop(self) -> None:
        """Lấy và xử lý tuần tự từng message cho tới khi worker dừng.

        Raises:
            Exception: Raise lại lỗi bất ngờ hoặc lỗi database sau khi đánh dấu
                worker dừng, để entrypoint kết thúc process theo policy MVP.

        Side Effects:
            Lấy từng envelope khỏi queue và gọi transaction boundary cho mỗi
            message; message còn lại không bị drain khi worker dừng.
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
        """Xử lý một envelope trong một transaction độc lập.

        Args:
            envelope: Message đã được consumer validate.

        Raises:
            Exception: Raise lại lỗi service/database sau khi log traceback.

        Side Effects:
            Commit transaction nếu service thành công; rollback khi có exception
            và ghi summary structured log cho message.
        """
        try:
            # Worker là transaction boundary; service/repository chỉ execute,
            # không tự commit hoặc rollback.
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
