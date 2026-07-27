"""
Orchestration vòng đời của telemetry ingestion process.

Module lắp ráp database startup probe, MQTT consumer, batch worker và HTTP health
server trong cùng một asyncio event loop. Runtime là component duy nhất sở hữu
signal và thứ tự startup/shutdown; module không thực hiện network I/O tại import
time.

Telemetry MVP dùng QoS 0, không retry/reconnect và không DLQ. Lỗi của consumer,
worker hoặc health server sẽ được propagate về entrypoint để cleanup toàn process
và thoát với mã lỗi khác không.
"""

import asyncio
import logging
import signal
from typing import Any

import uvicorn
from sqlalchemy import text

from app.domains.telemetry.ingestion.batch_worker import BatchWorker
from app.domains.telemetry.ingestion.health import (
    HealthServer,
    RuntimeState,
    create_health_app,
    serve_health_server,
)
from app.domains.telemetry.ingestion.mqtt_consumer import MQTTConsumer
from app.domains.telemetry.schemas import TelemetryEnvelope
from app.libs.common.config import settings
from app.libs.common.logging import configure_logging
from app.libs.db.session import async_session_factory, close_db

logger = logging.getLogger(__name__)


async def check_database_connection() -> None:
    """
    Buộc shared SQLAlchemy pool mở và xác minh một database connection.

    Import session factory vẫn lazy; ``SELECT 1`` tại lifecycle boundary mới tạo
    I/O thật. Probe dùng shared factory để kiểm tra đúng engine/pool mà batch
    worker sẽ dùng, nhưng không mở transaction business hoặc commit dữ liệu.

    Raises:
        SQLAlchemyError: Khi PostgreSQL không truy cập hoặc query không thực thi
            được. Exception được giữ nguyên để startup thất bại rõ ràng.
    """
    async with async_session_factory() as db:
        await db.execute(text("SELECT 1"))


def install_signal_handlers(
    shutdown_event: asyncio.Event,
) -> tuple[signal.Signals, ...]:
    """
    Đăng ký SIGINT/SIGTERM để đánh thức lifecycle monitor.

    Args:
        shutdown_event: Event được set khi process nhận signal.

    Returns:
        Tuple signal đã đăng ký để cleanup có thể remove handler.

    Side Effects:
        Thay signal handler hiện tại của event loop cho SIGINT và SIGTERM.
    """
    event_loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        event_loop.add_signal_handler(handled_signal, shutdown_event.set)
    return handled_signals


def remove_signal_handlers(handled_signals: tuple[signal.Signals, ...]) -> None:
    """
    Gỡ các signal handler do entrypoint đã đăng ký.

    Args:
        handled_signals: Các signal trả về từ ``install_signal_handlers``.

    Side Effects:
        Loại handler tương ứng khỏi event loop hiện tại.
    """
    event_loop = asyncio.get_running_loop()
    for handled_signal in handled_signals:
        event_loop.remove_signal_handler(handled_signal)


async def settle_task(
    task: asyncio.Task[Any],
    timeout: float,
    component: str,
) -> BaseException | None:
    """
    Chờ một task kết thúc; cancel và await nếu vượt timeout.

    Args:
        task: Background task cần thu hồi.
        timeout: Số giây còn lại của graceful shutdown.
        component: Tên component dùng trong structured log.

    Returns:
        Exception của task nếu task thất bại; ngược lại trả về ``None``.

    Side Effects:
        Có thể cancel task và luôn retrieve kết quả/exception để không phát sinh
        cảnh báo ``Task exception was never retrieved``.
    """
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=max(timeout, 0.001))
    except asyncio.TimeoutError:
        logger.warning(
            "Runtime task exceeded shutdown timeout; cancelling",
            extra={"component": component},
        )
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        task_result = result[0]
        if isinstance(task_result, BaseException) and not isinstance(
            task_result, asyncio.CancelledError
        ):
            return task_result
        return None
    except asyncio.CancelledError:
        return None
    except Exception as error:
        return error
    return None


async def shutdown_runtime(
    runtime_state: RuntimeState,
    consumer_task: asyncio.Task[None] | None,
    worker_wait_task: asyncio.Task[None] | None,
    health_server: HealthServer | None,
    health_task: asyncio.Task[None] | None,
) -> list[BaseException]:
    """
    Dừng các runtime component theo thứ tự và thu hồi toàn bộ task.

    Args:
        runtime_state: State cần chuyển sang stopping/unhealthy.
        consumer_task: Task chạy MQTT consume loop nếu đã tạo.
        worker_wait_task: Task theo dõi batch worker nếu đã tạo.
        health_server: Uvicorn server nếu đã khởi tạo.
        health_task: Task chạy health server nếu đã tạo.

    Returns:
        Danh sách exception phát sinh trong cleanup để caller log nhưng vẫn tiếp
        tục đóng các tài nguyên còn lại.

    Side Effects:
        Ngừng nhận MQTT message, drain/cancel worker, dừng health server và
        dispose shared database engine.
    """
    runtime_state.stopping = True
    errors: list[BaseException] = []
    event_loop = asyncio.get_running_loop()
    deadline = event_loop.time() + settings.TELEMETRY_SHUTDOWN_TIMEOUT

    def remaining_timeout() -> float:
        """Trả số giây còn lại trong một shutdown deadline dùng chung."""
        return max(0.001, deadline - event_loop.time())

    # Ngừng producer trước để queue không tăng thêm trong lúc worker drain.
    try:
        await runtime_state.consumer.disconnect()
    except Exception as error:
        errors.append(error)
        logger.exception("Failed to stop MQTT consumer")

    if consumer_task is not None:
        consumer_task_error = await settle_task(
            consumer_task,
            remaining_timeout(),
            "mqtt-consumer",
        )
        if consumer_task_error is not None:
            errors.append(consumer_task_error)

    # Worker.stop() giữ transaction hiện tại và drain queue trong timeout còn lại.
    try:
        await runtime_state.worker.stop(timeout=remaining_timeout())
    except Exception as error:
        errors.append(error)

    if worker_wait_task is not None:
        worker_task_error = await settle_task(
            worker_wait_task,
            remaining_timeout(),
            "batch-worker",
        )
        if worker_task_error is not None and worker_task_error not in errors:
            errors.append(worker_task_error)

    if health_server is not None:
        health_server.should_exit = True
    if health_task is not None:
        health_task_error = await settle_task(
            health_task,
            remaining_timeout(),
            "health-server",
        )
        if health_task_error is not None:
            errors.append(health_task_error)

    try:
        await close_db()
    except Exception as error:
        errors.append(error)
        logger.exception("Failed to dispose database engine")

    return errors


