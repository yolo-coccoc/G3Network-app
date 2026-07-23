"""Pydantic schemas for Vehicle domain."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict

from app.domains.vehicles.models import VehicleStatus


class VehicleBase(BaseModel):
    """Base schema for Vehicle with common fields."""
    
    license_plate: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Biển số xe",
        examples=["51A-12345"]
    )
    vin: str | None = Field(
        None,
        min_length=17,
        max_length=17,
        description="Số khung (Vehicle Identification Number)",
        examples=["1HGBH41JXMN109186"]
    )
    telematics_device_id: str | None = Field(
        None,
        max_length=50,
        description="Mã thiết bị telematics gắn trên xe",
        examples=["TEL-001-ABC123"]
    )
    make: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Hãng xe",
        examples=["VinFast"]
    )
    model: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Dòng xe",
        examples=["eTruck 500"]
    )
    year: int = Field(
        ...,
        ge=1900,
        le=2100,
        description="Năm sản xuất",
        examples=[2024]
    )
    status: VehicleStatus = Field(
        default=VehicleStatus.ACTIVE,
        description="Trạng thái xe"
    )


class VehicleCreate(VehicleBase):
    """Schema for creating a new vehicle."""
    
    fleet_id: UUID | None = Field(
        None,
        description="ID đội xe (nullable - có thể chưa phân bổ)"
    )


class VehicleUpdate(BaseModel):
    """Schema for updating a vehicle. All fields are optional."""
    
    license_plate: str | None = Field(
        None,
        min_length=1,
        max_length=20,
        description="Biển số xe"
    )
    vin: str | None = Field(
        None,
        min_length=17,
        max_length=17,
        description="Số khung"
    )
    telematics_device_id: str | None = Field(
        None,
        max_length=50,
        description="Mã thiết bị telematics"
    )
    make: str | None = Field(
        None,
        min_length=1,
        max_length=50,
        description="Hãng xe"
    )
    model: str | None = Field(
        None,
        min_length=1,
        max_length=50,
        description="Dòng xe"
    )
    year: int | None = Field(
        None,
        ge=1900,
        le=2100,
        description="Năm sản xuất"
    )
    status: VehicleStatus | None = Field(
        None,
        description="Trạng thái xe"
    )
    fleet_id: UUID | None = Field(
        None,
        description="ID đội xe"
    )


class VehicleResponse(VehicleBase):
    """Schema for vehicle response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID = Field(..., description="ID xe (UUID)")
    fleet_id: UUID | None = Field(None, description="ID đội xe")
    created_at: datetime = Field(..., description="Thời gian tạo")
    updated_at: datetime = Field(..., description="Thời gian cập nhật cuối")


class VehicleListResponse(BaseModel):
    """Schema for paginated list of vehicles."""
    
    items: list[VehicleResponse] = Field(..., description="Danh sách xe")
    total: int = Field(..., ge=0, description="Tổng số xe")
    page: int = Field(..., ge=1, description="Trang hiện tại")
    page_size: int = Field(..., ge=1, le=100, description="Số item per trang")
