"""Minimal SQLAlchemy models for charging station topology.

The module only describes the three active topology tables of the ideal MVP.
Stations, EVSEs, and connectors are pre-provisioned; technical status,
capability, and device metadata are not part of this step's persistence
contract.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.libs.db.base import Base


def utc_now() -> datetime:
    """Get the UTC timestamp used for defaults and soft-delete timestamps.

    Returns:
        The current time as a timezone-aware UTC ``datetime``.
    """
    return datetime.now(timezone.utc)


class ChargingStationModel(Base):
    """Record of a station pre-provisioned in the MVP.

    Attributes:
        station_id: Internal UUID.
        ocpp_identity: Identity that appears in the OCPP WebSocket path.
        display_name: Display name.
        created_at: Time the record was created.
        updated_at: Time the record was last updated.
        deleted_at: Soft-delete time, nullable.

    Invariants:
        ``ocpp_identity`` is a unique business identity and must not be
        reused after a soft-delete.
    """

    __tablename__ = "charging_stations"

    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    ocpp_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("ocpp_identity", name="uq_charging_stations_ocpp_identity"),
        Index("ix_charging_stations_deleted_at", "deleted_at"),
    )


class ChargingEvseModel(Base):
    """An EVSE belonging to a pre-provisioned station.

    Attributes:
        evse_id: Internal UUID.
        station_id: UUID of the station that owns the EVSE.
        ocpp_evse_id: EVSE ID used by OCPP, positive.
        created_at: Time the record was created.
        updated_at: Time the record was last updated.
        deleted_at: Soft-delete time, nullable.

    Invariants:
        ``ocpp_evse_id`` is only unique within its parent station and must be
        a positive number.
    """

    __tablename__ = "charging_evses"

    evse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ocpp_evse_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("ocpp_evse_id > 0", name="ck_charging_evses_ocpp_id_positive"),
        UniqueConstraint(
            "station_id", "ocpp_evse_id", name="uq_charging_evses_station_ocpp_id"
        ),
        Index("ix_charging_evses_station_deleted", "station_id", "deleted_at"),
    )


class ChargingConnectorModel(Base):
    """A physical connector belonging to a pre-provisioned EVSE.

    Attributes:
        connector_id: Internal UUID.
        evse_id: UUID of the EVSE that owns the connector.
        ocpp_connector_id: Connector ID used by OCPP, positive.
        created_at: Time the record was created.
        updated_at: Time the record was last updated.
        deleted_at: Soft-delete time, nullable.

    Invariants:
        ``ocpp_connector_id`` is only unique within its parent EVSE and must
        be a positive number.
    """

    __tablename__ = "charging_connectors"

    connector_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    evse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_evses.evse_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ocpp_connector_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "ocpp_connector_id > 0", name="ck_charging_connectors_ocpp_id_positive"
        ),
        UniqueConstraint(
            "evse_id",
            "ocpp_connector_id",
            name="uq_charging_connectors_evse_ocpp_id",
        ),
        Index("ix_charging_connectors_evse_deleted", "evse_id", "deleted_at"),
    )
