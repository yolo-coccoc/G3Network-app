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
        TELEMETRY_HISTORY_MAX_RANGE_DAYS: Maximum span allowed between
            start_time/end_time on the telemetry history query (F-A5).
        TELEMETRY_HISTORY_DEFAULT_LIMIT: Default max points returned per
            telemetry history call.
        TELEMETRY_HISTORY_MAX_LIMIT: Hard cap on points returned per
            telemetry history call.
        API_DEFAULT_PAGE: Default page for paginated endpoints.
        API_DEFAULT_PAGE_SIZE: Default number of records per page.
        API_MAX_PAGE_SIZE: Maximum number of records per page.
        CHARGING_OCPP_HOST: Bind host of the OCPP WebSocket gateway.
        CHARGING_OCPP_PORT: Bind port of the OCPP WebSocket gateway.
        CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS: Heartbeat ``interval``
            the gateway returns in an OCPP 1.6J ``BootNotification``
            response (the spec's range is 60-300 s).
        CHARGING_OFFLINE_TIMEOUT_SECONDS: How long without any frame from a
            charger before it is reported as offline (``is_online``); keep
            it a few multiples of the heartbeat interval.
        CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS: How long the gateway waits for a
            charger's answer to a request it sent (the post-boot
            ``GetConfiguration``).
        CHARGING_OCPP_MAX_MESSAGE_BYTES: Largest OCPP message the gateway
            accepts; bigger ones are refused by the WebSocket library
            instead of being stored truncated in the raw message log.
        CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM: Maximum radius accepted by
            the nearby-station search (F-D1).
        TELEMATICS_HEALTH_CHECK_INTERVAL_SECONDS: How often the device
            health monitor sweeps for silent devices (F-J1/F-J3).
        TELEMATICS_SILENT_THRESHOLD_MINUTES: How long without telemetry
            counts as a device going silent (F-J1/F-J3).
        MQTT_COMMAND_TOPIC_TEMPLATE: Topic template for backend->device
            commands (F-J2).
        MQTT_COMMAND_CLIENT_ID: Client identifier prefix for the
            short-lived publisher used to send device commands (F-J2).
        MQTT_COMMAND_QOS: QoS used when publishing a device command.
        MQTT_COMMAND_RETAIN: Whether a device command is retained.
        MQTT_COMMAND_TIMEOUT_SECONDS: Timeout bounding one command publish.
        TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS: Lower bound accepted
            for a pushed telemetry publish interval (F-J2).
        TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS: Upper bound accepted
            for a pushed telemetry publish interval (F-J2).
        TELEMETRY_REPORT_MAX_RANGE_DAYS: Maximum span allowed between
            start_time/end_time on the F-A6/F-C6 operating and
            energy-usage report queries.
        SUPPORT_TICKET_RESPONSE_SLA_MINUTES: Minutes allowed to first
            respond to an in-app support ticket (F-I1).
        SUPPORT_SOS_RESPONSE_SLA_MINUTES: Minutes allowed to first respond
            to an SOS case (F-I2's stated <=5 minute callback SLA).
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

    # Bounds for the telemetry history query (F-A5). No offset/page - a
    # range with more points than the limit is narrowed by the caller
    # instead, avoiding OFFSET pagination on a hypertable ordered by time.
    TELEMETRY_HISTORY_MAX_RANGE_DAYS: int = Field(default=7, ge=1)
    TELEMETRY_HISTORY_DEFAULT_LIMIT: int = Field(default=500, ge=1)
    TELEMETRY_HISTORY_MAX_LIMIT: int = Field(default=2000, ge=1)

    # Pagination policy shared across domains that have list endpoints.
    API_DEFAULT_PAGE: int = Field(default=1, ge=1)
    API_DEFAULT_PAGE_SIZE: int = Field(default=10, ge=1)
    API_MAX_PAGE_SIZE: int = Field(default=100, ge=1)

    # OCPP gateway (charging_stations/ocpp/entrypoint.py) settings, read from
    # this one namespace so no process infers its own timeouts. The bind
    # address is a safe development default.
    CHARGING_OCPP_HOST: str = "0.0.0.0"
    CHARGING_OCPP_PORT: int = Field(default=9000, ge=1, le=65535)
    # Defaults to the WebSocket library's own 1 MiB limit rather than a small
    # value: a full GetConfiguration reply from a real charger can be large.
    CHARGING_OCPP_MAX_MESSAGE_BYTES: int = Field(default=1_048_576, ge=1024)
    CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS: int = Field(default=60, ge=1)
    # Three missed heartbeats at the default interval.
    CHARGING_OFFLINE_TIMEOUT_SECONDS: float = Field(default=180.0, gt=0)
    CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)

    # Upper bound for the nearby-station search's radius_km query param
    # (F-D1) - guards against an unbounded PostGIS scan.
    CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM: float = Field(default=200.0, gt=0)

    # F-J1/F-J3's periodic device-silence check. The interval must stay
    # materially smaller than the threshold, or a device could sit past the
    # threshold for a whole interval before anyone notices.
    TELEMATICS_HEALTH_CHECK_INTERVAL_SECONDS: float = Field(default=300.0, gt=0)
    TELEMATICS_SILENT_THRESHOLD_MINUTES: int = Field(default=180, ge=1)

    # F-J2's backend->device command channel (mqtt-spec.md 2.3). The client
    # id must differ from MQTT_CLIENT_ID: a broker evicts an existing
    # session when a second connection claims the same id, so reusing the
    # telemetry consumer's id here would kick ingestion offline on every
    # config push. QoS 1 rather than the telemetry default of 0 because a
    # config command is a one-shot instruction - dropping it silently
    # leaves the device on its old interval with nothing to notice the
    # loss. The timeout bounds how long an HTTP request holds its database
    # transaction open across a broker round trip.
    MQTT_COMMAND_TOPIC_TEMPLATE: str = "g3network/telematics/{telematic_serial}/command"
    MQTT_COMMAND_CLIENT_ID: str = "g3network-backend-command"
    MQTT_COMMAND_QOS: int = Field(default=1, ge=0, le=2)
    MQTT_COMMAND_RETAIN: bool = False
    MQTT_COMMAND_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0)

    # F-J2's accepted range for the device telemetry publish interval. The
    # lower bound is the load-bearing one: an operator typo of "1" would
    # multiply this device's contribution to ingestion volume by the old
    # interval's factor, with no device-side guard anywhere to catch it.
    TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS: int = Field(default=5, ge=1)
    TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS: int = Field(default=3600, ge=1)

    # Bound for the F-A6/F-C6 report window. Deliberately larger than
    # TELEMETRY_HISTORY_MAX_RANGE_DAYS: the history endpoint materializes
    # one JSON point per row, so its 7-day cap bounds the *response
    # size*; this report streams a SQL aggregate and returns O(1) bytes
    # regardless of window length, so its only real constraint is scan
    # time. 31 days covers F-A6's "daily/weekly/monthly" requirement
    # including the longest calendar month. A cap still exists to stop an
    # unbounded "since 1970" request from scanning every hypertable chunk.
    TELEMETRY_REPORT_MAX_RANGE_DAYS: int = Field(default=31, ge=1)

    # F-I1/F-I2's response-SLA minutes. Copied onto each support_cases row
    # at creation time rather than read live at breach-check time, so a
    # later change to these settings never rewrites the SLA a past case
    # was actually held to. The SOS default matches the spec's stated
    # <=5 minute callback SLA; the ticket default (60) is an engineering
    # placeholder pending the CSKH staffing/SLA business decision recorded
    # in feature-list.md's "Items needing confirmation".
    SUPPORT_TICKET_RESPONSE_SLA_MINUTES: int = Field(default=60, ge=1)
    SUPPORT_SOS_RESPONSE_SLA_MINUTES: int = Field(default=5, ge=1)


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
