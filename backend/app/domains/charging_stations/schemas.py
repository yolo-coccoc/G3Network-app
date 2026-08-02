"""Pydantic schemas cho API CRUD topology trạm sạc.

Schema chỉ mô tả contract HTTP và không phụ thuộc SQLAlchemy hoặc kiểu PostGIS.
Tọa độ được biểu diễn bằng latitude/longitude rồi service chuyển thành
``geography(Point, 4326)`` trước khi lưu.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.charging_stations.types import (
    EvseAdministrativeStatus,
    StationAdministrativeStatus,
    StationConnectionStatus,
    TechnicalStatus,
)
from app.libs.common.config import settings


class LocationInput(BaseModel):
    """Tọa độ WGS84 dùng trong request topology."""

    latitude: float = Field(..., ge=-90, le=90, description="Vĩ độ WGS84")
    longitude: float = Field(..., ge=-180, le=180, description="Kinh độ WGS84")


class LocationResponse(LocationInput):
    """Tọa độ WGS84 trả về qua API."""


class StationCreate(BaseModel):
    """Dữ liệu tạo charging station đã pre-provision."""

    ocpp_identity: str = Field(..., min_length=1, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=200)
    manufacturer: str | None = Field(None, max_length=200)
    model: str | None = Field(None, max_length=200)
    serial_number: str | None = Field(None, max_length=200)
    firmware_version: str | None = Field(None, max_length=100)
    location: LocationInput | None = None
    administrative_status: StationAdministrativeStatus = (
        StationAdministrativeStatus.ACTIVE
    )

    @field_validator("ocpp_identity", "display_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """Loại khoảng trắng đầu/cuối và từ chối chuỗi chỉ gồm khoảng trắng."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Giá trị không được rỗng hoặc chỉ chứa khoảng trắng")
        return normalized


class StationUpdate(BaseModel):
    """Các field station được phép cập nhật một phần."""

    ocpp_identity: str | None = Field(None, min_length=1, max_length=255)
    display_name: str | None = Field(None, min_length=1, max_length=200)
    manufacturer: str | None = Field(None, max_length=200)
    model: str | None = Field(None, max_length=200)
    serial_number: str | None = Field(None, max_length=200)
    firmware_version: str | None = Field(None, max_length=100)
    location: LocationInput | None = None
    administrative_status: StationAdministrativeStatus | None = None

    @field_validator(
        "ocpp_identity",
        "display_name",
        "manufacturer",
        "model",
        "serial_number",
        "firmware_version",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Chuẩn hóa text; ``None`` được service coi là không cập nhật."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Giá trị không được rỗng hoặc chỉ chứa khoảng trắng")
        return normalized


class StationResponse(BaseModel):
    """Thông tin station không bao gồm raw payload hoặc credential."""

    model_config = ConfigDict(from_attributes=True)

    station_id: UUID
    ocpp_identity: str
    display_name: str
    manufacturer: str | None
    model: str | None
    serial_number: str | None
    firmware_version: str | None
    location: LocationResponse | None
    administrative_status: StationAdministrativeStatus
    connection_status: StationConnectionStatus
    last_seen_at: datetime | None
    last_boot_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class StationListResponse(BaseModel):
    """Danh sách station phân trang."""

    items: list[StationResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class EvseCreate(BaseModel):
    """Dữ liệu tạo EVSE thuộc station."""

    ocpp_evse_id: int = Field(..., gt=0)
    display_name: str | None = Field(None, max_length=100)
    administrative_status: EvseAdministrativeStatus = EvseAdministrativeStatus.ACTIVE
    technical_status: TechnicalStatus = TechnicalStatus.UNKNOWN
    capabilities: dict[str, object] = Field(default_factory=dict)


class EvseUpdate(BaseModel):
    """Các field EVSE được phép cập nhật một phần."""

    ocpp_evse_id: int | None = Field(None, gt=0)
    display_name: str | None = Field(None, max_length=100)
    administrative_status: EvseAdministrativeStatus | None = None
    technical_status: TechnicalStatus | None = None
    capabilities: dict[str, object] | None = None


class EvseResponse(BaseModel):
    """Thông tin EVSE thuộc topology đã pre-provision."""

    model_config = ConfigDict(from_attributes=True)

    evse_id: UUID
    station_id: UUID
    ocpp_evse_id: int
    display_name: str | None
    administrative_status: EvseAdministrativeStatus
    technical_status: TechnicalStatus
    capabilities: dict[str, object]
    last_status_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class EvseListResponse(BaseModel):
    """Danh sách EVSE phân trang."""

    items: list[EvseResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class ConnectorCreate(BaseModel):
    """Dữ liệu tạo connector thuộc EVSE."""

    ocpp_connector_id: int = Field(..., gt=0)
    connector_type: str | None = Field(None, max_length=100)
    max_power_kw: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=3)
    administrative_status: EvseAdministrativeStatus = EvseAdministrativeStatus.ACTIVE
    technical_status: TechnicalStatus = TechnicalStatus.UNKNOWN
    capabilities: dict[str, object] = Field(default_factory=dict)


class ConnectorUpdate(BaseModel):
    """Các field connector được phép cập nhật một phần."""

    ocpp_connector_id: int | None = Field(None, gt=0)
    connector_type: str | None = Field(None, max_length=100)
    max_power_kw: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=3)
    administrative_status: EvseAdministrativeStatus | None = None
    technical_status: TechnicalStatus | None = None
    capabilities: dict[str, object] | None = None


class ConnectorResponse(BaseModel):
    """Thông tin connector thuộc topology đã pre-provision."""

    model_config = ConfigDict(from_attributes=True)

    connector_id: UUID
    evse_id: UUID
    ocpp_connector_id: int
    connector_type: str | None
    max_power_kw: Decimal | None
    administrative_status: EvseAdministrativeStatus
    technical_status: TechnicalStatus
    capabilities: dict[str, object]
    last_status_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ConnectorListResponse(BaseModel):
    """Danh sách connector phân trang."""

    items: list[ConnectorResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class DeleteResponse(BaseModel):
    """Kết quả soft-delete topology."""

    message: str
