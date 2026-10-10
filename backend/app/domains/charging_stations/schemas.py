"""Pydantic request/response schemas of the charging_stations HTTP API.

Locations (``ChargingLocation*``) and their access grants (``ChargingLocation
Access*``) describe the place and who may charge there (CS-09, CS-10). A charger
(``ChargingStation*``) is created at a location and reads its owner through it;
``ChargingStationResponse`` returns the profile plus the read-only state the
charger reports over OCPP (protocol version, boot identity, ``last_seen_at``,
the whole-charger status and the derived ``is_online``) and the read-time
``available_connector_count`` (F-D1). EVSE and connector schemas carry the
public ID, status, plug standard and power entered by a person; the connector
response adds the status the charger reported (F-C2, read-only).
``ChargingStationStatusResponse`` gathers the whole charger's status and every
gun's status in one read. ``NearbyChargingStationResponse`` is the driver-facing
search result (F-D1). The command schemas expose the command channel (CS-20,
PR-16), the configuration schemas the latest ``COMPLETE`` snapshot (CS-19), and
the energy-total schemas are the all-stations F-C5 report.
Capability negotiation and status history remain deferred
(``docs/decisions/deferred.md`` items 27 and 28).
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingResourceStatus,
    ConnectorStandard,
    StationCommandOutcome,
    StationCommandType,
)
from app.libs.common.config import settings


def _strip_required_text(value: str) -> str:
    """Strip required text and reject strings that are blank/whitespace-only.

    Args:
        value: Raw text from the request.

    Returns:
        Text with leading/trailing whitespace stripped.

    Raises:
        ValueError: If the text is empty after normalization.
    """
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value must not be empty or contain only whitespace")
    return normalized


def _strip_optional_text(value: str | None) -> str | None:
    """Strip optional text; ``None`` means the field is not updated.

    Args:
        value: Raw text or ``None`` from the PATCH request.

    Returns:
        Text with whitespace stripped, or ``None``.

    Raises:
        ValueError: If the text is empty after normalization.
    """
    return None if value is None else _strip_required_text(value)


# --- Locations ---------------------------------------------------------------


class ChargingLocationCreateRequest(BaseModel):
    """Data for creating a location (CS-09, CS-12).

    Attributes:
        organization_id: The owning organization (required until
            authentication supplies it, WP2).
        display_name: Name shown to drivers; not unique.
        address: Address as one free-text line.
        latitude: GPS latitude in decimal degrees of the map pin.
        longitude: GPS longitude in decimal degrees of the map pin.
        is_public: ``True``: anyone may charge; ``False``: only the owner's
            members and the organizations with a grant.
    """

    organization_id: UUID
    display_name: str = Field(..., min_length=1, max_length=200)
    address: str = Field(..., min_length=1, max_length=500)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    is_public: bool

    @field_validator("display_name", "address")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """Normalize required text and reject blank strings.

        Args:
            value: Raw text from the request.

        Returns:
            The stripped text.
        """
        return _strip_required_text(value)


class ChargingLocationUpdateRequest(BaseModel):
    """Location fields allowed for partial update (``None`` means unchanged).

    Attributes:
        display_name: New name.
        address: New address line.
        latitude: New latitude; must come with ``longitude``.
        longitude: New longitude; must come with ``latitude``.
        is_public: New visibility.
        status: New status set by a person (DM-25).
        status_reason: Why; required when the status becomes ``INACTIVE``.
    """

    display_name: str | None = Field(None, min_length=1, max_length=200)
    address: str | None = Field(None, min_length=1, max_length=500)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    is_public: bool | None = None
    status: ChargingResourceStatus | None = None
    status_reason: str | None = Field(None, min_length=1, max_length=200)

    @field_validator("display_name", "address", "status_reason")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Normalize optional text.

        Args:
            value: Raw text or ``None``.

        Returns:
            The stripped text, or ``None``.
        """
        return _strip_optional_text(value)

    @model_validator(mode="after")
    def validate_update(self) -> "ChargingLocationUpdateRequest":
        """Require the coordinates together and a reason for ``INACTIVE``.

        Returns:
            The validated request.

        Raises:
            ValueError: If only one coordinate is given, or the status becomes
                ``INACTIVE`` without a reason.
        """
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if self.status is ChargingResourceStatus.INACTIVE and not self.status_reason:
            raise ValueError("status_reason is required when status is INACTIVE")
        return self


