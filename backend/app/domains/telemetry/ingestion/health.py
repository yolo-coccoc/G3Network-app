"""
HTTP health server của telemetry ingestion process.

Module chỉ ánh xạ trạng thái runtime trong bộ nhớ sang endpoint ``GET /health``
và điều chỉnh Uvicorn để runtime orchestration tiếp tục sở hữu OS signal. Health
check không tự mở kết nối database hoặc MQTT và không thực hiện network I/O tại
import time.
"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from app.domains.telemetry.ingestion.batch_worker import BatchWorker
from app.domains.telemetry.ingestion.mqtt_consumer import MQTTConsumer
from app.libs.common.config import settings


class HealthServer(uvicorn.Server):
    """
    Uvicorn server không tự sở hữu OS signal.

    Uvicorn mặc định thay signal handler khi ``serve()`` chạy trên main thread.
    Telemetry runtime cần giữ ownership duy nhất của SIGINT/SIGTERM để dừng
    consumer trước khi drain worker, vì vậy subclass chỉ vô hiệu hóa phần bắt
    signal và giữ nguyên toàn bộ HTTP server lifecycle còn lại.
    """

    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        """
        Bỏ qua signal capture nội bộ của Uvicorn.

        Yields:
            Quyền điều khiển cho ``uvicorn.Server.serve()`` trong khi runtime
            tiếp tục sở hữu signal handler cấp process.
        """
        yield


@dataclass(slots=True)
class RuntimeState:
    """
    Trạng thái runtime chỉ đọc bởi health endpoint.

    Attributes:
        consumer: MQTT consumer của process.
        worker: Batch worker của process.
        started: Startup orchestration đã tạo đủ background task.
        stopping: Runtime đã bắt đầu graceful shutdown.
    """

    consumer: MQTTConsumer
    worker: BatchWorker
    started: bool = False
    stopping: bool = False

    @property
    def is_healthy(self) -> bool:
        """
        Xác định ingestion process có đang thực hiện đầy đủ vai trò hay không.

        Returns:
            ``True`` khi startup hoàn tất, process chưa shutdown, consumer đang
            nhận MQTT message và batch worker còn hoạt động.
        """
        return (
            self.started
            and not self.stopping
            and self.consumer.is_consuming
            and self.worker.is_running
        )


def create_health_app(runtime_state: RuntimeState) -> FastAPI:
    """
    Tạo FastAPI app chỉ phục vụ health của telemetry process.

    Args:
        runtime_state: State do runtime orchestration sở hữu và endpoint chỉ đọc.

    Returns:
        FastAPI app có duy nhất endpoint ``GET /health``.
    """
    app = FastAPI(
        title="G3Network Telemetry Ingestion Health",
        version=settings.APP_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health", response_model=None)
    async def health_check() -> JSONResponse:
        """
        Trả health dựa trên trạng thái consumer, worker và shutdown.

        Returns:
            HTTP 200 với ``healthy`` khi runtime sẵn sàng; ngược lại HTTP 503 với
            ``unhealthy``. Endpoint không query database hoặc mở MQTT connection.
        """
        if runtime_state.is_healthy:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "healthy"},
            )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy"},
        )

    return app


async def serve_health_server(health_server: HealthServer) -> None:
    """
    Chạy health server và chuyển Uvicorn startup exit thành runtime exception.

    Uvicorn dùng ``SystemExit`` khi không bind/start được server. Background task
    không được phép kết thúc toàn interpreter ngoài lifecycle monitor, nên wrapper
    chuyển trạng thái đó thành ``RuntimeError`` để runtime cleanup nhất quán.

    Args:
        health_server: Uvicorn server đã được cấu hình và không sở hữu signal.

    Raises:
        RuntimeError: Khi Uvicorn không thể khởi động health server.
    """
    try:
        await health_server.serve()
    except SystemExit as error:
        raise RuntimeError("Health server failed to start") from error
