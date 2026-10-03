"""SQLAlchemy models for charging stations, their topology and OCPP device state.

Five tables:

* ``ChargingStationModel``, ``ChargingEvseModel``, ``ChargingConnectorModel``:
  the pre-provisioned topology (OCPP never creates it). A station carries its
  directory metadata (location, power rating, connector standard, operating
  hours, maintenance status; F-C1) and the device state its charger reports:
  boot identity (vendor/model/serial/firmware), liveness (``last_seen_at``,
  from which ``is_online`` is derived at read time) and the whole-charger
  status of OCPP 1.6J connector ``0``. A connector carries its live
  ``StatusNotification`` status and error details (F-C2).
* ``ChargingOcppMessageModel``: the verbatim, append-only log of every OCPP
  frame exchanged with a station (both protocols; a hypertable).
* ``ChargingStationConfigurationEntryModel``: append-only ``GetConfiguration``
  captures.

Device-reported columns never bump ``updated_at``, which keeps meaning "last
administrator edit". Still deferred: administrative/technical status history,
capability negotiation and stale-status handling (``docs/product/
deferred.md`` items 27, 28 and 76).
"""

from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

from geoalchemy2 import Geography
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingStationMaintenanceStatus,
    OcppMessageDirection,
)
from app.libs.common.clock import utc_now
from app.libs.db.base import Base
from app.libs.db.enums import enum_values

# Width of the columns holding a negotiated WebSocket subprotocol
# (``charging_ocpp_messages.ocpp_subprotocol`` and
# ``charging_stations.ocpp_protocol_version``); the OCPP state service
# validates against it before writing.
OCPP_SUBPROTOCOL_MAX_LENGTH: Final[int] = 20


class ChargingStationModel(Base):
    """A pre-provisioned station: directory metadata plus charger-reported state.

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
        ocpp_protocol_version: Subprotocol of the station's latest
            connection (``ocpp1.6`` or ``ocpp2.0.1``), nullable until it
            first connects. Reported by the charger's connection, not
            admin-editable.
        vendor: ``BootNotification`` vendor name, nullable until first boot.
        model: ``BootNotification`` model name, nullable until first boot.
        serial_number: Charger serial number from ``BootNotification``,
            nullable.
        firmware_version: Firmware version from the latest
            ``BootNotification``, nullable; a change is logged as a warning
            (baseline for noticing a firmware swap).
        last_boot_at: Time of the latest accepted ``BootNotification``,
            nullable.
        last_seen_at: Time of the latest frame of any kind received from the
            charger, nullable. "Online" is derived from it at read time
            (``CHARGING_OFFLINE_TIMEOUT_SECONDS``), never stored.
        charger_status: OCPP 1.6J connector ``0`` — the status of the whole
            charger, nullable (2.0.1 has no such concept). Kept on the
            station because connector ``0`` has no topology row.
        charger_status_updated_at: Time the last connector-``0`` status was
            processed, nullable.
        charger_error_code: ``errorCode`` reported for connector ``0`` as
            sent (``NoError`` included), nullable.
        charger_vendor_error_code: ``vendorErrorCode`` reported for connector
            ``0``, nullable.
        created_at: Time the record was created.
        updated_at: Time the record was last updated by an admin edit;
            device-reported columns above do not bump it.
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
    ocpp_protocol_version: Mapped[str | None] = mapped_column(
        String(OCPP_SUBPROTOCOL_MAX_LENGTH), nullable=True
    )
    vendor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_boot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    charger_status: Mapped[ChargingConnectorStatus | None] = mapped_column(
        SQLEnum(
            ChargingConnectorStatus,
            name="chargingconnectorstatus",
            values_callable=enum_values,
        ),
        nullable=True,
    )
    charger_status_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    charger_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    charger_vendor_error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
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
            ``docs/decisions/deferred.md`` item 27).
        error_code: ``errorCode`` from the latest ``StatusNotification`` as
            sent (OCPP 1.6J; ``NoError`` included), nullable. Replaced by
            every status update, so it always describes the latest report.
        vendor_error_code: ``vendorErrorCode`` from the latest report,
            nullable.
        status_info: The free-text ``info`` field of the latest report,
            nullable.
        created_at: Time the record was created.
        updated_at: Time the record was last updated by an admin edit; the
            OCPP-reported status columns above do not bump it.
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
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vendor_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status_info: Mapped[str | None] = mapped_column(String(50), nullable=True)
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


class ChargingOcppMessageModel(Base):
    """One OCPP frame exchanged with a station, stored verbatim and append-only.

    The table is the evidence trail for disputes and for discovering how a
    real charger deviates from the OCPP standard: it holds the exact text of
    each frame *before* any parsing, including frames the gateway cannot
    parse or has no handler for. Stored as a TimescaleDB hypertable. There is
    deliberately no read API, and the frames (which can contain RFID
    ``idTag`` values) must not be copied into application logs.

    Attributes:
        message_id: Internal UUID of the log row.
        occurred_at: When the frame was received (inbound) or sent
            (outbound), timezone-aware UTC; also the time partitioning key.
        station_id: The station the frame was exchanged with.
        ocpp_subprotocol: The WebSocket subprotocol negotiated for the
            connection (``ocpp1.6`` or ``ocpp2.0.1``).
        direction: Whether the frame was received from or sent to the
            charge point.
        raw_frame: The exact frame text (a JSON array in OCPP-J), never
            re-serialised.
    """

    __tablename__ = "charging_ocpp_messages"

    message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    # TimescaleDB needs the time column in the primary key to partition the
    # hypertable; message_id keeps rows unique when frames share a timestamp.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ocpp_subprotocol: Mapped[str] = mapped_column(
        String(OCPP_SUBPROTOCOL_MAX_LENGTH), nullable=False
    )
    direction: Mapped[OcppMessageDirection] = mapped_column(
        SQLEnum(
            OcppMessageDirection,
            name="chargingocppmessagedirection",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    raw_frame: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_charging_ocpp_messages_station_time", "station_id", "occurred_at"),
    )


class ChargingStationConfigurationEntryModel(Base):
    """One configuration key of a charger, captured by ``GetConfiguration``.

    Append-only: every boot adds a new capture (all its rows share
    ``capture_id`` and ``captured_at``) and nothing is updated, so the history
    shows whether a charger's settings were changed. The capture of
    ``SupportedFeatureProfiles`` is the charger's real specification.

    Attributes:
        entry_id: Internal UUID of the row.
        station_id: The station the configuration belongs to.
        capture_id: Groups the rows of one capture.
        captured_at: When the charger's answer was received, timezone-aware UTC.
        config_key: The configuration key name.
        value: The key's value as text, nullable.
        is_readonly: Whether the charger reported the key as read-only.
    """

    __tablename__ = "charging_station_configuration_entries"

    entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
        nullable=False,
    )
    capture_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    config_key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_readonly: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        Index(
            "ix_charging_config_entries_station_captured", "station_id", "captured_at"
        ),
    )
