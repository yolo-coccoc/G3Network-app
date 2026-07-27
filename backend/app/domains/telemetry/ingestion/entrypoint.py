"""
Process entrypoint của telemetry ingestion.

Module tạo asyncio event loop, gọi runtime orchestration và chuyển lỗi đã được
cleanup thành process exit code khác không. Module không thực hiện network I/O
tại import time.
"""

import asyncio
import logging

from app.domains.telemetry.ingestion.runtime import run

logger = logging.getLogger(__name__)


def main() -> None:
    """
    Chạy telemetry ingestion event loop và chuyển failure thành exit code 1.

    Side Effects:
        Tạo asyncio event loop cho toàn process và raise ``SystemExit(1)`` khi
        startup/runtime failure đã được structured log trong ``run()``.
    """
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        # Signal handler bình thường đã chuyển SIGINT thành shutdown event; nhánh
        # này chỉ bảo vệ trường hợp interrupt xảy ra trước khi handler được cài.
        logger.info("Telemetry ingestion interrupted during startup")
    except Exception as error:
        logger.error(
            "Telemetry ingestion process exited with failure",
            extra={
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
