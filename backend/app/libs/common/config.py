"""Shared configuration definitions for the backend processes.

This module is the single source defining variable names, data types,
validation, and safe default values. Environment-dependent values are loaded
from environment variables or the ``.env`` file; the module does not contain
default credentials.
"""

from decimal import Decimal
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
        MQTT_STATUS_REPORT_TOPIC: Topic pattern for the devices' status
            reports (DEV-04, mqtt-spec.md 2.2).
        MQTT_STATUS_CLIENT_ID: Client identifier of the status-report
            consumer; must differ from ``MQTT_CLIENT_ID``.
        TELEMATICS_STATUS_QUEUE_SIZE: In-memory queue capacity of the
            status-report consumer.
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
            ``GetConfiguration`` and every queued command).
        CHARGING_OCPP_COMMAND_POLL_SECONDS: How often the gateway looks for
            queued ``charging_station_commands`` of its connected chargers
            (PR-16).
        CHARGING_OCPP_COMMAND_PICKUP_TIMEOUT_SECONDS: How long a queued command
            may wait for a connected charger before it is closed as
            ``NOT_SENT`` (CS-20).
        CHARGING_PENDING_SESSION_TIMEOUT_SECONDS: How long a scan may wait for
            the charger to start before its PENDING session is ABANDONED
            (CE-10).
        CHARGING_SESSION_SWEEP_INTERVAL_SECONDS: How often the gateway's loop
            sweeps expired PENDING sessions.
        BILLING_MIN_BALANCE_VND: Wallet balance needed to start a charge
            (BL-14); 0 disables the check.
        CHARGING_OCPP_MAX_MESSAGE_BYTES: Largest OCPP message the gateway
            accepts; bigger ones are refused by the WebSocket library
            instead of being stored truncated in the raw message log.
        CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM: Maximum radius accepted by
            the nearby-station search (F-D1).
        DRIVERS_CHECKIN_MAX_DISTANCE_M: Farthest the phone may be from the
            truck's last T-Box position at check-in (DR-07).
        DRIVERS_CHECKIN_POSITION_MAX_AGE_MINUTES: Oldest T-Box position that
            still counts for that check; an older one is treated as "no
            recent position" and the check-in is allowed with a warning.
        DRIVERS_MOVING_SPEED_KMH: Speed above which a telemetry sample means
            the truck is moving (auto-end of a driving session, DR-07).
        DRIVERS_AUTO_END_CHECK_INTERVAL_SECONDS: How often the driving-session
            auto-end worker sweeps the open sessions.
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
        TELEMETRY_ONLINE_THRESHOLD_SECONDS: A vehicle is "online" while its
            newest telemetry arrived within this many seconds (computed at
            read time; separate from the device-silence alert).
        TELEMETRY_ENERGY_COST_PER_KWH_VND: Electricity price used by the
            F-A6/F-C6 cost figures.
        TELEMETRY_SOH_ALERT_THRESHOLD_PERCENT: State of health below which
            an F-A3 SOH alert is raised.
        TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS: Maximum span of the F-A3
            battery-health trend query.
        APP_REPORT_TIMEZONE: IANA time zone that defines a "day"/"week"/
            "month" when reports and series are bucketed; timestamps in
            responses stay UTC.
        CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS: Maximum span of the F-C5
            station energy time-series query.
        IDENTITY_TOKEN_SECRET_KEY: Secret that signs access tokens and keys
            the hash of one-time codes; leave empty in development to get a
            random per-process key (tokens then die with the process).
        IDENTITY_ACCESS_TOKEN_TTL_SECONDS: Lifetime of an access token; the
            refresh token renews it.
        IDENTITY_SESSION_DRIVER_APP_DAYS: Idle lifetime of an Android / iOS
            session (ID-36).
        IDENTITY_SESSION_PORTAL_DAYS: Idle lifetime of a browser session of a
            customer user (ID-36).
        IDENTITY_SESSION_INTERNAL_DAYS: Idle lifetime of a session of a user
            who belongs to an internal organization (ID-36).
        IDENTITY_LOGIN_MAX_FAILED_ATTEMPTS: Consecutive wrong passwords that
            start a temporary login lockout (ID-23).
        IDENTITY_LOGIN_LOCKOUT_MINUTES: Length of that lockout.
        IDENTITY_PASSWORD_MIN_LENGTH: Shortest accepted new password.
        IDENTITY_SMS_PROVIDER: SMS gateway for one-time codes and alerts;
            only ``log`` (a fake that writes the message to the log) exists.
        IDENTITY_EMAIL_PROVIDER: E-mail gateway; only ``log`` exists.
        IDENTITY_OTP_TTL_MINUTES: Lifetime of a sign-up, reset or phone-change
            code.
        IDENTITY_INVITE_TTL_HOURS: Lifetime of an invitation code (ID-16).
        IDENTITY_OTP_MAX_FAILED_ATTEMPTS: Wrong guesses that kill a code
            (ID-37).
        IDENTITY_OTP_RESEND_COOLDOWN_SECONDS: Shortest gap between two codes
            for one phone number and purpose.
        IDENTITY_OTP_MAX_PER_PHONE_PER_DAY: Codes one phone number may be sent
            in 24 hours (SMS-pumping guard).
        IDENTITY_AUDIT_LOG_MAX_RANGE_DAYS: Longest time range of one audit
            log search.
        IDENTITY_EMERGENCY_ADMIN_PHONE: Phone number of the sealed emergency
            HEAD_ADMIN account (ACC-20); a login of this account alerts the
            other head administrators.
        IDENTITY_BOOTSTRAP_ORGANIZATION_NAME: Display name of the internal
            organization the bootstrap command creates.
        IDENTITY_BOOTSTRAP_ORGANIZATION_LEGAL_NAME: Its registered name.
        IDENTITY_BOOTSTRAP_ORGANIZATION_TAX_CODE: Its tax code (optional).
        IDENTITY_BOOTSTRAP_ADMIN_PHONE: Phone number of the first HEAD_ADMIN.
        IDENTITY_BOOTSTRAP_ADMIN_NAME: Full name of the first HEAD_ADMIN.
        IDENTITY_BOOTSTRAP_ADMIN_PASSWORD: Password of the first HEAD_ADMIN.
        IDENTITY_BOOTSTRAP_EMERGENCY_ADMIN_PASSWORD: Password of the emergency
            account created when ``IDENTITY_EMERGENCY_ADMIN_PHONE`` is set.
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

    # In-RAM queue between the MQTT consumer and the per-message worker. (A
    # batched ingestion path is deferred: deferred.md item 25.)
    TELEMETRY_QUEUE_SIZE: int = Field(default=10000, ge=1)

    # Status-report ingestion (DEV-03/DEV-04, mqtt-spec.md 2.2): its own
    # process and client id, because a broker evicts an existing session when a
    # second connection claims the same id (the telemetry consumer's).
    MQTT_STATUS_REPORT_TOPIC: str = "g3network/telematics/+/status"
    MQTT_STATUS_CLIENT_ID: str = "g3network-backend-status"
    TELEMATICS_STATUS_QUEUE_SIZE: int = Field(default=1000, ge=1)

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
    # Command channel between the API and the gateway (PR-16): the gateway polls
    # the command table; a queued command no connected gateway picks up within
    # the pickup timeout ends as NOT_SENT.
    CHARGING_OCPP_COMMAND_POLL_SECONDS: float = Field(default=1.0, gt=0)
    CHARGING_OCPP_COMMAND_PICKUP_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)
    # QR charging (CE-10, CE-11): a PENDING session whose charger never started
    # within this many seconds of the scan becomes ABANDONED, and its token
    # stops working. The gateway's command loop runs the sweep every
    # CHARGING_SESSION_SWEEP_INTERVAL_SECONDS.
    CHARGING_PENDING_SESSION_TIMEOUT_SECONDS: float = Field(default=300.0, gt=0)
    CHARGING_SESSION_SWEEP_INTERVAL_SECONDS: float = Field(default=30.0, gt=0)

    # Wallet minimum balance to start a charge, in VND (BL-14). 0 turns the check
    # off, which is the default until top-ups exist (WP9).
    BILLING_MIN_BALANCE_VND: Decimal = Field(default=Decimal(0), ge=0)

    # Upper bound for the nearby-station search's radius_km query param
    # (F-D1) - guards against an unbounded PostGIS scan.
    CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM: float = Field(default=200.0, gt=0)

    # DR-07 check-in: the phone must be near the truck's last T-Box position.
    # A truck without a recent position (no T-Box, or silent) cannot be
    # checked, so the check-in is allowed and flagged instead of refused.
    DRIVERS_CHECKIN_MAX_DISTANCE_M: float = Field(default=500.0, gt=0)
    DRIVERS_CHECKIN_POSITION_MAX_AGE_MINUTES: int = Field(default=30, ge=1)
    # DR-07 auto-end: a sample faster than this counts as "the truck moved";
    # the worker sweeps often enough for the shortest organization setting.
    DRIVERS_MOVING_SPEED_KMH: float = Field(default=2.0, ge=0)
    DRIVERS_AUTO_END_CHECK_INTERVAL_SECONDS: float = Field(default=60.0, gt=0)

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
    # size*; this report streams a SQL aggregate (one row per period), so
    # its only real constraint is scan time. 366 days lets a monthly
    # breakdown span a year. A cap still exists to stop an unbounded
    # "since 1970" request from scanning every hypertable chunk.
    TELEMETRY_REPORT_MAX_RANGE_DAYS: int = Field(default=366, ge=1)

    # F-I1/F-I2's response-SLA minutes. Copied onto each support_cases row
    # at creation time rather than read live at breach-check time, so a
    # later change to these settings never rewrites the SLA a past case
    # was actually held to. The SOS default matches the spec's stated
    # <=5 minute callback SLA; the ticket default (60) is an engineering
    # placeholder pending the CSKH staffing/SLA business decision recorded
    # in feature-list.md's "Items needing confirmation".
    SUPPORT_TICKET_RESPONSE_SLA_MINUTES: int = Field(default=60, ge=1)
    SUPPORT_SOS_RESPONSE_SLA_MINUTES: int = Field(default=5, ge=1)

    # "Online" for maps and lists (F-A1/F-E1/F-J1): derived at read time from
    # the newest telemetry receive time, never stored (database.md). Devices
    # publish every 5-10 s, so 5 minutes of silence is a clear "offline";
    # the much longer TELEMATICS_SILENT_THRESHOLD_MINUTES drives the alert.
    TELEMETRY_ONLINE_THRESHOLD_SECONDS: int = Field(default=300, ge=1)

    # F-A6/F-C6 cost figures: one flat tariff until time-of-use/per-tenant
    # pricing exists (deferred.md item 60).
    TELEMETRY_ENERGY_COST_PER_KWH_VND: float = Field(default=3000.0, ge=0)

    # F-A3: SOH below this raises an alert; vendor-validated value pending.
    TELEMETRY_SOH_ALERT_THRESHOLD_PERCENT: float = Field(default=70.0, gt=0, le=100)

    # F-A3 battery-health trend: one point per day, so a year is ~366 points.
    TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS: int = Field(default=366, ge=1)

    # Calendar used to cut reports and series into days/weeks/months. The
    # operator works in Vietnam time; stored and returned timestamps stay UTC.
    APP_REPORT_TIMEZONE: str = "Asia/Ho_Chi_Minh"

    # F-C5 station energy series: hourly buckets over a month = 744 points.
    CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS: int = Field(default=31, ge=1)

    # Identity (WP2): sessions, passwords, one-time codes. The secret has no
    # default on purpose (no default credentials in source): when empty, the
    # security module draws a random key at start-up and logs a warning.
    IDENTITY_TOKEN_SECRET_KEY: str = ""
    IDENTITY_ACCESS_TOKEN_TTL_SECONDS: int = Field(default=900, ge=60)
    IDENTITY_SESSION_DRIVER_APP_DAYS: int = Field(default=90, ge=1)
    IDENTITY_SESSION_PORTAL_DAYS: int = Field(default=7, ge=1)
    IDENTITY_SESSION_INTERNAL_DAYS: int = Field(default=1, ge=1)
    IDENTITY_LOGIN_MAX_FAILED_ATTEMPTS: int = Field(default=5, ge=1)
    IDENTITY_LOGIN_LOCKOUT_MINUTES: int = Field(default=15, ge=1)
    IDENTITY_PASSWORD_MIN_LENGTH: int = Field(default=8, ge=6)
    # External providers sit behind an interface (PR-15); only a fake that
    # logs the message exists, so a real gateway is a new value here.
    IDENTITY_SMS_PROVIDER: Literal["log"] = "log"
    IDENTITY_EMAIL_PROVIDER: Literal["log"] = "log"
    IDENTITY_OTP_TTL_MINUTES: int = Field(default=10, ge=1)
    IDENTITY_INVITE_TTL_HOURS: int = Field(default=72, ge=1)
    IDENTITY_OTP_MAX_FAILED_ATTEMPTS: int = Field(default=5, ge=1)
    IDENTITY_OTP_RESEND_COOLDOWN_SECONDS: int = Field(default=60, ge=0)
    IDENTITY_OTP_MAX_PER_PHONE_PER_DAY: int = Field(default=10, ge=1)
    IDENTITY_AUDIT_LOG_MAX_RANGE_DAYS: int = Field(default=366, ge=1)
    IDENTITY_EMERGENCY_ADMIN_PHONE: str | None = None

    # Read only by `python -m app.domains.identity.bootstrap` (no endpoint can
    # create the first administrator).
    IDENTITY_BOOTSTRAP_ORGANIZATION_NAME: str = "G3 Network"
    IDENTITY_BOOTSTRAP_ORGANIZATION_LEGAL_NAME: str = "G3 Network"
    IDENTITY_BOOTSTRAP_ORGANIZATION_TAX_CODE: str | None = None
    IDENTITY_BOOTSTRAP_ADMIN_PHONE: str | None = None
    IDENTITY_BOOTSTRAP_ADMIN_NAME: str = "Head Administrator"
    IDENTITY_BOOTSTRAP_ADMIN_PASSWORD: str | None = None
    IDENTITY_BOOTSTRAP_EMERGENCY_ADMIN_PASSWORD: str | None = None


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
