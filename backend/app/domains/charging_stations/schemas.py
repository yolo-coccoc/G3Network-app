"""Pydantic schemas for pre-provisioned charging topology.

The ideal MVP only exposes the OCPP identity, internal IDs, and timestamps
needed to inspect the topology. Location, capability, technical status, and
device information are deferred alongside the technical status path; they are
not included in the active HTTP contract.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.libs.common.config import settings


class ChargingStationCreateRequest(BaseModel):
    """Data for creating a pre-provisioned station.

    Attributes:
        ocpp_identity: Station identity used in the OCPP WebSocket path.
        display_name: Display name of the station.
    """

    ocpp_identity: str = Field(..., min_length=1, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=200)

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


class ChargingStationUpdateRequest(BaseModel):
    """Station identity fields allowed for partial update.

    Attributes:
        ocpp_identity: New identity; ``None`` means do not update.
        display_name: New display name; ``None`` means do not update.
    """

    ocpp_identity: str | None = Field(None, min_length=1, max_length=255)
    display_name: str | None = Field(None, min_length=1, max_length=200)

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


class ChargingStationResponse(BaseModel):
    """Active station information without technical status or raw payload.

    Attributes:
        station_id: Internal UUID.
        ocpp_identity: Unique OCPP identity.
        display_name: Display name.
        created_at: Time created.
        updated_at: Time of last update.
        deleted_at: Soft-delete time, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    station_id: UUID
    ocpp_identity: str
    display_name: str
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
        created_at: Time created.
        updated_at: Time of last update.
        deleted_at: Soft-delete time, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    connector_id: UUID
    evse_id: UUID
    ocpp_connector_id: int
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
