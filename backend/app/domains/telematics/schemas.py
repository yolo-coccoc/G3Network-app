"""Pydantic schemas cho CRUD thiết bị Telematic."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.telematics.types import TelematicStatus


class TelematicCreate(BaseModel):
    """Dữ liệu tạo thiết bị; VIN dùng để resolve vehicle_id."""

    telematic_serial: str = Field(..., min_length=1, max_length=50)
    vehicle_vin: str | None = Field(None, min_length=17, max_length=17)
    status: TelematicStatus = TelematicStatus.ACTIVE
    firmware_version: str | None = Field(None, max_length=50)


class TelematicUpdate(BaseModel):
    """Dữ liệu cập nhật từng phần của thiết bị."""

    telematic_serial: str | None = Field(None, min_length=1, max_length=50)
    vehicle_vin: str | None = Field(None, min_length=17, max_length=17)
    status: TelematicStatus | None = None
    firmware_version: str | None = Field(None, max_length=50)


class TelematicResponse(BaseModel):
    """Thông tin thiết bị kèm VIN xe đang được gán."""

    model_config = ConfigDict(from_attributes=True)

    telematic_id: UUID
    telematic_serial: str
    vehicle_id: UUID | None
    vehicle_vin: str | None
    status: TelematicStatus
    firmware_version: str | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TelematicListResponse(BaseModel):
    """Danh sách thiết bị có phân trang."""

    items: list[TelematicResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