class ChargingLocationResponse(BaseModel):
    """A location (trạm sạc): where drivers go to charge.

    Attributes:
        location_id: Internal UUID.
        organization_id: The owning organization.
        display_name: Name shown to drivers.
        address: Address line.
        latitude: GPS latitude of the map pin.
        longitude: GPS longitude of the map pin.
        is_public: Whether anyone may charge there.
        status: ``ACTIVE`` or ``INACTIVE``.
        status_reason: Why, ``None`` when ACTIVE.
        created_at: Time created.
        updated_at: Time of the last edit.
        deleted_at: Soft-delete time, nullable.
    """

    location_id: UUID
    organization_id: UUID
    display_name: str
    address: str
    latitude: float | None
    longitude: float | None
    is_public: bool
    status: ChargingResourceStatus
    status_reason: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ChargingLocationListResponse(BaseModel):
    """Paginated list of locations.

    Attributes:
        items: Active locations on the current page.
        total: Total number of active locations.
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
    """

    items: list[ChargingLocationResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class ChargingLocationAccessCreateRequest(BaseModel):
    """Data for letting another organization charge at a private location (CS-13).

    Attributes:
        allowed_organization_id: The grantee organization (not the owner).
        granted_by: User who grants the access (required until authentication
            supplies it, WP2).
        valid_until: Last valid day, inclusive, Vietnam time; ``None`` for no end.
    """

    allowed_organization_id: UUID
    granted_by: UUID
    valid_until: date | None = None


class ChargingLocationAccessRevokeRequest(BaseModel):
    """Data for revoking a grant.

    Attributes:
        revoked_by: User who revokes it (``None`` until authentication, WP2).
        revoke_reason: Why the access ends.
    """

    revoked_by: UUID | None = None
    revoke_reason: str = Field(..., min_length=1, max_length=200)

    @field_validator("revoke_reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        """Reject a blank reason.

        Args:
            value: Raw text from the request.

        Returns:
            The stripped text.
        """
        return _strip_required_text(value)


class ChargingLocationAccessResponse(BaseModel):
    """A grant of one organization at a private location.

    Attributes:
        access_id: Internal UUID.
        location_id: The private location.
        allowed_organization_id: The grantee organization.
        granted_at: When it was granted.
        granted_by: Who granted it.
        valid_until: Last valid day, nullable.
        revoked_at: When it ended, ``None`` while in force.
        revoked_by: Who revoked it, nullable.
        revoke_reason: Why it ended, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    access_id: UUID
    location_id: UUID
    allowed_organization_id: UUID
    granted_at: datetime
    granted_by: UUID
    valid_until: date | None
    revoked_at: datetime | None
    revoked_by: UUID | None
    revoke_reason: str | None


class ChargingLocationAccessListResponse(BaseModel):
    """The live grants of a location.

    Attributes:
        items: Grants that are not revoked, oldest first.
    """

    items: list[ChargingLocationAccessResponse]


# --- Stations ----------------------------------------------------------------


class ChargingStationCreateRequest(BaseModel):
    """Data for creating a pre-provisioned charger at a location.

    Attributes:
        location_id: The location the charger stands at.
        ocpp_identity: Station identity used in the OCPP WebSocket path.
        registered_serial_number: Serial read from the nameplate (CS-14).
        physical_reference: Label printed on the unit, e.g. ``Trụ 1``.
        max_power_kw: Total output shared by the guns (0 < value <= 9999.99,
            matching the ``Numeric(6, 2)`` column).
    """

    location_id: UUID
    ocpp_identity: str = Field(..., min_length=1, max_length=255)
    registered_serial_number: str = Field(..., min_length=1, max_length=100)
    physical_reference: str | None = Field(None, min_length=1, max_length=16)
    max_power_kw: float | None = Field(None, gt=0, le=9999.99)

    @field_validator("ocpp_identity", "registered_serial_number")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """Normalize required text and reject blank strings.

        Args:
            value: Raw text from the request.

        Returns:
            The stripped text.
        """
        return _strip_required_text(value)


class ChargingStationUpdateRequest(BaseModel):
    """Station fields allowed for partial update (``None`` means unchanged).

    Attributes:
        location_id: Move the charger to another location.
        ocpp_identity: New identity.
        registered_serial_number: New nameplate serial.
        physical_reference: New label.
        max_power_kw: New total output (0 < value <= 9999.99).
        status: New status set by a person (DM-25).
        status_reason: Why; required when the status becomes ``INACTIVE``.
    """

    location_id: UUID | None = None
    ocpp_identity: str | None = Field(None, min_length=1, max_length=255)
    registered_serial_number: str | None = Field(None, min_length=1, max_length=100)
    physical_reference: str | None = Field(None, min_length=1, max_length=16)
    max_power_kw: float | None = Field(None, gt=0, le=9999.99)
    status: ChargingResourceStatus | None = None
    status_reason: str | None = Field(None, min_length=1, max_length=200)

    @field_validator("ocpp_identity", "registered_serial_number", "status_reason")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Normalize optional text.

        Args:
            value: Raw text or ``None``.

        Returns:
            The stripped text, or ``None``.
        """
        return _strip_optional_text(value)

    @model_validator(mode="after")
    def validate_reason(self) -> "ChargingStationUpdateRequest":
        """Require a reason when the charger is taken out of service.

        Returns:
            The validated request.

        Raises:
            ValueError: If the status becomes ``INACTIVE`` without a reason.
        """
        if self.status is ChargingResourceStatus.INACTIVE and not self.status_reason:
            raise ValueError("status_reason is required when status is INACTIVE")
        return self


class ChargingStationResponse(BaseModel):
    """An active charger: profile plus the state it reports.

    Attributes:
        station_id: Internal UUID.
        location_id: The location it stands at.
        organization_id: The owning organization, read through the location.
        location_display_name: Name of the location.
        latitude: GPS latitude of the location, nullable.
        longitude: GPS longitude of the location, nullable.
        ocpp_identity: Unique OCPP identity.
        registered_serial_number: Serial read from the nameplate.
        physical_reference: Label printed on the unit, nullable.
        max_power_kw: Total output shared by the guns, nullable.
        status: ``ACTIVE`` or ``INACTIVE``, set by a person.
        status_reason: Why, ``None`` when ACTIVE.
        connector_count: Number of active connectors across the station's
            active EVSEs, computed at read time (not stored).
        available_connector_count: How many of those connectors last
            reported ``Available``, computed at read time (F-D1).
        ocpp_protocol_version: Subprotocol of the latest connection, nullable.
        vendor: Charger vendor from ``BootNotification``, nullable.
        model: Charger model from ``BootNotification``, nullable.
        serial_number: Serial the charger reported at boot, nullable.
        firmware_version: Firmware version from the latest boot, nullable.
        last_boot_at: Time of the latest accepted boot, nullable.
        last_seen_at: Time of the latest frame of any kind, nullable.
        charger_status: Status of the whole charger, nullable.
        charger_status_updated_at: Time that status was reported, nullable.
        charger_error_code: ``errorCode`` reported for the whole charger.
        charger_vendor_error_code: ``vendorErrorCode`` of the whole charger.
        is_online: Derived at read time: ``last_seen_at`` is within
            ``CHARGING_OFFLINE_TIMEOUT_SECONDS``. ``False`` for a station
            that has never connected.
        created_at: Time created.
        updated_at: Time of the last edit by a person.
        deleted_at: Soft-delete time, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    station_id: UUID
    location_id: UUID
    organization_id: UUID
    location_display_name: str
    latitude: float | None
    longitude: float | None
    ocpp_identity: str
    registered_serial_number: str
    physical_reference: str | None
    max_power_kw: float | None
    status: ChargingResourceStatus
    status_reason: str | None
    connector_count: int = Field(..., ge=0)
    available_connector_count: int = Field(..., ge=0)
    ocpp_protocol_version: str | None
    vendor: str | None
    model: str | None
    serial_number: str | None
    firmware_version: str | None
    last_boot_at: datetime | None
    last_seen_at: datetime | None
    charger_status: ChargingConnectorStatus | None
    charger_status_updated_at: datetime | None
    charger_error_code: str | None
    charger_vendor_error_code: str | None
    is_online: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ChargingStationListResponse(BaseModel):
    """Paginated list of stations.

    Attributes:
        items: Active stations on the current page.
        total: Total number of active stations.
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
    """

    items: list[ChargingStationResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class NearbyChargingStationResponse(BaseModel):
    """A charger near a queried point, driver-facing (F-D1).

    Drops the internal fields ``ChargingStationResponse`` carries and adds
    ``distance_km``. Only chargers at public, ``ACTIVE`` locations are found
    until authentication exists (WP2).

    Attributes:
        station_id: Internal UUID.
        location_id: The location it stands at.
        display_name: Name of the location.
        physical_reference: Label printed on the unit, nullable.
        latitude: GPS latitude of the location.
        longitude: GPS longitude of the location.
        max_power_kw: Total output shared by the guns, nullable.
        status: ``ACTIVE`` or ``INACTIVE``.
        connector_count: Number of active connectors, computed at read time.
        available_connector_count: How many of those last reported
            ``Available`` (F-D1). A station is "available" (the
            ``is_available_only`` filter) when it is ``ACTIVE`` and this is at
            least one; ``is_online`` is not required.
        is_online: Derived at read time; informational only.
        distance_km: Great-circle distance from the query point, in km.
    """

    station_id: UUID
    location_id: UUID
    display_name: str
    physical_reference: str | None
    latitude: float | None
    longitude: float | None
    max_power_kw: float | None
    status: ChargingResourceStatus
    connector_count: int = Field(..., ge=0)
    available_connector_count: int = Field(..., ge=0)
    is_online: bool
    distance_km: float = Field(..., ge=0)


class NearbyChargingStationListResponse(BaseModel):
    """Paginated list of nearby stations.

    Attributes:
        items: Matching stations on the current page, nearest first.
        total: Total number of stations within the queried radius.
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
    """

    items: list[NearbyChargingStationResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


# --- EVSEs and connectors ----------------------------------------------------


class ChargingEvseCreateRequest(BaseModel):
    """Data for creating an EVSE belonging to a station.

    Attributes:
        ocpp_evse_id: Positive EVSE ID used by the station in OCPP.
        emi3_evse_id: Public eMI3 ID, e.g. ``VN*G3N*E0001A`` (CS-16).
    """

    ocpp_evse_id: int = Field(..., gt=0)
    emi3_evse_id: str = Field(..., min_length=1, max_length=48)

    @field_validator("emi3_evse_id")
    @classmethod
    def validate_emi3_evse_id(cls, value: str) -> str:
        """Reject a blank public ID.

        Args:
            value: Raw text from the request.

        Returns:
            The stripped text.
        """
        return _strip_required_text(value)


class ChargingEvseUpdateRequest(BaseModel):
    """EVSE fields allowed for partial update (``None`` means unchanged).

    Attributes:
        ocpp_evse_id: New OCPP ID.
        emi3_evse_id: New public ID.
        status: New status set by a person (one gun out of service, CS-16).
        status_reason: Why; required when the status becomes ``INACTIVE``.
    """

    ocpp_evse_id: int | None = Field(None, gt=0)
    emi3_evse_id: str | None = Field(None, min_length=1, max_length=48)
    status: ChargingResourceStatus | None = None
    status_reason: str | None = Field(None, min_length=1, max_length=200)

    @field_validator("emi3_evse_id", "status_reason")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Normalize optional text.

        Args:
            value: Raw text or ``None``.

        Returns:
            The stripped text, or ``None``.
        """
        return _strip_optional_text(value)

    @model_validator(mode="after")
    def validate_reason(self) -> "ChargingEvseUpdateRequest":
        """Require a reason when the EVSE is taken out of service.

        Returns:
            The validated request.

        Raises:
            ValueError: If the status becomes ``INACTIVE`` without a reason.
        """
        if self.status is ChargingResourceStatus.INACTIVE and not self.status_reason:
            raise ValueError("status_reason is required when status is INACTIVE")
        return self


class ChargingEvseResponse(BaseModel):
    """EVSE information within the pre-provisioned topology.

    Attributes:
        evse_id: Internal UUID.
        station_id: UUID of the parent station.
        ocpp_evse_id: EVSE ID in OCPP.
        emi3_evse_id: Public eMI3 ID.
        status: ``ACTIVE`` or ``INACTIVE``.
        status_reason: Why, ``None`` when ACTIVE.
        created_at: Time created.
        updated_at: Time of last update.
        deleted_at: Soft-delete time, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    evse_id: UUID
    station_id: UUID
    ocpp_evse_id: int
    emi3_evse_id: str
    status: ChargingResourceStatus
    status_reason: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ChargingEvseListResponse(BaseModel):
    """Paginated list of EVSEs.

    Attributes:
        items: Active EVSEs on the current page.
        total: Total number of active EVSEs for the parent station.
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
    """

    items: list[ChargingEvseResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class ChargingConnectorCreateRequest(BaseModel):
    """Data for creating a connector belonging to an EVSE (CS-17).

    Attributes:
        ocpp_connector_id: Positive connector ID used by the EVSE in OCPP.
        standard: Plug standard, from the gun's nameplate.
        max_power_kw: Highest power the gun delivers, in kW.
        max_voltage_v: Highest output voltage, in volts.
        max_current_a: Highest output current, in amperes.
    """

    ocpp_connector_id: int = Field(..., gt=0)
    standard: ConnectorStandard
    max_power_kw: float = Field(..., gt=0, le=9999.99)
    max_voltage_v: int = Field(..., gt=0)
    max_current_a: int = Field(..., gt=0)


class ChargingConnectorUpdateRequest(BaseModel):
    """Connector fields allowed for partial update (``None`` means unchanged).

    Attributes:
        ocpp_connector_id: New OCPP ID.
        standard: New plug standard.
        max_power_kw: New highest power, in kW.
        max_voltage_v: New highest voltage, in volts.
        max_current_a: New highest current, in amperes.
    """

    ocpp_connector_id: int | None = Field(None, gt=0)
    standard: ConnectorStandard | None = None
    max_power_kw: float | None = Field(None, gt=0, le=9999.99)
    max_voltage_v: int | None = Field(None, gt=0)
    max_current_a: int | None = Field(None, gt=0)


class ChargingConnectorResponse(BaseModel):
    """Connector information: its plug and power, plus the status it reported.

    Attributes:
        connector_id: Internal UUID.
        evse_id: UUID of the parent EVSE.
        ocpp_connector_id: Connector ID in OCPP.
        standard: Plug standard.
        max_power_kw: Highest power the gun delivers, in kW.
        max_voltage_v: Highest output voltage, in volts.
        max_current_a: Highest output current, in amperes.
        status: Live status last reported via OCPP ``StatusNotification``
            (F-C2), nullable if the connector hasn't reported yet.
        status_updated_at: Time the last status report was processed,
            nullable.
        error_code: ``errorCode`` of the latest report (OCPP 1.6J), nullable.
        vendor_error_code: ``vendorErrorCode`` of the latest report.
        status_info: Free-text ``info`` of the latest report, nullable.
        created_at: Time created.
        updated_at: Time of last update.
        deleted_at: Soft-delete time, nullable.
    """

    connector_id: UUID
    evse_id: UUID
    ocpp_connector_id: int
    standard: ConnectorStandard
    max_power_kw: float
    max_voltage_v: int
    max_current_a: int
    status: ChargingConnectorStatus | None
    status_updated_at: datetime | None
    error_code: str | None
    vendor_error_code: str | None
    status_info: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ChargingConnectorListResponse(BaseModel):
    """Paginated list of connectors.

    Attributes:
        items: Active connectors on the current page.
        total: Total number of active connectors for the parent EVSE.
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
    """

    items: list[ChargingConnectorResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class ChargingResourceDeleteResponse(BaseModel):
    """Result of a topology soft-delete.

    Attributes:
        message: Business message returned to the client.
    """

    message: str


# --- Commands ----------------------------------------------------------------


class ChargingStationCommandCreateRequest(BaseModel):
    """Data for sending a command to a charger (STN-10, CS-20).

    The command is queued; the OCPP gateway holding the charger's connection
    sends it and writes the answer back (PR-16).

    Attributes:
        command_type: What to ask.
        evse_id: The gun it targets, ``None`` for the whole charger.
        session_id: The session a remote stop ends (or a remote start begins).
        parameters: What to send besides the links, e.g. ``{"reset_type":
            "Soft"}``, ``{"key": "HeartbeatInterval", "value": "60"}``,
            ``{"requested_message": "StatusNotification"}``,
            ``{"availability": "Inoperative"}``, ``{"id_token": "..."}`` for a
            manual remote start without a session.
        requested_by: User who asks (required until authentication, WP2).
        reason: Why, typed by the operator.
    """

    command_type: StationCommandType
    evse_id: UUID | None = None
    session_id: UUID | None = None
    parameters: dict[str, Any] | None = None
    requested_by: UUID | None = None
    reason: str | None = Field(None, min_length=1, max_length=200)


class ChargingStationCommandResponse(BaseModel):
    """A command and its answer.

    Attributes:
        command_id: Internal UUID.
        station_id: The charger.
        evse_id: The gun targeted, nullable.
        session_id: The session it is about, nullable.
        command_type: What was asked.
        parameters: What was sent besides the links, nullable.
        requested_by: User who asked, nullable.
        reason: Why, nullable.
        requested_at: When it was created.
        ocpp_message_id: Message ID of the frame sent, ``None`` while queued.
        outcome: Observed result; ``PENDING`` with no message ID is queued.
        response_status: The charger's answer as sent, nullable.
        answered_at: When the answer or timeout was recorded, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    command_id: UUID
    station_id: UUID
    evse_id: UUID | None
    session_id: UUID | None
    command_type: StationCommandType
    parameters: dict[str, Any] | None
    requested_by: UUID | None
    reason: str | None
    requested_at: datetime
    ocpp_message_id: str | None
    outcome: StationCommandOutcome
    response_status: str | None
    answered_at: datetime | None


class ChargingStationCommandListResponse(BaseModel):
    """Paginated list of a charger's commands, newest first.

    Attributes:
        items: Commands on the current page.
        total: Total number of the charger's commands.
        page: Page number, starting at one.
        page_size: Maximum number of items per page.
    """

    items: list[ChargingStationCommandResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


# --- Configuration -----------------------------------------------------------


class ChargingStationConfigurationEntryResponse(BaseModel):
    """One value of one setting of a charger.

    Attributes:
        variable_name: The 1.6J key or the 2.0.1 variable.
        component_name: OCPP 2.0.1 only: the component, nullable.
        attribute_type: ``Actual`` (always for 1.6J), ``Target``, ``MinSet``
            or ``MaxSet``.
        value: The value as text, nullable.
        mutability: ``READ_ONLY``, ``READ_WRITE`` or ``WRITE_ONLY``.
    """

    model_config = ConfigDict(from_attributes=True)

    variable_name: str
    component_name: str | None
    attribute_type: str
    value: str | None
    mutability: str


class ChargingStationConfigurationResponse(BaseModel):
    """The latest complete configuration snapshot of a charger.

    Attributes:
        station_id: The station the configuration belongs to.
        capture_id: Identifies the snapshot, nullable if the charger has not
            reported its configuration yet.
        captured_at: When the charger's answer was received, nullable likewise.
        items: The settings of that snapshot, sorted by name; empty if none.
    """

    station_id: UUID
    capture_id: UUID | None
    captured_at: datetime | None
    items: list[ChargingStationConfigurationEntryResponse]


class ChargingStationConnectorStatusResponse(BaseModel):
    """The reported status of one gun of a station (F-C2).

    Attributes:
        connector_id: Internal UUID of the connector.
        evse_id: Internal UUID of the parent EVSE.
        ocpp_evse_id: EVSE number in OCPP; for a 1.6J charger this is the gun
            number (decision D3 of the OCPP 1.6J planner).
        ocpp_connector_id: Connector number within the EVSE.
        status: Last status reported via ``StatusNotification``, nullable if
            the connector has not reported yet.
        status_updated_at: Time that status was reported, nullable.
        error_code: ``errorCode`` of the latest report (1.6J), nullable.
        vendor_error_code: ``vendorErrorCode`` of the latest report, nullable.
        status_info: Free-text ``info`` of the latest report, nullable.
    """

    connector_id: UUID
    evse_id: UUID
    ocpp_evse_id: int
    ocpp_connector_id: int
    status: ChargingConnectorStatus | None
    status_updated_at: datetime | None
    error_code: str | None
    vendor_error_code: str | None
    status_info: str | None


class ChargingStationStatusResponse(BaseModel):
    """The whole charger's status plus every gun's status, in one read (F-C2).

    Statuses are shown as last reported; a charger that went offline keeps
    its last statuses (stale-status handling is deferred).

    Attributes:
        station_id: Internal UUID of the station.
        charger_status: Status of the whole charger, nullable.
        charger_status_updated_at: Time that status was reported, nullable.
        charger_error_code: ``errorCode`` reported for the whole charger,
            nullable.
        charger_vendor_error_code: ``vendorErrorCode`` reported for the whole
            charger, nullable.
        connectors: Every active connector of the station's active EVSEs,
            ordered by EVSE number, then connector number.
    """

    station_id: UUID
    charger_status: ChargingConnectorStatus | None
    charger_status_updated_at: datetime | None
    charger_error_code: str | None
    charger_vendor_error_code: str | None
    connectors: list[ChargingStationConnectorStatusResponse]


class ChargingStationEnergyTotalResponse(BaseModel):
    """Energy sold at one station within the queried window (F-C5).

    Attributes:
        station_id: Internal UUID of the station.
        display_name: Display name of the station.
        total_energy_kwh: Sum of ``energy_delivered_wh`` (in kWh) of the
            station's completed sessions that ended within the window.
        session_count: Number of those sessions.
    """

    station_id: UUID
    display_name: str
    total_energy_kwh: float = Field(..., ge=0)
    session_count: int = Field(..., ge=0)


class ChargingStationEnergyTotalListResponse(BaseModel):
    """Energy sold per station within a window, for every active station (F-C5).

    Same semantics as the per-station
    ``/charging-sessions/stations/{station_id}/energy`` summary, applied to
    each non-deleted station.

    Attributes:
        start_time: Inclusive lower bound on a session's ``ended_at``, in UTC.
        end_time: Inclusive upper bound on a session's ``ended_at``, in UTC.
        total_energy_kwh: Sum over every listed station.
        session_count: Sum of the listed stations' session counts.
        items: One entry per active station (a station with no session is
            included at zero), highest energy first; ties by display name.
    """

    start_time: datetime
    end_time: datetime
    total_energy_kwh: float = Field(..., ge=0)
    session_count: int = Field(..., ge=0)
    items: list[ChargingStationEnergyTotalResponse]
