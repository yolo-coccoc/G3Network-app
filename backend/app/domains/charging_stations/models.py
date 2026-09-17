"""Minimal SQLAlchemy models for charging station topology.

The module describes the three active topology tables of the ideal MVP.
Stations, EVSEs, and connectors are pre-provisioned. ``ChargingStationModel``
also carries directory/descriptive metadata (location, power rating,
connector standard, operating hours, maintenance status) per F-C1, and
``ChargingConnectorModel`` carries a live OCPP-reported status per F-C2.
Heartbeat-based online/offline connection status, administrative status,
capability negotiation, and other device metadata are still not part of
this step's persistence contract — see ``docs/01-requirements/future.md``
items 27 and 28.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from geoalchemy2 import Geography
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    CheckConstraint,
    DateTime,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingStationMaintenanceStatus,
)
from app.libs.db.base import Base


def utc_now() -> datetime:
    """Get the UTC timestamp used for defaults and soft-delete timestamps.

    Returns:
        The current time as a timezone-aware UTC ``datetime``.
    """
    return datetime.now(timezone.utc)


def enum_values(enum_type: type[object]) -> list[str]:
    """Get the enum values so PostgreSQL stores the correct public contract.

    Duplicated locally rather than imported from ``charging_sessions`` —
    importing another domain's ``models.py`` is forbidden by
    ``domain-boundaries.md``; every domain that needs this duplicates it,
    the same way ``utc_now()`` above is duplicated per domain.

    Args:
        enum_type: An enum whose members carry a ``value`` attribute.

    Returns:
        The list of values in the enum's declaration order.
    """
    return [member.value for member in enum_type]  # type: ignore[attr-defined]


class ChargingStationModel(Base):
    """Record of a station pre-provisioned in the MVP.

    Attributes:
        station_id: Internal UUID.
        ocpp_identity: Identity that appears in the OCPP WebSocket path.
        display_name: Display name.
        location: GPS location as a PostGIS geography point (SRID 4326),
            nullable. Stored as geography (not plain lat/lon columns, unlike
            ``vehicle_telemetry``) since this is descriptive directory data
            rather than a high-frequency telemetry stream.
        power_rating_kw: Nominal power rating of the station in kW, nullable.
            A simple station-level aggregate, not modeled per EVSE/connector.
        connector_standard: Connector standard served by the station (e.g.
            ``"CCS2"``), nullable.
        operating_hours: Freeform description of operating hours (e.g.
            ``"24/7"``), nullable.
        maintenance_status: Admin-set maintenance state; defaults to
            ``OPERATIONAL``.
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
    location: Mapped[WKBElement | None] = mapped_column(
        # spatial_index=False: the GIST index is created explicitly by the
        # migration (ix_charging_stations_location) instead of relying on
        # GeoAlchemy2's automatic DDL hook, matching how every other index
        # in this codebase is explicit.
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    power_rating_kw: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), nullable=True
    )
    connector_standard: Mapped[str | None] = mapped_column(String(20), nullable=True)
    operating_hours: Mapped[str | None] = mapped_column(String(100), nullable=True)
    maintenance_status: Mapped[ChargingStationMaintenanceStatus] = mapped_column(
        SQLEnum(ChargingStationMaintenanceStatus),
        nullable=False,
        default=ChargingStationMaintenanceStatus.OPERATIONAL,
    )
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
        Index("ix_charging_stations_location", "location", postgresql_using="gist"),
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
        status: Live status last reported via OCPP ``StatusNotification``
            (F-C2), nullable — a pre-provisioned connector that hasn't
            reported yet has no status.
        status_updated_at: Time the last ``StatusNotification`` was
            processed, nullable. No out-of-order guard — in-order message
            arrival is this MVP's existing assumption (see
            ``docs/01-requirements/future.md`` item 27).
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
    status: Mapped[ChargingConnectorStatus | None] = mapped_column(
        SQLEnum(
            ChargingConnectorStatus,
            name="chargingconnectorstatus",
            values_callable=enum_values,
        ),
        nullable=True,
    )
    status_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
