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
    """Dữ liệu tạo station đã pre-provision.

    Attributes:
        ocpp_identity: Identity station dùng trong OCPP WebSocket path.
        display_name: Tên hiển thị của station.
    """

    ocpp_identity: str = Field(..., min_length=1, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=200)

    @field_validator("ocpp_identity", "display_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """Chuẩn hóa text bắt buộc và từ chối chuỗi chỉ gồm khoảng trắng.

        Args:
            value: Text thô từ request.

        Returns:
            Text đã bỏ khoảng trắng đầu/cuối.

        Raises:
            ValueError: Nếu text rỗng sau khi chuẩn hóa.
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("Giá trị không được rỗng hoặc chỉ chứa khoảng trắng")
        return normalized


class StationUpdate(BaseModel):
    """Các identity station được phép cập nhật một phần.

    Attributes:
        ocpp_identity: Identity mới; ``None`` nghĩa là không cập nhật.
        display_name: Tên mới; ``None`` nghĩa là không cập nhật.
    """

    ocpp_identity: str | None = Field(None, min_length=1, max_length=255)
    display_name: str | None = Field(None, min_length=1, max_length=200)

    @field_validator("ocpp_identity", "display_name")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Chuẩn hóa text tùy chọn; ``None`` nghĩa là không cập nhật field.

        Args:
            value: Text thô hoặc ``None`` từ PATCH request.

        Returns:
            Text đã bỏ khoảng trắng hoặc ``None``.

        Raises:
            ValueError: Nếu text không rỗng sau khi chuẩn hóa.
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Giá trị không được rỗng hoặc chỉ chứa khoảng trắng")
        return normalized


class StationResponse(BaseModel):
    """Thông tin station active không chứa technical status hoặc raw payload.

    Attributes:
        station_id: UUID nội bộ.
        ocpp_identity: Identity OCPP duy nhất.
        display_name: Tên hiển thị.
        created_at: Thời điểm tạo.
        updated_at: Thời điểm cập nhật gần nhất.
        deleted_at: Thời điểm soft-delete, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    station_id: UUID
    ocpp_identity: str
    display_name: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class StationListResponse(BaseModel):
    """Danh sách station phân trang.

    Attributes:
        items: Các station active ở trang hiện tại.
        total: Tổng station active.
        page: Số trang bắt đầu từ một.
        page_size: Số item tối đa trong một trang.
    """

    items: list[StationResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class EvseCreate(BaseModel):
    """Dữ liệu tạo EVSE thuộc station.

    Attributes:
        ocpp_evse_id: ID EVSE dương do station dùng trong OCPP.
    """

    ocpp_evse_id: int = Field(..., gt=0)


class EvseUpdate(BaseModel):
    """Identity OCPP của EVSE được phép cập nhật một phần.

    Attributes:
        ocpp_evse_id: ID mới hoặc ``None`` để không cập nhật.
    """

    ocpp_evse_id: int | None = Field(None, gt=0)


class EvseResponse(BaseModel):
    """Thông tin EVSE thuộc topology đã pre-provision.

    Attributes:
        evse_id: UUID nội bộ.
        station_id: UUID station parent.
        ocpp_evse_id: ID EVSE trong OCPP.
        created_at: Thời điểm tạo.
        updated_at: Thời điểm cập nhật gần nhất.
        deleted_at: Thời điểm soft-delete, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    evse_id: UUID
    station_id: UUID
    ocpp_evse_id: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class EvseListResponse(BaseModel):
    """Danh sách EVSE phân trang.

    Attributes:
        items: Các EVSE active ở trang hiện tại.
        total: Tổng EVSE active của station parent.
        page: Số trang bắt đầu từ một.
        page_size: Số item tối đa trong một trang.
    """

    items: list[EvseResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class ConnectorCreate(BaseModel):
    """Dữ liệu tạo connector thuộc EVSE.

    Attributes:
        ocpp_connector_id: ID connector dương do EVSE dùng trong OCPP.
    """

    ocpp_connector_id: int = Field(..., gt=0)


class ConnectorUpdate(BaseModel):
    """Identity OCPP của connector được phép cập nhật một phần.

    Attributes:
        ocpp_connector_id: ID mới hoặc ``None`` để không cập nhật.
    """

    ocpp_connector_id: int | None = Field(None, gt=0)


class ConnectorResponse(BaseModel):
    """Thông tin connector thuộc topology đã pre-provision.

    Attributes:
        connector_id: UUID nội bộ.
        evse_id: UUID EVSE parent.
        ocpp_connector_id: ID connector trong OCPP.
        created_at: Thời điểm tạo.
        updated_at: Thời điểm cập nhật gần nhất.
        deleted_at: Thời điểm soft-delete, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    connector_id: UUID
    evse_id: UUID
    ocpp_connector_id: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ConnectorListResponse(BaseModel):
    """Danh sách connector phân trang.

    Attributes:
        items: Các connector active ở trang hiện tại.
        total: Tổng connector active của EVSE parent.
        page: Số trang bắt đầu từ một.
        page_size: Số item tối đa trong một trang.
    """

    items: list[ConnectorResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class DeleteResponse(BaseModel):
    """Kết quả soft-delete topology.

    Attributes:
        message: Thông báo nghiệp vụ để trả cho client.
    """

    message: str
