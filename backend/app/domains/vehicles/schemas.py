"""Pydantic schemas for Vehicle domain."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict

from app.domains.vehicles.models import VehicleStatus


class VehicleBase(BaseModel):
    """Base schema for Vehicle with common fields."""
    
    license_plate: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Biển số xe"
    )
    vin: str | None = Field(
        None,
        min_length=17,
        max_length=17,
        description="Số khung (Vehicle Identification Number)"
    )
    telematics_device_id: str | None = Field(
        None,
        max_length=50,
        description="Mã thiết bị telematics gắn trên xe"
    )
    make: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Hãng xe"
    )
    model: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Dòng xe"
    )
    year: int = Field(
        ...,
        ge=1900,
        le=2100,
        description="Năm sản xuất"
    )
    status: VehicleStatus = Field(
        default=VehicleStatus.ACTIVE,
        description="Trạng thái xe"
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "license_plate": "51A-12345",
                "vin": "1HGBH41JXMN109186",
                "telematics_device_id": "TEL-001-ABC123",
                "make": "VinFast",
                "model": "eTruck 500",
                "year": 2024,
                "status": "active"
            }
        }
    )


class VehicleCreate(VehicleBase):
    """Schema for creating a new vehicle."""
    
    fleet_id: UUID | None = Field(
        None,
        description="ID đội xe (nullable - có thể chưa phân bổ)"
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "license_plate": "51A-12345",
                "vin": "1HGBH41JXMN109186",
                "telematics_device_id": "TEL-001-ABC123",
                "make": "VinFast",
                "model": "eTruck 500",
                "year": 2024,
                "status": "active",
                "fleet_id": "123e4567-e89b-12d3-a456-426614174000"
            }
        }
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
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "year": 2025,
                "status": "maintenance"
            }
        }
    )


class VehicleResponse(VehicleBase):
    """Schema for vehicle response."""
    
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "license_plate": "51A-12345",
                "vin": "1HGBH41JXMN109186",
                "telematics_device_id": "TEL-001-ABC123",
                "make": "VinFast",
                "model": "eTruck 500",
                "year": 2024,
                "status": "active",
                "fleet_id": "123e4567-e89b-12d3-a456-426614174001",
                "created_at": "2024-01-15T10:30:00",
                "updated_at": "2024-01-15T10:30:00"
            }
        }
    )
    
    id: UUID = Field(..., description="ID xe (UUID)")
    fleet_id: UUID | None = Field(None, description="ID đội xe")
    created_at: datetime = Field(..., description="Thời gian tạo")
    updated_at: datetime = Field(..., description="Thời gian cập nhật cuối")


class VehicleListResponse(BaseModel):
    """Schema for paginated list of vehicles."""
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "id": "123e4567-e89b-12d3-a456-426614174000",
                        "license_plate": "51A-12345",
                        "vin": "1HGBH41JXMN109186",
                        "telematics_device_id": "TEL-001-ABC123",
                        "make": "VinFast",
                        "model": "eTruck 500",
                        "year": 2024,
                        "status": "active",
                        "fleet_id": "123e4567-e89b-12d3-a456-426614174001",
                        "created_at": "2024-01-15T10:30:00",
                        "updated_at": "2024-01-15T10:30:00"
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 10
            }
        }
    )
    
    items: list[VehicleResponse] = Field(..., description="Danh sách xe")
    total: int = Field(..., ge=0, description="Tổng số xe")
    page: int = Field(..., ge=1, description="Trang hiện tại")
    page_size: int = Field(..., ge=1, le=100, description="Số item per trang")
