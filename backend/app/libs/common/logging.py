"""
Cấu hình structured logging dùng chung cho các backend process chạy độc lập.

Module chuyển đổi record chuẩn của ``logging`` và các field trong ``extra`` thành
JSON một dòng, phù hợp để hệ thống container thu thập log. Cấu hình có tính
idempotent để các lần gọi lặp trong lifecycle không gắn handler trùng lặp.

Module chủ ý chỉ dùng thư viện chuẩn Python. Việc vận chuyển, lưu giữ log và
monitoring tập trung nằm ngoài phạm vi telemetry ingestion MVP.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

# Record mẫu cung cấp tập key built-in chuẩn. Việc loại các key này giúp field
# truyền qua ``extra`` nằm trực tiếp ở cấp cao nhất của JSON mà không lặp lại các
# thông tin nội bộ như đường dẫn file, thread ID hoặc tuple tham số.
_STANDARD_LOG_RECORD_FIELDS = frozenset(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__
)
# Marker được gắn trên handler thay vì giữ bằng state của module vì test suite và
# code khởi tạo process có thể gọi ``configure_logging`` qua các lần import mới.
_HANDLER_MARKER = "_g3network_json_handler"


class JsonFormatter(logging.Formatter):
    """
    Chuyển log record của Python thành structured JSON một dòng.

    Output chuẩn luôn gồm timestamp UTC timezone-aware, mức độ, tên logger và
    message đã render. Giá trị truyền qua ``extra`` được giữ ở cấp cao nhất. Giá
    trị không hỗ trợ JSON được chuyển bằng ``str`` để lỗi observability không che
    mất sự kiện ứng dụng cần ghi nhận.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Chuyển một log record thành JSON.

        Args:
            record: Log record của Python cần serialize.

        Returns:
            Chuỗi JSON một dòng chứa các field chuẩn và field bổ sung.

        Side Effects:
            Khi record chứa exception, formatter của Python có thể cache
            traceback đã render trên chính record đầu vào.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Giữ structured context ở dạng mà log collector có thể truy vấn trực
        # tiếp, thay vì lồng toàn bộ vào một chuỗi message không có cấu trúc.
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_LOG_RECORD_FIELDS
                and key not in {"message", "asctime"}
                and not key.startswith("_")
            }
        )

        # ``logger.exception`` lưu traceback tách khỏi message; cần đưa traceback
        # vào payload rõ ràng để JSON handler không vô tình loại bỏ thông tin này.
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """
    Cấu hình JSON logging idempotent cho một backend process chạy độc lập.

    Root logger được dùng để consumer, service, repository và worker của telemetry
    cùng tuân theo một output contract. Các handler có sẵn được giữ nguyên; hàm
    chỉ bảo đảm có đúng một G3Network JSON handler được gắn thêm.

    Args:
        level: Mức log thấp nhất mà root logger phát ra.

    Side Effects:
        Cập nhật level của root logger và có thể gắn một stderr stream handler.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Lifecycle có thể gọi hàm nhiều lần trong startup test hoặc guarded restart.
    # Marker ngăn một record bị xuất thành nhiều dòng JSON trùng nhau.
    if any(
        getattr(handler, _HANDLER_MARKER, False) for handler in root_logger.handlers
    ):
        return

    # StreamHandler mặc định ghi vào stderr, phù hợp convention logging của
    # container và vẫn dành stdout cho output tường minh của process khi cần.
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    root_logger.addHandler(handler)
