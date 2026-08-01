"""Định nghĩa cấu hình dùng chung cho các backend process.

Module này là nguồn duy nhất định nghĩa tên biến, kiểu dữ liệu, validation và
giá trị mặc định an toàn. Giá trị phụ thuộc môi trường được nạp từ biến môi
trường hoặc file ``.env``; module không chứa credential mặc định.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Tập cấu hình được validate và cache theo lifecycle của backend process.

    Attributes:
        APP_NAME: Tên hiển thị của backend.
        APP_VERSION: Phiên bản ứng dụng.
        APP_DESCRIPTION: Mô tả hiển thị trong metadata của API.
        APP_DEBUG: Bật SQLAlchemy echo khi development.
        APP_LOG_LEVEL: Mức log mặc định cho process dùng structured logging.
        DATABASE_URL: Async SQLAlchemy URL tới PostgreSQL, bắt buộc từ môi trường.
        MQTT_HOST: Host của EMQX broker.
        MQTT_PORT: Cổng MQTT TCP.
        MQTT_CLIENT_ID: Client identifier của telemetry consumer.
        MQTT_USERNAME: Username MQTT tùy chọn.
        MQTT_PASSWORD: Password MQTT tùy chọn.
        MQTT_QOS: QoS của telemetry subscription trong MVP.
        MQTT_TELEMETRY_TOPIC: Topic pattern nhận telemetry.
        MQTT_STATUS_TOPIC_TEMPLATE: Mẫu topic MQTT Last Will.
        MQTT_WILL_QOS: QoS của MQTT Last Will.
        MQTT_WILL_RETAIN: Có retain MQTT Last Will hay không.
        TELEMETRY_QUEUE_SIZE: Sức chứa in-memory queue.
        TELEMETRY_BATCH_SIZE: Cấu hình batch worker được giữ cho phase tương lai.
        TELEMETRY_FLUSH_INTERVAL: Cửa sổ batch được giữ cho phase tương lai.
        API_DEFAULT_PAGE: Trang mặc định cho endpoint phân trang.
        API_DEFAULT_PAGE_SIZE: Số bản ghi mặc định mỗi trang.
        API_MAX_PAGE_SIZE: Số bản ghi tối đa mỗi trang.
        CHARGING_HEARTBEAT_TIMEOUT_SECONDS: Khoảng chờ heartbeat tối đa trước
            khi đánh dấu snapshot kỹ thuật là stale.
        CHARGING_OFFLINE_TIMEOUT_SECONDS: Khoảng chờ trước khi station chuyển
            sang offline.
        CHARGING_METER_STALE_TIMEOUT_SECONDS: Khoảng chờ để coi meter sample
            là stale trong các bước xử lý charging sau này.
        CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS: Timeout request OCPP.
        CHARGING_MAX_RAW_PAYLOAD_BYTES: Kích thước tối đa của raw payload sau
            khi sanitize.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Metadata ứng dụng có mặc định ổn định, còn môi trường có thể override qua .env.
    APP_NAME: str = "G3Network Backend"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = (
        "Backend for G3Network - Electric truck driver support system"
    )
    APP_DEBUG: bool = False
    APP_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # URL dùng asyncpg; credential phải đến từ môi trường, không để trong source.
    DATABASE_URL: str = Field(min_length=1)

    # Cấu hình kết nối và subscription telemetry tại EMQX. Các giá trị này có
    # fallback local để process vẫn có cấu hình hợp lệ khi chỉ cần development.
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = Field(default=1883, ge=1, le=65535)
    MQTT_CLIENT_ID: str = "g3network-backend"
    MQTT_USERNAME: str | None = None
    MQTT_PASSWORD: str | None = None
    MQTT_QOS: int = Field(default=0, ge=0, le=2)
    MQTT_TELEMETRY_TOPIC: str = "g3network/telematics/+/telemetry"
    MQTT_STATUS_TOPIC_TEMPLATE: str = "g3network/consumers/{client_id}/status"
    MQTT_WILL_QOS: int = Field(default=1, ge=0, le=2)
    MQTT_WILL_RETAIN: bool = True

    # Queue dùng cho luồng từng message hiện tại. Hai setting batch bên dưới
    # được giữ để implementation batch tương lai vẫn có thể khởi động lại.
    TELEMETRY_QUEUE_SIZE: int = Field(default=10000, ge=1)
    TELEMETRY_BATCH_SIZE: int = Field(default=100, ge=1)
    TELEMETRY_FLUSH_INTERVAL: float = Field(default=30.0, gt=0)

    # Chính sách phân trang dùng chung cho các domain có endpoint list.
    API_DEFAULT_PAGE: int = Field(default=1, ge=1)
    API_DEFAULT_PAGE_SIZE: int = Field(default=10, ge=1)
    API_MAX_PAGE_SIZE: int = Field(default=100, ge=1)

    # Đây là default an toàn cho môi trường development; gateway sẽ dùng cùng
    # namespace này để các process không tự suy diễn timeout khác nhau.
    CHARGING_HEARTBEAT_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)
    CHARGING_OFFLINE_TIMEOUT_SECONDS: float = Field(default=180.0, gt=0)
    CHARGING_METER_STALE_TIMEOUT_SECONDS: float = Field(default=300.0, gt=0)
    CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)
    CHARGING_MAX_RAW_PAYLOAD_BYTES: int = Field(default=65536, ge=1024)


@lru_cache
def get_settings() -> Settings:
    """
    Lấy settings instance được cache trong phạm vi process.

    Returns:
        Cấu hình đã validate; các lần gọi sau trong cùng process nhận cùng
        instance.
    """
    # Pydantic Settings đọc DATABASE_URL từ env/.env; mypy không suy luận được
    # nguồn giá trị ngoài constructor nên cần bỏ qua riêng cảnh báo này.
    return Settings()  # type: ignore[call-arg]


# Module-level instance là nguồn cấu hình chung trong một process. Process API và
# telemetry import cùng module path nhưng vẫn có instance riêng trong bộ nhớ.
settings = get_settings()
