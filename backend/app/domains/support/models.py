"""SQLAlchemy ORM model for the support_cases table in the support domain."""

from datetime import datetime
from uuid import UUID, uuid4

from geoalchemy2 import Geography
from geoalchemy2.elements import WKBElement
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.support.types import (
    SupportCaseCategory,
    SupportCaseChannel,
    SupportCaseStatus,
    SupportCaseType,
)
from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class SupportCaseModel(Base):
    """A support case: an in-app ticket (F-I1) or an SOS report (F-I2).

    Attributes:
        case_id: Primary key (UUID).
        organization_id: The customer organization the case belongs to: the
            vehicle's owner when a VIN resolved, otherwise the one the caller
            named; nullable for a ticket with neither.
        case_type: Whether this is an ordinary ticket or an SOS report.
        category: Business categorization of the case.
        channel: Where the case originated.
        status: Current lifecycle status.
        vehicle_id: The vehicle this case concerns, if resolved from a
            VIN at creation. `ondelete=RESTRICT` - vehicles are only ever
            soft-deleted in this backend, never hard-deleted.
        driver_id: The driver who raised the case, if supplied and
            resolved. Same `RESTRICT` reasoning.
        vin: VIN snapshot at case-creation time, kept even if the vehicle
            is later soft-deleted or its VIN changes.
        error_code: Device/vehicle error code active at case-creation
            time, if any, client-supplied.
        location: GPS location at case-creation time (client-supplied,
            e.g. an SOS's device GPS fix), nullable. No GIST index - this
            is only ever an input snapshot, never a search target (F-I4's
            nearest-partner routing, which would justify one, is
            deferred).
        subject: Short subject line. Required for a ticket; auto-filled
            for an SOS (a button tap has no free-text subject).
        description: Free-text details, optional.
        sla_response_minutes: The response-SLA minutes value in effect
            when this case was created, copied from config onto the row
            so a later config change never rewrites history.
        response_due_at: `created_at + sla_response_minutes`.
        first_responded_at: When the case first left OPEN, nullable.
        resolved_at: When the case reached RESOLVED, nullable.
        closed_at: When the case reached CLOSED, nullable.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete timestamp.
    """

    __tablename__ = "support_cases"

    case_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=True,
    )
    case_type: Mapped[SupportCaseType] = mapped_column(
        SQLEnum(SupportCaseType), nullable=False
    )
    category: Mapped[SupportCaseCategory] = mapped_column(
        SQLEnum(SupportCaseCategory), nullable=False
    )
    channel: Mapped[SupportCaseChannel] = mapped_column(
        SQLEnum(SupportCaseChannel), default=SupportCaseChannel.IN_APP, nullable=False
    )
    status: Mapped[SupportCaseStatus] = mapped_column(
        SQLEnum(SupportCaseStatus),
        default=SupportCaseStatus.OPEN,
        nullable=False,
        index=True,
    )
    vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    driver_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("drivers.driver_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[WKBElement | None] = mapped_column(
        # spatial_index=False: this column is only ever an input snapshot,
        # never searched - matching telemetry.location's rationale
        # rather than charging_stations.location's GIST-indexed one.
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    sla_response_minutes: Mapped[int] = mapped_column(nullable=False)
    response_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    first_responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_support_cases_status_created_at", "status", "created_at"),
        Index("ix_support_cases_vehicle_created_at", "vehicle_id", "created_at"),
        Index("ix_support_cases_deleted_at", "deleted_at"),
        # Partial index (this domain's use of the pattern established by
        # driver_vehicle_assignments): only cases still awaiting a first
        # response have a meaningful "is this breaching" query target.
        Index(
            "ix_support_cases_response_due_pending",
            "response_due_at",
            postgresql_where=text("first_responded_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging a support case record."""
        return f"<SupportCaseModel {self.case_type} ({self.status})>"
