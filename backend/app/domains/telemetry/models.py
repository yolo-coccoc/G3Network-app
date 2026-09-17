"""SQLAlchemy models for Telemetry domain."""

from datetime import datetime, timezone
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
    String,
    UniqueConstraint,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.libs.db.base import Base


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class VehicleTelemetryModel(Base):
    """Vehicle telemetry data model for time-series storage.

    Stores real-time telemetry data from telematic devices installed on vehicles.
    Uses TimescaleDB hypertable for efficient time-series queries.

    Attributes:
        message_id: BIGINT primary key (auto-generated)
        message_uuid: UUID created by telematic device
        telematic_id: UUID foreign key to telematics table
        telematic_serial: Serial number stored for audit/debug
        vehicle_id: UUID foreign key to vehicles table
        recorded_at: Timestamp when telematic recorded the data
        received_at: Timestamp when backend received the message
        location: GPS location as a PostGIS geography point (SRID 4326).
            No spatial index (unlike `charging_stations.location`) - this is
            a high-frequency hypertable write path and nothing currently
            runs a spatial query against it; add one if/when that changes.
        speed: Vehicle speed in km/h
        heading: Direction of travel in degrees (0-360), nullable
        soc: State of Charge percentage (0-100)
        battery_voltage: Battery voltage in volts, nullable
        battery_current: Battery current in amperes, nullable
        battery_temperature: Battery temperature in °C, nullable
        soh_percent: Battery State of Health, remaining capacity vs. new
            (0-100), nullable (F-A3)
        cycle_count: Charge/discharge cycle count, nullable (F-A3)
        motor_temperature: Motor temperature in °C, nullable
        odometer: Total distance traveled in km, nullable
        signal_strength: Cellular signal strength in dBm, nullable
        error_codes: JSONB array of error codes, nullable
        raw_payload: Original JSON payload from telematic (for debug/reprocessing)
        schema_version: Version of the MQTT message contract the device
            used (F-A1). Defaults to 1 for devices/records that predate
            this field.
    """

    __tablename__ = "vehicle_telemetry"

    # Primary key - BIGINT for TimescaleDB performance
    # Must include recorded_at for partition key
    message_id: Mapped[int] = mapped_column(
        BigInteger(),
        primary_key=True,
        autoincrement=True,
    )

    # Message UUID from telematic device
    message_uuid: Mapped[UUID] = mapped_column(
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

    # Telematic serial for audit/debug (denormalized)
    telematic_serial: Mapped[str] = mapped_column(
        String(50),
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
    speed: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # km/h, nullable because not every telematic device provides it
    heading: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # degrees 0-360

    # Battery data
    soc: Mapped[float] = mapped_column(Double(), nullable=False)  # State of Charge %
    battery_voltage: Mapped[float | None] = mapped_column(Double(), nullable=True)  # V
    battery_current: Mapped[float | None] = mapped_column(Double(), nullable=True)  # A
    battery_temperature: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # °C
    soh_percent: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # State of Health %, F-A3
    cycle_count: Mapped[int | None] = mapped_column(
        Integer(), nullable=True
    )  # charge/discharge cycles, F-A3

    # Motor data
    motor_temperature: Mapped[float | None] = mapped_column(
        Double(), nullable=True
    )  # °C

    # Vehicle data
    odometer: Mapped[float | None] = mapped_column(Double(), nullable=True)  # km

    # Signal quality
    signal_strength: Mapped[int | None] = mapped_column(
        BigInteger(), nullable=True
    )  # dBm

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
            "telematic_id", "recorded_at", name="uq_telematic_recorded_at"
        ),
        # Index for querying by vehicle with time ordering
        Index(
            "ix_vehicle_telemetry_vehicle_time",
            "vehicle_id",
            literal_column("recorded_at DESC"),
        ),
    )

    def __repr__(self) -> str:
        """Return a concise debug representation of the telemetry record."""
        return f"<VehicleTelemetryModel {self.telematic_serial} @ {self.recorded_at}>"