async def run() -> None:
    """
    Khởi động, theo dõi và shutdown telemetry ingestion runtime.

    Entrypoint tạo đúng một queue rồi inject vào consumer và worker. Hàm chờ
    signal hoặc component đầu tiên kết thúc; component kết thúc trước signal được
    xem là runtime failure. Cleanup luôn chạy trước khi failure được raise lại.

    Raises:
        Exception: Khi database startup probe hoặc một runtime component thất
            bại. ``main()`` chuyển failure thành process exit code 1.
    """
    configure_logging()
    logger.info("Telemetry ingestion process starting")

    shutdown_event = asyncio.Event()
    handled_signals = install_signal_handlers(shutdown_event)
    queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue(
        maxsize=settings.TELEMETRY_QUEUE_SIZE
    )
    consumer = MQTTConsumer(queue=queue)
    worker = BatchWorker(
        queue=queue,
        batch_size=settings.TELEMETRY_BATCH_SIZE,
        flush_interval=settings.TELEMETRY_FLUSH_INTERVAL,
    )
    runtime_state = RuntimeState(consumer=consumer, worker=worker)

    consumer_task: asyncio.Task[None] | None = None
    worker_wait_task: asyncio.Task[None] | None = None
    health_server: HealthServer | None = None
    health_task: asyncio.Task[None] | None = None
    shutdown_wait_task: asyncio.Task[bool] | None = None
    runtime_error: BaseException | None = None

    try:
        await check_database_connection()
        logger.info("Database startup probe succeeded")

        await consumer.connect()
        await worker.start()

        health_app = create_health_app(runtime_state)
        health_config = uvicorn.Config(
            health_app,
            host=settings.TELEMETRY_HEALTH_HOST,
            port=settings.TELEMETRY_HEALTH_PORT,
            log_config=None,
            access_log=False,
        )
        health_server = HealthServer(health_config)

        consumer_task = asyncio.create_task(
            consumer.start_consuming(),
            name="telemetry-mqtt-consumer",
        )
        worker_wait_task = asyncio.create_task(
            worker.wait(),
            name="telemetry-batch-worker-wait",
        )
        health_task = asyncio.create_task(
            serve_health_server(health_server),
            name="telemetry-health-server",
        )
        shutdown_wait_task = asyncio.create_task(
            shutdown_event.wait(),
            name="telemetry-shutdown-signal",
        )
        runtime_state.started = True

        logger.info(
            "Telemetry ingestion runtime started",
            extra={
                "health_host": settings.TELEMETRY_HEALTH_HOST,
                "health_port": settings.TELEMETRY_HEALTH_PORT,
                "queue_size": settings.TELEMETRY_QUEUE_SIZE,
                "batch_size": settings.TELEMETRY_BATCH_SIZE,
                "flush_interval": settings.TELEMETRY_FLUSH_INTERVAL,
            },
        )

        monitored_tasks: dict[asyncio.Task[Any], str] = {
            consumer_task: "mqtt-consumer",
            worker_wait_task: "batch-worker",
            health_task: "health-server",
            shutdown_wait_task: "shutdown-signal",
        }
        completed_tasks, _ = await asyncio.wait(
            monitored_tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if shutdown_wait_task in completed_tasks:
            logger.info("Shutdown signal received")

        for completed_task in completed_tasks:
            if completed_task is shutdown_wait_task:
                completed_task.result()
                continue
            component = monitored_tasks[completed_task]
            try:
                completed_task.result()
            except asyncio.CancelledError:
                continue
            except Exception as error:
                logger.error(
                    "Runtime component failed",
                    exc_info=(type(error), error, error.__traceback__),
                    extra={"component": component},
                )
                runtime_error = error
            else:
                runtime_error = RuntimeError(
                    f"Runtime component '{component}' stopped unexpectedly"
                )
                logger.error(
                    "Runtime component stopped unexpectedly",
                    extra={"component": component},
                )
    except Exception as error:
        logger.error(
            "Telemetry ingestion startup failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        runtime_error = error
    finally:
        if shutdown_wait_task is not None and not shutdown_wait_task.done():
            shutdown_wait_task.cancel()
            await asyncio.gather(shutdown_wait_task, return_exceptions=True)

        cleanup_errors = await shutdown_runtime(
            runtime_state,
            consumer_task,
            worker_wait_task,
            health_server,
            health_task,
        )
        remove_signal_handlers(handled_signals)

        for cleanup_error in cleanup_errors:
            if cleanup_error is runtime_error:
                continue
            logger.error(
                "Runtime cleanup reported an error",
                extra={
                    "error_type": type(cleanup_error).__name__,
                    "error": str(cleanup_error),
                },
            )
        if runtime_error is None and cleanup_errors:
            runtime_error = cleanup_errors[0]

    if runtime_error is not None:
        raise runtime_error

    logger.info("Telemetry ingestion process stopped")
