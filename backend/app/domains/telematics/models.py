"""SQLAlchemy models of the telematics domain: devices and their status reports.

``telematics`` has change history (``@tracked *``): its ``telematic_history``
table and trigger are created by the baseline migration
(``app.libs.db.history_ddl``) and are not modelled. ``telematic_status_reports``
is append-only (TX-10): rows are inserted once and never edited.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.telematics.types import TelematicStatus
from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class TelematicModel(Base):
    """Physical Telematic device (T-Box): what it is and the decisions about it.

    Attributes:
        telematic_id: Internal ID of the device.
        imei: IMEI of the device modem, entered at provisioning; unique among
            devices not deleted; NULL until entered.
        telematic_serial: Serial printed on the device (also its MQTT
            identity); unique among devices not deleted.
        organization_id: The organization that owns the device now (TX-07).
        acquired_at: When the current owner took the device (DM-22).
        vehicle_id: ID of the vehicle it is mounted on now, NULL when in
            stock or removed; at most one device per vehicle.
        installed_at: When it was mounted on the current vehicle, NULL when
            not mounted (DM-22).
        status: Status set by a person (DM-25).
        status_reason: Why the device is in its status; NULL when ACTIVE.
        created_at: Creation time.
        updated_at: Last update time, refreshed by the ORM ``onupdate`` hook
            on every flushed UPDATE of the row.
        deleted_at: Soft-delete timestamp (DM-25): the device left the system
            or was entered by mistake; NULL while it is part of the system.
    """

    __tablename__ = "telematics"

    telematic_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    imei: Mapped[str | None] = mapped_column(String(15), nullable=True)
    telematic_serial: Mapped[str] = mapped_column(String(50), nullable=False)
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[TelematicStatus] = mapped_column(
        SQLEnum(TelematicStatus), nullable=False, index=True
    )
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Business values are unique among devices not deleted, so a deleted
    # device (kept as history) never blocks reusing its serial/IMEI or truck.
    __table_args__ = (
        Index(
            "ix_telematics_telematic_serial",
            "telematic_serial",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_telematics_imei",
            "imei",
            unique=True,
            postgresql_where=text("imei IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_telematics_active_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("vehicle_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint(
            "deleted_at IS NULL OR (status = 'INACTIVE' AND vehicle_id IS NULL)",
            name="ck_telematics_deleted_inactive_unmounted",
        ),
    )


class TelematicStatusReportModel(Base):
    """One health report a device sent about itself (TX-10, TX-11).

    Append-only; the device's current health is its newest report. The report
    fields are provisional until the vendor confirms the message
    (mqtt-spec.md section 2.2).

    Attributes:
        telematic_status_report_id: Auto-increasing ID of the report.
        telematic_id: Device that sent the report.
        firmware_version: Firmware version reported.
        telemetry_interval_seconds: Publish interval the device said it uses.
        sim_iccid: ICCID of the SIM in the device.
        is_esim: Whether the SIM is an eSIM.
        sim_data_status: Mobile data status.
        supply_voltage_v: Power supply voltage at the device, in volts.
        signal_dbm: Mobile signal strength in dBm.
        storage_used_percent: Share of the device storage in use, 0-100.
        gnss_status: Satellite positioning status.
        reported_at: When the device produced the report (UTC).
        received_at: When the backend received it.
    """

    __tablename__ = "telematic_status_reports"

    telematic_status_report_id: Mapped[int] = mapped_column(
        BigInteger(), primary_key=True, autoincrement=True
    )
    telematic_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telematics.telematic_id", ondelete="RESTRICT"),
        nullable=False,
    )
    firmware_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    telemetry_interval_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    sim_iccid: Mapped[str | None] = mapped_column(String(22), nullable=True)
    is_esim: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sim_data_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    supply_voltage_v: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    signal_dbm: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    storage_used_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    gnss_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index(
            "ix_telematic_status_reports_device_time",
            "telematic_id",
            "reported_at",
        ),
    )
