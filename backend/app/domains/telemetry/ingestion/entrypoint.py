"""Entrypoint tối giản cho tiến trình nhận telemetry qua MQTT.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Tiến trình MVP chỉ giữ queue và task trong RAM. Khi nhận tín hiệu dừng hoặc một
task lỗi, process sẽ ngắt consumer, cancel worker và bỏ qua message còn lại
trong queue.
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
    """Chạy consumer và worker cho tới khi nhận signal hoặc có task thất bại.

    Side Effects:
        Tạo queue trong RAM, mở kết nối MQTT và đóng tài nguyên khi process dừng.

    Raises:
        RuntimeError: Khi worker không tạo được background task hoặc một task
            dừng bất thường.
        Exception: Raise lại lỗi từ consumer hoặc worker.
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
    """Chạy event loop cho telemetry ingestion và thoát với mã lỗi khi cần.

    Side Effects:
        Tạo event loop cho toàn process và ghi log khi process thất bại.
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
