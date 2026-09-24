"""Pydantic schemas for pre-provisioned charging topology.

``ChargingStationCreateRequest``/``ChargingStationUpdateRequest``/
``ChargingStationResponse`` expose directory/descriptive metadata (location,
power rating, connector standard, operating hours, maintenance status) per
F-C1, plus the OCPP identity, internal IDs, and timestamps.
``ChargingConnectorResponse`` also exposes the connector's live OCPP status
per F-C2 (read-only - not part of ``ChargingConnectorUpdateRequest``, since
it's OCPP-owned, not admin-editable). Capability negotiation, heartbeat-based
connection status, and other device information remain deferred alongside
the technical status path; they are not included in the active HTTP
contract.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingStationMaintenanceStatus,
)
from app.libs.common.config import settings


class ChargingStationCreateRequest(BaseModel):
    """Data for creating a pre-provisioned station.

    Attributes:
        ocpp_identity: Station identity used in the OCPP WebSocket path.
        display_name: Display name of the station.
        latitude: GPS latitude in decimal degrees, nullable.
        longitude: GPS longitude in decimal degrees, nullable.
        power_rating_kw: Nominal power rating in kW, nullable (0 <
            value <= 9999.99, matching the ``Numeric(6, 2)`` column).
        connector_standard: Connector standard served (e.g. ``"CCS2"``),
            nullable.
        operating_hours: Freeform operating hours description, nullable.
        maintenance_status: Admin-set maintenance state; defaults to
            ``OPERATIONAL`` if not given.
    """

    ocpp_identity: str = Field(..., min_length=1, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=200)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    power_rating_kw: float | None = Field(None, gt=0, le=9999.99)
    connector_standard: str | None = Field(None, min_length=1, max_length=20)
    operating_hours: str | None = Field(None, min_length=1, max_length=100)
    maintenance_status: ChargingStationMaintenanceStatus | None = None

    @field_validator("ocpp_identity", "display_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """Normalize required text and reject strings that are blank/whitespace-only.

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

    @model_validator(mode="after")
    def validate_location_pair(self) -> "ChargingStationCreateRequest":
        """Require latitude/longitude together, since one alone isn't a location.

        Returns:
            The validated request.

        Raises:
            ValueError: If exactly one of latitude/longitude is provided.
        """
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class ChargingStationUpdateRequest(BaseModel):
    """Station fields allowed for partial update.

    Attributes:
        ocpp_identity: New identity; ``None`` means do not update.
        display_name: New display name; ``None`` means do not update.
        latitude: New GPS latitude; ``None`` means do not update.
        longitude: New GPS longitude; ``None`` means do not update.
        power_rating_kw: New power rating in kW (0 < value <= 9999.99);
            ``None`` means do not update.
        connector_standard: New connector standard; ``None`` means do not
            update.
        operating_hours: New operating hours description; ``None`` means do
            not update.
        maintenance_status: New maintenance state; ``None`` means do not
            update.
    """

    ocpp_identity: str | None = Field(None, min_length=1, max_length=255)
    display_name: str | None = Field(None, min_length=1, max_length=200)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    power_rating_kw: float | None = Field(None, gt=0, le=9999.99)
    connector_standard: str | None = Field(None, min_length=1, max_length=20)
    operating_hours: str | None = Field(None, min_length=1, max_length=100)
    maintenance_status: ChargingStationMaintenanceStatus | None = None

    @field_validator("ocpp_identity", "display_name")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Normalize optional text; ``None`` means the field is not updated.

        Args:
            value: Raw text or ``None`` from the PATCH request.

        Returns:
            Text with whitespace stripped, or ``None``.

        Raises:
            ValueError: If the text is empty after normalization.
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be empty or contain only whitespace")
        return normalized

    @model_validator(mode="after")
    def validate_location_pair(self) -> "ChargingStationUpdateRequest":
        """Require latitude/longitude together when either is being updated.

        Per this backend's PATCH convention, ``None`` means "do not update"
        for each field independently - so this only rejects the case where
        exactly one of the pair is being set to a real value.

        Returns:
            The validated request.

        Raises:
            ValueError: If exactly one of latitude/longitude is provided.
        """
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class ChargingStationResponse(BaseModel):
    """Active station information without technical status or raw payload.

    Attributes:
        station_id: Internal UUID.
        ocpp_identity: Unique OCPP identity.
        display_name: Display name.
        latitude: GPS latitude in decimal degrees, nullable.
        longitude: GPS longitude in decimal degrees, nullable.
        power_rating_kw: Nominal power rating in kW, nullable.
        connector_standard: Connector standard served, nullable.
        operating_hours: Freeform operating hours description, nullable.
        maintenance_status: Admin-set maintenance state.
        connector_count: Number of active connectors across the station's
            active EVSEs, computed at read time (not stored).
        ocpp_protocol_version: Subprotocol of the latest connection,
            nullable until the charger first connects.
        vendor: Charger vendor from ``BootNotification``, nullable.
        model: Charger model from ``BootNotification``, nullable.
        serial_number: Charger serial number, nullable.
        firmware_version: Firmware version from the latest boot, nullable.
        last_boot_at: Time of the latest accepted boot, nullable.
        last_seen_at: Time of the latest frame of any kind from the charger,
            nullable.
        is_online: Derived at read time: ``last_seen_at`` is within
            ``CHARGING_OFFLINE_TIMEOUT_SECONDS``. ``False`` for a station
            that has never connected. A connector's last reported status is
            *not* invalidated when a charger goes offline.
        created_at: Time created.
        updated_at: Time of last update.
        deleted_at: Soft-delete time, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    station_id: UUID
    ocpp_identity: str
    display_name: str
    latitude: float | None
    longitude: float | None
    power_rating_kw: float | None
    connector_standard: str | None
    operating_hours: str | None
    maintenance_status: ChargingStationMaintenanceStatus
    connector_count: int = Field(..., ge=0)
    ocpp_protocol_version: str | None
    vendor: str | None
    model: str | None
    serial_number: str | None
    firmware_version: str | None
    last_boot_at: datetime | None
    last_seen_at: datetime | None
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
    """A station near a queried point, driver-facing (F-D1).

    Drops ``ocpp_identity`` and the audit/soft-delete timestamps that
    ``ChargingStationResponse`` carries - not relevant to a driver-facing
    map query - and adds ``distance_km``.

    Attributes:
        station_id: Internal UUID.
        display_name: Display name.
        latitude: GPS latitude in decimal degrees, nullable.
        longitude: GPS longitude in decimal degrees, nullable.
        power_rating_kw: Nominal power rating in kW, nullable.
        connector_standard: Connector standard served, nullable.
        operating_hours: Freeform operating hours description, nullable.
        maintenance_status: Admin-set maintenance state. "Available" only
            reflects this field, not a live occupancy signal - see
            ``docs/01-requirements/future.md``.
        connector_count: Number of active connectors across the station's
            active EVSEs, computed at read time (not stored).
        distance_km: Great-circle distance from the query point, in km.
    """

    model_config = ConfigDict(from_attributes=True)

    station_id: UUID
    display_name: str
    latitude: float | None
    longitude: float | None
    power_rating_kw: float | None
    connector_standard: str | None
    operating_hours: str | None
    maintenance_status: ChargingStationMaintenanceStatus
    connector_count: int = Field(..., ge=0)
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


class ChargingEvseCreateRequest(BaseModel):
    """Data for creating an EVSE belonging to a station.

    Attributes:
        ocpp_evse_id: Positive EVSE ID used by the station in OCPP.
    """

    ocpp_evse_id: int = Field(..., gt=0)


class ChargingEvseUpdateRequest(BaseModel):
    """EVSE OCPP identity allowed for partial update.

    Attributes:
        ocpp_evse_id: New ID, or ``None`` to leave it unchanged.
    """

    ocpp_evse_id: int | None = Field(None, gt=0)


class ChargingEvseResponse(BaseModel):
    """EVSE information within the pre-provisioned topology.

    Attributes:
        evse_id: Internal UUID.
        station_id: UUID of the parent station.
        ocpp_evse_id: EVSE ID in OCPP.
        created_at: Time created.
        updated_at: Time of last update.
        deleted_at: Soft-delete time, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    evse_id: UUID
    station_id: UUID
    ocpp_evse_id: int
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
    """Data for creating a connector belonging to an EVSE.

    Attributes:
        ocpp_connector_id: Positive connector ID used by the EVSE in OCPP.
    """

    ocpp_connector_id: int = Field(..., gt=0)


class ChargingConnectorUpdateRequest(BaseModel):
    """Connector OCPP identity allowed for partial update.

    Attributes:
        ocpp_connector_id: New ID, or ``None`` to leave it unchanged.
    """

    ocpp_connector_id: int | None = Field(None, gt=0)


class ChargingConnectorResponse(BaseModel):
    """Connector information within the pre-provisioned topology.

    Attributes:
        connector_id: Internal UUID.
        evse_id: UUID of the parent EVSE.
        ocpp_connector_id: Connector ID in OCPP.
        status: Live status last reported via OCPP ``StatusNotification``
            (F-C2), nullable if the connector hasn't reported yet.
        status_updated_at: Time the last status report was processed,
            nullable.
        created_at: Time created.
        updated_at: Time of last update.
        deleted_at: Soft-delete time, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    connector_id: UUID
    evse_id: UUID
    ocpp_connector_id: int
    status: ChargingConnectorStatus | None
    status_updated_at: datetime | None
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
