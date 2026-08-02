"""Pydantic schemas cho topology charging đã pre-provision.

MVP lý tưởng chỉ expose identity OCPP, internal IDs và timestamps cần để kiểm
tra topology. Location, capability, technical status và thông tin thiết bị
được hoãn cùng technical status path; không đưa chúng vào HTTP contract active.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.libs.common.config import settings


class StationCreate(BaseModel):
    """Dữ liệu tạo station đã pre-provision."""

    ocpp_identity: str = Field(..., min_length=1, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=200)

    @field_validator("ocpp_identity", "display_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """Chuẩn hóa text và từ chối chuỗi chỉ gồm khoảng trắng."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Giá trị không được rỗng hoặc chỉ chứa khoảng trắng")
        return normalized


class StationUpdate(BaseModel):
    """Các identity station được phép cập nhật một phần."""

    ocpp_identity: str | None = Field(None, min_length=1, max_length=255)
    display_name: str | None = Field(None, min_length=1, max_length=200)

    @field_validator("ocpp_identity", "display_name")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Chuẩn hóa text; ``None`` có nghĩa không cập nhật field."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Giá trị không được rỗng hoặc chỉ chứa khoảng trắng")
        return normalized


class StationResponse(BaseModel):
    """Thông tin station active không chứa technical status hoặc raw payload."""

    model_config = ConfigDict(from_attributes=True)

    station_id: UUID
    ocpp_identity: str
    display_name: str
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


class EvseUpdate(BaseModel):
    """Identity OCPP của EVSE được phép cập nhật một phần."""

    ocpp_evse_id: int | None = Field(None, gt=0)


class EvseResponse(BaseModel):
    """Thông tin EVSE thuộc topology đã pre-provision."""

    model_config = ConfigDict(from_attributes=True)

    evse_id: UUID
    station_id: UUID
    ocpp_evse_id: int
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


class ConnectorUpdate(BaseModel):
    """Identity OCPP của connector được phép cập nhật một phần."""

    ocpp_connector_id: int | None = Field(None, gt=0)


class ConnectorResponse(BaseModel):
    """Thông tin connector thuộc topology đã pre-provision."""

    model_config = ConfigDict(from_attributes=True)

    connector_id: UUID
    evse_id: UUID
    ocpp_connector_id: int
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
