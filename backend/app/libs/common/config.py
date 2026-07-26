"""Application configuration using Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "G3Network Backend"
    APP_VERSION: str = "0.1.0"
    APP_DEBUG: bool = True

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://g3network:g3network123@localhost:5432/g3network"
    )

    # MQTT
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_CLIENT_ID: str = "g3network-backend"
    MQTT_USERNAME: str | None = None
    MQTT_PASSWORD: str | None = None
    MQTT_QOS: int = 0  # QoS 0 for MVP
    MQTT_TELEMETRY_TOPIC: str = "g3network/telematics/+/telemetry"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
