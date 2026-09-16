"""Shared configuration definitions for the backend processes.

This module is the single source defining variable names, data types,
validation, and safe default values. Environment-dependent values are loaded
from environment variables or the ``.env`` file; the module does not contain
default credentials.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuration set validated and cached over the backend process lifecycle.

    Attributes:
        APP_NAME: Display name of the backend.
        APP_VERSION: Application version.
        APP_DESCRIPTION: Description shown in the API metadata.
        APP_DEBUG: Enables SQLAlchemy echo during development.
        APP_LOG_LEVEL: Default log level for processes using structured logging.
        DATABASE_URL: Async SQLAlchemy URL to PostgreSQL, required from the environment.
        MQTT_HOST: Host of the EMQX broker.
        MQTT_PORT: MQTT TCP port.
        MQTT_CLIENT_ID: Client identifier of the telemetry consumer.
        MQTT_USERNAME: Optional MQTT username.
        MQTT_PASSWORD: Optional MQTT password.
        MQTT_QOS: QoS of the telemetry subscription in the MVP.
        MQTT_TELEMETRY_TOPIC: Topic pattern for receiving telemetry.
        MQTT_STATUS_TOPIC_TEMPLATE: MQTT Last Will topic template.
        MQTT_WILL_QOS: QoS of the MQTT Last Will.
        MQTT_WILL_RETAIN: Whether to retain the MQTT Last Will.
        TELEMETRY_QUEUE_SIZE: In-memory queue capacity.
        TELEMETRY_BATCH_SIZE: Batch worker setting kept for a future phase.
        TELEMETRY_FLUSH_INTERVAL: Batch window kept for a future phase.
        API_DEFAULT_PAGE: Default page for paginated endpoints.
        API_DEFAULT_PAGE_SIZE: Default number of records per page.
        API_MAX_PAGE_SIZE: Maximum number of records per page.
        CHARGING_OCPP_HOST: Bind host of the OCPP WebSocket gateway.
        CHARGING_OCPP_PORT: Bind port of the OCPP WebSocket gateway.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application metadata has stable defaults, while the environment can override via .env.
    APP_NAME: str = "G3Network Backend"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = (
        "Backend for G3Network - Electric truck driver support system"
    )
    APP_DEBUG: bool = False
    APP_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # URL uses asyncpg; credentials must come from the environment, not be left in source.
    DATABASE_URL: str = Field(min_length=1)

    # Connection and telemetry subscription configuration at EMQX. These values
    # have local fallbacks so the process still has a valid configuration for
    # development-only use.
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

    # Queue used for the current per-message flow. The two batch settings below
    # are kept so a future batch implementation can pick back up.
    TELEMETRY_QUEUE_SIZE: int = Field(default=10000, ge=1)
    TELEMETRY_BATCH_SIZE: int = Field(default=100, ge=1)
    TELEMETRY_FLUSH_INTERVAL: float = Field(default=30.0, gt=0)

    # Pagination policy shared across domains that have list endpoints.
    API_DEFAULT_PAGE: int = Field(default=1, ge=1)
    API_DEFAULT_PAGE_SIZE: int = Field(default=10, ge=1)
    API_MAX_PAGE_SIZE: int = Field(default=100, ge=1)

    # This is a safe default for the development environment; the gateway will
    # use the same namespace so processes don't infer different timeouts on their own.
    CHARGING_OCPP_HOST: str = "0.0.0.0"
    CHARGING_OCPP_PORT: int = Field(default=9000, ge=1, le=65535)

    # The old production planner's timeout/retry/raw-payload settings are
    # commented out in the ideal MVP; the corresponding source will come back
    # when item 27 in future.md is picked up.
    # CHARGING_HEARTBEAT_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)
    # CHARGING_OFFLINE_TIMEOUT_SECONDS: float = Field(default=180.0, gt=0)
    # CHARGING_METER_STALE_TIMEOUT_SECONDS: float = Field(default=300.0, gt=0)
    # CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)
    # CHARGING_MAX_RAW_PAYLOAD_BYTES: int = Field(default=65536, ge=1024)


@lru_cache
def get_settings() -> Settings:
    """
    Get the settings instance cached for the process scope.

    Returns:
        The validated configuration; subsequent calls within the same process
        receive the same instance.
    """
    # Pydantic Settings reads DATABASE_URL from env/.env; mypy cannot infer the
    # value's source outside the constructor, so this specific warning is ignored.
    return Settings()  # type: ignore[call-arg]


# The module-level instance is the shared configuration source within a process.
# The API and telemetry processes import the same module path but still get
# their own instance in memory.
settings = get_settings()
