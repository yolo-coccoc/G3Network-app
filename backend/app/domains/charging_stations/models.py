"""SQLAlchemy models for charging locations, stations, their topology and OCPP data.

Tables (all in the ``charging_stations`` group of the DBML):

* ``ChargingLocationModel`` and ``ChargingLocationAccessModel``: the place
  drivers go to (owned by one organization, public or private) and the
  organizations allowed to charge at a private one (CS-09, CS-10).
* ``ChargingStationModel``, ``ChargingEvseModel``, ``ChargingConnectorModel``:
  the pre-provisioned profile of a charger and its topology (OCPP never creates
  it). A charger reads its owner through its location. All three have change
  history (``@tracked *``; the history tables are built by the baseline
  migration and are not models).
* ``ChargingStationStateModel`` and ``ChargingConnectorStateModel``: what the
  charger reports about itself (boot identity, liveness, statuses), latest
  values only, written by the OCPP gateway (DM-16). Whether a charger is online
  is computed from ``last_seen_at`` at read time.
* ``ChargingOcppMessageModel``: the verbatim, append-only log of every OCPP
  frame exchanged with a station (both protocols; a hypertable).
* ``ChargingStationCommandModel``: every command sent to a charger and its
  answer, also the channel between the API and the gateway process (CS-20,
  PR-16).
* ``ChargingStationConfigurationCaptureModel`` and
  ``ChargingStationConfigurationEntryModel``: append-only snapshots of a
  charger's settings, each from one ``GET_CONFIGURATION`` command (CS-19, CS-21).

Still deferred: capability negotiation and stale-status handling
(``docs/decisions/deferred.md`` items 28 and 76).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid4

from geoalchemy2 import Geography
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.charging_stations.types import OcppMessageDirection
from app.libs.common.clock import utc_now
from app.libs.db.base import Base
from app.libs.db.enums import enum_values

# Width of the columns holding a negotiated WebSocket subprotocol
# (``charging_ocpp_messages.ocpp_subprotocol`` and
# ``charging_station_state.ocpp_protocol_version``); the OCPP state service
# validates against it before writing.
OCPP_SUBPROTOCOL_MAX_LENGTH: Final[int] = 20


class ChargingLocationModel(Base):
    """One place drivers go to charge: several chargers in a row (CS-09).

    Attributes:
        location_id: Internal UUID.
        organization_id: The owning organization (CS-10); chargers read their
            owner through the location.
        display_name: Name shown to drivers; not unique (CS-12).
        address: Address as one free-text line.
        coordinates: Map pin as a PostGIS geography point (SRID 4326), required.
        is_public: ``True``: anyone may charge and the location is on the
            public map; ``False``: only members of the owner and of the
            organizations in ``charging_location_access``.
        status: ``ACTIVE`` or ``INACTIVE``, set by a person (DM-25).
        status_reason: Why the location has its status; ``NULL`` when ACTIVE.
        created_at: Time the record was created.
        updated_at: Time the record was last edited.
        deleted_at: Soft-delete time; a deleted location is ``INACTIVE``.
    """

    __tablename__ = "charging_locations"

    location_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    coordinates: Mapped[WKBElement] = mapped_column(
        # spatial_index=False: the GIST index is created explicitly below
        # (ix_charging_locations_coordinates), like every other index here.
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
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
            "deleted_at IS NULL OR status = 'INACTIVE'",
            name="ck_charging_locations_deleted_is_inactive",
        ),
        Index(
            "ix_charging_locations_coordinates",
            "coordinates",
            postgresql_using="gist",
        ),
        Index("ix_charging_locations_organization_id", "organization_id"),
    )


class ChargingLocationAccessModel(Base):
    """A grant letting another organization charge at a private location (CS-10).

    An open/close row: it is only ever closed (revoked), never edited, so there
    is no change history.

    Attributes:
        access_id: Internal UUID.
        location_id: The private location.
        allowed_organization_id: The grantee organization (not the owner).
        granted_at: When the access was granted.
        granted_by: User who granted it.
        valid_until: Last valid day (inclusive, Vietnam time), ``NULL`` for no
            end date.
        revoked_at: When the access ended; ``NULL`` while in force.
        revoked_by: Who revoked it; ``NULL`` while in force or when the
            system ended it.
        revoke_reason: Why it ended; ``NULL`` while in force.
    """

    __tablename__ = "charging_location_access"

    access_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    location_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_locations.location_id", ondelete="RESTRICT"),
        nullable=False,
    )
    allowed_organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    granted_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        Index(
            "uq_charging_location_access_live",
            "location_id",
            "allowed_organization_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_charging_location_access_allowed_organization_id",
            "allowed_organization_id",
        ),
    )


class ChargingStationModel(Base):
    """A pre-provisioned charger (trụ sạc): profile and decisions only (CS-09).

    What the charger reports about itself lives in ``ChargingStationStateModel``.

    Attributes:
        station_id: Internal UUID.
        location_id: The location the charger stands at; it reads its owner
            through it.
        ocpp_identity: Identity that appears in the OCPP WebSocket path; unique
            and never reused, even after a soft delete.
        registered_serial_number: Serial read from the nameplate at
            installation (CS-14); unique among chargers not deleted.
        physical_reference: Short label printed on the unit, nullable.
        max_power_kw: Total output shared by the guns, nullable.
        status: ``ACTIVE`` or ``INACTIVE``, set by a person (DM-25).
        status_reason: Why the charger has its status; ``NULL`` when ACTIVE.
        created_at: Time the record was created.
        updated_at: Time of the last edit by a person.
        deleted_at: Soft-delete time; a deleted charger is ``INACTIVE``.
    """

    __tablename__ = "charging_stations"

    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    location_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_locations.location_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ocpp_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    registered_serial_number: Mapped[str] = mapped_column(String(100), nullable=False)
    physical_reference: Mapped[str | None] = mapped_column(String(16), nullable=True)
    max_power_kw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
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
        CheckConstraint(
            "deleted_at IS NULL OR status = 'INACTIVE'",
            name="ck_charging_stations_deleted_is_inactive",
        ),
        Index("ix_charging_stations_deleted_at", "deleted_at"),
        Index("ix_charging_stations_location_id", "location_id"),
        Index(
            "uq_charging_stations_live_registered_serial_number",
            "registered_serial_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class ChargingStationStateModel(Base):
    """What a charger reports about itself: latest values only (DM-16, CS-15).

    One row per charger (1:1), created with it and written by the OCPP gateway.
    The history of boots, firmware and status is the raw frame log.

    Attributes:
        station_id: The charger (primary key and foreign key).
        last_seen_at: Time of the latest frame of any kind; ``is_online`` is
            computed from it at read time. ``NULL`` before the first connection.
        last_boot_at: Time of the latest accepted ``BootNotification``.
        ocpp_protocol_version: Subprotocol of the latest connection.
        vendor: Vendor reported at boot.
        model: Model reported at boot.
        serial_number: Serial reported at boot (compared with the registered
            serial, CS-14).
        firmware_version: Firmware reported at the latest boot; a change is
            logged.
        charger_status: Status of the whole charger (1.6J connector ``0``; the
            2.0.1 adapter fills it from its own messages), stored as the
            protocol label.
        charger_status_updated_at: When that status was reported.
        charger_error_code: Error code of the whole charger, as sent.
        charger_vendor_error_code: Vendor error code of the whole charger.
    """

    __tablename__ = "charging_station_state"

    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_boot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ocpp_protocol_version: Mapped[str | None] = mapped_column(
        String(OCPP_SUBPROTOCOL_MAX_LENGTH), nullable=True
    )
    vendor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    charger_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    charger_status_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    charger_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    charger_vendor_error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )


class ChargingEvseModel(Base):
    """An EVSE (outlet charging one truck at a time) of a pre-provisioned charger.

    Attributes:
        evse_id: Internal UUID.
        station_id: UUID of the charger that owns the EVSE.
        ocpp_evse_id: EVSE ID used by OCPP, positive, unique within the charger.
        emi3_evse_id: Public eMI3 ID (``VN*G3N*E0001A``, CS-16); unique among
            EVSEs not deleted and taken over by a replacement EVSE.
        status: ``ACTIVE`` or ``INACTIVE``, set by a person (DM-25).
        status_reason: Why the EVSE is out of service; ``NULL`` when ACTIVE.
        created_at: Time the record was created.
        updated_at: Time the record was last updated.
        deleted_at: Soft-delete time; a deleted EVSE is ``INACTIVE``.
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
    emi3_evse_id: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
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
        CheckConstraint(
            "deleted_at IS NULL OR status = 'INACTIVE'",
            name="ck_charging_evses_deleted_is_inactive",
        ),
        UniqueConstraint(
            "station_id", "ocpp_evse_id", name="uq_charging_evses_station_ocpp_id"
        ),
        Index("ix_charging_evses_station_deleted", "station_id", "deleted_at"),
        Index(
            "uq_charging_evses_live_emi3_evse_id",
            "emi3_evse_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class ChargingConnectorModel(Base):
    """A physical connector (gun): its plug standard and power (CS-17).

    Attributes:
        connector_id: Internal UUID.
        evse_id: UUID of the EVSE that owns the connector.
        ocpp_connector_id: Connector ID used by OCPP, positive.
        standard: Plug standard (``ConnectorStandard`` value), entered by a
            person from the nameplate.
        max_power_kw: Highest power the gun can deliver, in kW.
        max_voltage_v: Highest output voltage, in volts.
        max_current_a: Highest output current, in amperes.
        created_at: Time the record was created.
        updated_at: Time the record was last edited by a person.
        deleted_at: Soft-delete time, nullable.

    Invariants:
        ``ocpp_connector_id`` is only unique within its parent EVSE and must
        be a positive number. The live status the charger reports is in
        ``ChargingConnectorStateModel``.
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
    standard: Mapped[str] = mapped_column(String(30), nullable=False)
    max_power_kw: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    max_voltage_v: Mapped[int] = mapped_column(Integer, nullable=False)
    max_current_a: Mapped[int] = mapped_column(Integer, nullable=False)
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


class ChargingConnectorStateModel(Base):
    """The live status of one gun as the charger reports it (DM-16).

    One row per connector (1:1), created with it; latest values only.

    Attributes:
        connector_id: The gun (primary key and foreign key).
        status: Last ``StatusNotification`` status as the protocol label
            (``ChargingConnectorStatus`` value); ``NULL`` before the first
            report.
        status_updated_at: When that status was reported.
        error_code: ``errorCode`` of the latest report, as sent (replaced by
            every report).
        vendor_error_code: ``vendorErrorCode`` of the latest report.
        status_info: Free-text ``info`` of the latest report.
    """

    __tablename__ = "charging_connector_state"

    connector_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_connectors.connector_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vendor_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status_info: Mapped[str | None] = mapped_column(String(50), nullable=True)


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
        action: Message type copied from the envelope of a request, ``NULL``
            for answers, errors and unreadable frames (CS-18).
        ocpp_message_id: The frame's own message ID from the envelope, shared
            by a request and its answer; ``NULL`` for an unreadable frame.
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
    action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ocpp_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    __table_args__ = (
        Index("ix_charging_ocpp_messages_station_time", "station_id", "occurred_at"),
        Index(
            "ix_charging_ocpp_messages_station_action_time",
            "station_id",
            "action",
            "occurred_at",
        ),
        Index(
            "ix_charging_ocpp_messages_station_message_id",
            "station_id",
            "ocpp_message_id",
        ),
    )


class ChargingStationCommandModel(Base):
    """A command sent to a charger and its answer (CS-20, PR-16).

    Also the channel between the API and the gateway process: the API inserts
    a ``PENDING`` row with no ``ocpp_message_id`` (queued); the gateway that
    holds the charger's connection claims it, sends the OCPP call (setting
    ``ocpp_message_id``) and writes the answer back. Written when created and
    updated once with the answer; no change history, no soft delete.

    Attributes:
        command_id: Internal UUID.
        station_id: The charger the command goes to.
        evse_id: The gun the command targets, ``NULL`` for the whole charger.
        session_id: The session a remote start begins or a remote stop ends.
        command_type: ``StationCommandType`` value.
        parameters: What was sent besides the links (reset type, setting key
            and value, ...); never the session's token.
        requested_by: User who asked; ``NULL`` when the system sent it.
        reason: Why, typed by the operator for a manual command.
        requested_at: When the command was created (server clock).
        ocpp_message_id: Message ID of the frame sent; ``NULL`` while queued
            or when nothing was sent.
        outcome: ``StationCommandOutcome`` value.
        response_status: The charger's answer as sent.
        answered_at: When the answer or the timeout was recorded.
    """

    __tablename__ = "charging_station_commands"

    command_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
        nullable=False,
    )
    evse_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_evses.evse_id", ondelete="RESTRICT"),
        nullable=True,
    )
    session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_sessions.session_id", ondelete="RESTRICT"),
        nullable=True,
    )
    command_type: Mapped[str] = mapped_column(String(30), nullable=False)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    requested_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    ocpp_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    response_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "outcome <> 'NOT_SENT' OR ocpp_message_id IS NULL",
            name="ck_charging_station_commands_not_sent_no_message",
        ),
        CheckConstraint(
            "outcome IN ('PENDING', 'NOT_SENT') OR answered_at IS NOT NULL",
            name="ck_charging_station_commands_answered_has_time",
        ),
        Index(
            "ix_charging_station_commands_station_requested",
            "station_id",
            "requested_at",
        ),
        Index("ix_charging_station_commands_session_id", "session_id"),
    )


class ChargingStationConfigurationCaptureModel(Base):
    """One snapshot of a charger's settings, from one command (CS-19, CS-21).

    Append-only. The charger, the request time and who asked are read from the
    command. The current settings of a charger are its newest ``COMPLETE``
    snapshot.

    Attributes:
        capture_id: Internal UUID.
        command_id: The ``GET_CONFIGURATION`` command that asked for it
            (unique: one command gives one snapshot).
        reason: ``ConfigurationCaptureReason`` value.
        ocpp_protocol_version: Protocol of the connection the snapshot came over.
        ocpp_request_id: OCPP 2.0.1 only: the ``requestId`` of
            ``GetBaseReport``; ``NULL`` for 1.6J.
        captured_at: When the answer (2.0.1: its last part) arrived; ``NULL``
            while waiting or when it failed.
        outcome: ``ConfigurationCaptureOutcome`` value.
    """

    __tablename__ = "charging_station_configuration_captures"

    capture_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    command_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_station_commands.command_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    ocpp_protocol_version: Mapped[str] = mapped_column(String(20), nullable=False)
    ocpp_request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "command_id", name="uq_charging_station_configuration_captures_command_id"
        ),
        Index("ix_charging_config_captures_ocpp_request_id", "ocpp_request_id"),
    )


class ChargingStationConfigurationEntryModel(Base):
    """One value of one setting in one snapshot (CS-19).

    Named the OCPP 2.0.1 way (component + variable + attribute type), which
    also holds 1.6J: a 1.6J key is a variable with no component, attribute
    ``Actual``, and ``readonly`` mapped to ``mutability``. Append-only.

    Attributes:
        entry_id: Internal UUID.
        capture_id: The snapshot this value belongs to.
        component_name: OCPP 2.0.1 only: the component; ``NULL`` for 1.6J.
        component_instance: OCPP 2.0.1 only: instance of the component.
        ocpp_evse_id: OCPP 2.0.1 only: EVSE the component sits on.
        ocpp_connector_id: OCPP 2.0.1 only: connector the component sits on.
        variable_name: The 1.6J configuration key or the 2.0.1 variable.
        variable_instance: OCPP 2.0.1 only: instance of the variable.
        attribute_type: ``Actual`` (always for 1.6J), ``Target``, ``MinSet``
            or ``MaxSet``.
        value: The value as text, as the charger sent it; ``NULL`` if none.
        mutability: ``ConfigurationMutability`` value.
    """

    __tablename__ = "charging_station_configuration_entries"

    entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    capture_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "charging_station_configuration_captures.capture_id", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    component_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    component_instance: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ocpp_evse_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocpp_connector_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variable_name: Mapped[str] = mapped_column(String(100), nullable=False)
    variable_instance: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attribute_type: Mapped[str] = mapped_column(String(10), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    mutability: Mapped[str] = mapped_column(String(10), nullable=False)

    __table_args__ = (
        Index(
            "uq_charging_config_entries_capture_setting",
            "capture_id",
            "component_name",
            "component_instance",
            "ocpp_evse_id",
            "ocpp_connector_id",
            "variable_name",
            "variable_instance",
            "attribute_type",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )
