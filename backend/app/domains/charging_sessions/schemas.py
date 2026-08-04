"""Pydantic response schemas cho monitoring charging session MVP.

Schema chỉ expose aggregate session, lifecycle event và meter sample canonical
Wh. Không đưa raw OCPP payload, authorization, payment hoặc debt vào contract.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.charging_sessions.types import SessionEventType, SessionStatus
from app.libs.common.config import settings


class ChargingSessionResponse(BaseModel):
    """Thông tin aggregate session cần cho monitoring happy path.

    Attributes:
        session_id: UUID nội bộ của aggregate.
        station_id: UUID station sở hữu transaction.
        evse_id: UUID EVSE sở hữu transaction.
        connector_id: UUID connector cấp điện.
        ocpp_transaction_id: Transaction identity do trụ cấp.
        status: ``active`` hoặc ``completed``.
        started_at: Thời điểm Started.
        ended_at: Thời điểm Ended, nullable khi active.
        meter_start_wh: Meter đầu phiên.
        meter_end_wh: Meter cuối cùng.
        energy_delivered_wh: Chênh lệch meter đầu/cuối.
        created_at: Thời điểm tạo aggregate.
        updated_at: Thời điểm cập nhật cuối.
    """

    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    station_id: UUID
    evse_id: UUID
    connector_id: UUID
    ocpp_transaction_id: str
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None
    meter_start_wh: Decimal | None
    meter_end_wh: Decimal | None
    energy_delivered_wh: Decimal | None
    created_at: datetime
    updated_at: datetime


class ChargingSessionListResponse(BaseModel):
    """Danh sách aggregate session phân trang cho monitoring.

    Attributes:
        items: Các session mới nhất trong trang hiện tại.
        total: Tổng số session.
        page: Trang hiện tại, bắt đầu từ một.
        page_size: Số item tối đa trong trang.
    """

    items: list[ChargingSessionResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class ChargingSessionEventResponse(BaseModel):
    """Một lifecycle event của session.

    Attributes:
        event_id: UUID nội bộ event.
        event_occurred_at: Thời điểm event phát sinh.
        session_id: UUID session sở hữu event.
        event_type: ``Started``, ``Updated`` hoặc ``Ended``.
    """

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    event_occurred_at: datetime
    session_id: UUID
    event_type: SessionEventType


class ChargingSessionMeterValueResponse(BaseModel):
    """Một meter sample canonical Wh của session.

    Attributes:
        meter_value_id: UUID nội bộ sample.
        sampled_at: Thời điểm lấy mẫu.
        session_id: UUID session sở hữu sample.
        value_wh: Giá trị energy theo Wh.
    """

    model_config = ConfigDict(from_attributes=True)

    meter_value_id: UUID
    sampled_at: datetime
    session_id: UUID
    value_wh: Decimal


class ChargingSessionEventListResponse(BaseModel):
    """Danh sách event phân trang cho một session.

    Attributes:
        items: Event trong trang hiện tại.
        total: Tổng số event của session.
        page: Trang hiện tại, bắt đầu từ một.
        page_size: Số item tối đa trong trang.
    """

    items: list[ChargingSessionEventResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class ChargingSessionMeterValueListResponse(BaseModel):
    """Danh sách meter sample phân trang cho một session.

    Attributes:
        items: Meter sample trong trang hiện tại.
        total: Tổng số sample của session.
        page: Trang hiện tại, bắt đầu từ một.
        page_size: Số item tối đa trong trang.
    """

    items: list[ChargingSessionMeterValueResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)
