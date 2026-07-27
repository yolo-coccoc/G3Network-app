"""
Cấu hình dùng chung của các backend process bằng Pydantic Settings.

Settings đọc biến môi trường và file ``.env`` tại working directory của process.
Tên biến được namespace theo component để API, MQTT và telemetry ingestion không
va chạm khi cùng chạy trên host.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Tập cấu hình được validate và cache theo lifecycle của backend process.

    Attributes:
        APP_NAME: Tên hiển thị của backend.
        APP_VERSION: Phiên bản ứng dụng.
        APP_DEBUG: Bật SQLAlchemy echo và hành vi debug khi development.
        DATABASE_URL: Async SQLAlchemy URL tới PostgreSQL.
        MQTT_HOST: Host của EMQX broker.
        MQTT_PORT: Cổng MQTT TCP.
        MQTT_CLIENT_ID: Client identifier của telemetry consumer.
        MQTT_USERNAME: Username MQTT tùy chọn.
        MQTT_PASSWORD: Password MQTT tùy chọn.
        MQTT_QOS: QoS của telemetry subscription trong MVP.
        MQTT_TELEMETRY_TOPIC: Topic pattern nhận telemetry.
        TELEMETRY_QUEUE_SIZE: Sức chứa in-memory queue.
        TELEMETRY_BATCH_SIZE: Số message tối đa trong một transaction.
        TELEMETRY_FLUSH_INTERVAL: Thời gian tối đa chờ batch chưa đầy.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Cấu hình chung của application process.
    APP_NAME: str = "G3Network Backend"
    APP_VERSION: str = "0.1.0"
    APP_DEBUG: bool = True

    # URL dùng asyncpg; mỗi OS process tạo một engine/pool riêng từ URL này.
    DATABASE_URL: str = (
        "postgresql+asyncpg://g3network:g3network123@localhost:5432/g3network"
    )

    # Cấu hình kết nối và subscription telemetry tại EMQX.
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = Field(default=1883, ge=1, le=65535)
    MQTT_CLIENT_ID: str = "g3network-backend"
    MQTT_USERNAME: str | None = None
    MQTT_PASSWORD: str | None = None
    MQTT_QOS: int = Field(default=0, ge=0, le=2)
    MQTT_TELEMETRY_TOPIC: str = "g3network/telematics/+/telemetry"

    # Cấu hình queue và batch của telemetry process.
    TELEMETRY_QUEUE_SIZE: int = Field(default=10000, ge=1)
    TELEMETRY_BATCH_SIZE: int = Field(default=100, ge=1)
    TELEMETRY_FLUSH_INTERVAL: float = Field(default=30.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    """
    Lấy settings instance được cache trong phạm vi process.

    Returns:
        Cấu hình đã validate; các lần gọi sau trong cùng process nhận cùng
        instance.
    """
    return Settings()


# Module-level instance là nguồn cấu hình chung trong một process. Process API và
# telemetry import cùng module path nhưng vẫn có instance riêng trong bộ nhớ.
settings = get_settings()
