"""SQLAlchemy model for the telemetry domain's single table, ``telemetry``.

Feature code: F-A1 (Real-time vehicle telemetry ingestion).

``telemetry`` is a TimescaleDB hypertable (1-day chunks on
``recorded_at``, which is therefore part of the primary key); the
hypertable itself is created by the baseline migration, not by this
model. One row per ingested MQTT message, append-only: ingestion never
updates or deletes a row. The foreign keys to ``telematics`` and
``vehicles`` are the only schema-level coupling to other domains - code in
other domains never imports this model (import-linter contract).
"""

from datetime import datetime
from uuid import UUID

from geoalchemy2 import Geography
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    BigInteger,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class TelemetryModel(Base):
    """Vehicle telemetry data model for time-series storage.

    Stores real-time telemetry data from telematic devices installed on vehicles.
    Uses TimescaleDB hypertable for efficient time-series queries.

    Attributes:
        message_id: BIGINT primary key (auto-generated)
        organization_id: The organization that owned the vehicle at
            ``recorded_at`` (DM-24 case C): written once at insert, never
            updated, so the sample stays with that owner after a sale (VH-11).
        device_message_id: ID the device gave the message (not unique, TM-06)
        telematic_id: UUID foreign key to telematics table
        vehicle_id: UUID foreign key to vehicles table
        recorded_at: Timestamp when telematic recorded the data
        received_at: Timestamp when backend received the message
        location: GPS location as a PostGIS geography point (SRID 4326).
            No spatial index (unlike `charging_stations.location`) - this is
            a high-frequency hypertable write path and nothing currently
            runs a spatial query against it; add one if/when that changes.
        speed_kmh: Vehicle speed in km/h
        heading_degrees: Direction of travel in degrees (0-360), nullable
        soc_percent: State of Charge percentage (0-100)
        battery_voltage_v: Battery voltage in volts, nullable
        battery_current_a: Battery current in amperes, nullable
        battery_temperature_celsius: Battery temperature in °C, nullable
        soh_percent: Battery State of Health, remaining capacity vs. new
            (0-100), nullable (F-A3)
        cycle_count: Charge/discharge cycle count, nullable (F-A3)
        motor_temperature_celsius: Motor temperature in °C, nullable
        odometer_km: Total distance traveled in km, nullable
        signal_dbm: Cellular signal strength in dBm, nullable
        error_codes: JSONB array of error codes, nullable
        raw_payload: Original JSON payload from telematic (for debug/reprocessing)
        schema_version: Version of the MQTT message contract the device
            used (F-A1). Defaults to 1 for devices/records that predate
            this field.
    """

    __tablename__ = "telemetry"

    # Primary key - BIGINT for TimescaleDB performance
    # Must include recorded_at for partition key
    message_id: Mapped[int] = mapped_column(
        BigInteger(),
        primary_key=True,
        autoincrement=True,
    )

    # Owner of the vehicle at recorded_at (DM-24 case C), written once.
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ID the telematic device gave the message (external identifier, TM-18)
    device_message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Foreign key to telematics
    telematic_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telematics.telematic_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Foreign key to vehicles
    vehicle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Timestamps
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        primary_key=True,  # Part of composite PK for TimescaleDB
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # GPS data - stored as PostGIS geography, no spatial index (see class
    # docstring). spatial_index=False also avoids GeoAlchemy2's automatic
    # DDL hook creating an unwanted index of its own.
    location: Mapped[WKBElement] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )

    # Motion data
    speed_kmh: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # km/h, nullable because not every telematic device provides it
    heading_degrees: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # degrees 0-360

    # Battery data
    soc_percent: Mapped[float] = mapped_column(
        Double(), nullable=False
    )  # State of Charge %
    battery_voltage_v: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # V
    battery_current_a: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # A
    battery_temperature_celsius: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # °C
    soh_percent: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # State of Health %, F-A3
    cycle_count: Mapped[int | None] = mapped_column(
        Integer(), nullable=True
    )  # charge/discharge cycles, F-A3

    # Motor data
    motor_temperature_celsius: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # °C

    # Vehicle data
    odometer_km: Mapped[float | None] = mapped_column(Double(), nullable=True)  # km

    # Signal quality
    signal_dbm: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)  # dBm

    # Error codes (JSONB array)
    error_codes: Mapped[dict[str, list[str]] | None] = mapped_column(
        JSONB(), nullable=True
    )

    # Raw payload for debugging and reprocessing
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSONB(), nullable=False)

    # Version of the MQTT message contract the sending device used (F-A1).
    schema_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)

    # Table constraints and indexes
    __table_args__ = (
        # Unique constraint: one message per telematic per timestamp
        UniqueConstraint(
            "telematic_id", "recorded_at", name="uq_telemetry_telematic_recorded_at"
        ),
        # Index for querying by vehicle with time ordering
        Index(
            "ix_telemetry_vehicle_time",
            "vehicle_id",
            literal_column("recorded_at DESC"),
        ),
        # "When did the backend last hear from this vehicle" (F-J1/F-J3
        # device-health monitor): ordered by the backend's receive clock,
        # which a device with a skewed clock cannot move.
        Index(
            "ix_telemetry_vehicle_received",
            "vehicle_id",
            literal_column("received_at DESC"),
        ),
    )

    def __repr__(self) -> str:
        """Return a concise debug representation of the telemetry record."""
        return f"<TelemetryModel {self.vehicle_id} @ {self.recorded_at}>"
