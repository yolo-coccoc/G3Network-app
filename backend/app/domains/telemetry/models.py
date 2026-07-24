"""SQLAlchemy models for Telemetry domain."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import String, DateTime, ForeignKey, Enum as SQLEnum, UniqueConstraint, Index, BigInteger, Double
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
import enum

from app.libs.db.session import Base


class TelematicStatus(str, enum.Enum):
    """Telematic device status enum."""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"


class Telematic(Base):
    """Telematic device model representing hardware installed on vehicles.
    
    Attributes:
        telematic_id: UUID primary key
        telematic_serial: Mã vật lý trên thiết bị (unique)
        vehicle_id: ID xe được gán (nullable, có thể gán sau)
        status: Trạng thái thiết bị
        firmware_version: Phiên bản firmware (nullable)
        last_seen_at: Thời điểm nhận message cuối cùng (nullable)
        created_at: Thời gian tạo
        updated_at: Thời gian cập nhật
    """
    
    __tablename__ = "telematics"
    
    # Primary key - UUID
    telematic_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    
    # Business key - serial number on physical device
    telematic_serial: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )
    
    # Foreign key to vehicles (nullable - can be assigned later)
    vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    # Status
    status: Mapped[TelematicStatus] = mapped_column(
        SQLEnum(TelematicStatus),
        default=TelematicStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    
    # Firmware version (nullable)
    firmware_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    
    # Last seen timestamp (updated when receiving message)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    
    # Table constraints
    __table_args__ = (
        # Each vehicle can have at most 1 telematic device
        UniqueConstraint("vehicle_id", name="uq_telematics_vehicle_id"),
    )
    
    def __repr__(self) -> str:
        return f"<Telematic {self.telematic_serial} ({self.status.value})>"


class VehicleTelemetry(Base):
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
        latitude: GPS latitude coordinate
        longitude: GPS longitude coordinate
        speed: Vehicle speed in km/h
        heading: Direction of travel in degrees (0-360), nullable
        soc: State of Charge percentage (0-100)
        battery_voltage: Battery voltage in volts, nullable
        battery_current: Battery current in amperes, nullable
        battery_temperature: Battery temperature in °C, nullable
        motor_temperature: Motor temperature in °C, nullable
        odometer: Total distance traveled in km, nullable
        signal_strength: Cellular signal strength in dBm, nullable
        error_codes: JSONB array of error codes, nullable
        raw_payload: Original JSON payload from telematic (for debug/reprocessing)
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
        default=datetime.utcnow,
    )
    
    # GPS data
    latitude: Mapped[float] = mapped_column(Double(), nullable=False)
    longitude: Mapped[float] = mapped_column(Double(), nullable=False)
    
    # Motion data
    speed: Mapped[float] = mapped_column(Double(), nullable=False)  # km/h
    heading: Mapped[float | None] = mapped_column(Double(), nullable=True)  # degrees 0-360
    
    # Battery data
    soc: Mapped[float] = mapped_column(Double(), nullable=False)  # State of Charge %
    battery_voltage: Mapped[float | None] = mapped_column(Double(), nullable=True)  # V
    battery_current: Mapped[float | None] = mapped_column(Double(), nullable=True)  # A
    battery_temperature: Mapped[float | None] = mapped_column(Double(), nullable=True)  # °C
    
    # Motor data
    motor_temperature: Mapped[float | None] = mapped_column(Double(), nullable=True)  # °C
    
    # Vehicle data
    odometer: Mapped[float | None] = mapped_column(Double(), nullable=True)  # km
    
    # Signal quality
    signal_strength: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)  # dBm
    
    # Error codes (JSONB array)
    error_codes: Mapped[list | None] = mapped_column(JSONB(), nullable=True)
    
    # Raw payload for debugging and reprocessing
    raw_payload: Mapped[dict] = mapped_column(JSONB(), nullable=False)
    
    # Table constraints and indexes
    __table_args__ = (
        # Unique constraint: one message per telematic per timestamp
        UniqueConstraint("telematic_id", "recorded_at", name="uq_telematic_recorded_at"),
        # Index for querying by vehicle with time ordering
        Index("ix_vehicle_telemetry_vehicle_time", "vehicle_id", recorded_at.desc()),
    )
    
    def __repr__(self) -> str:
        return f"<VehicleTelemetry {self.telematic_serial} @ {self.recorded_at}>"
