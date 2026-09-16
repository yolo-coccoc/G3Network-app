"""Pydantic response schemas for the charging session monitoring MVP.

The schemas only expose the session aggregate, lifecycle events and
canonical Wh meter samples. Raw OCPP payloads, authorization, payment or
debt are not part of the contract.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.charging_sessions.types import SessionEventType, SessionStatus
from app.libs.common.config import settings


class ChargingSessionResponse(BaseModel):
    """Session aggregate information needed for happy-path monitoring.

    Attributes:
        session_id: Internal UUID of the aggregate.
        station_id: UUID of the station that owns the transaction.
        evse_id: UUID of the EVSE that owns the transaction.
        connector_id: UUID of the connector delivering power.
        ocpp_transaction_id: The transaction identity issued by the station.
        status: ``active`` or ``completed``.
        started_at: The time of Started.
        ended_at: The time of Ended, nullable while active.
        meter_start_wh: The meter reading at the start of the session.
        meter_end_wh: The final meter reading.
        energy_delivered_wh: The difference between the start/end meter
            readings.
        created_at: The time the aggregate was created.
        updated_at: The time of the last update.
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
    """A paginated list of session aggregates for monitoring.

    Attributes:
        items: The most recent sessions in the current page.
        total: The total number of sessions.
        page: The current page, starting at one.
        page_size: The maximum number of items in the page.
    """

    items: list[ChargingSessionResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class ChargingSessionEventResponse(BaseModel):
    """One lifecycle event of a session.

    Attributes:
        event_id: Internal UUID of the event.
        event_occurred_at: The time the event occurred.
        session_id: UUID of the session that owns the event.
        event_type: ``Started``, ``Updated`` or ``Ended``.
    """

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    event_occurred_at: datetime
    session_id: UUID
    event_type: SessionEventType


class ChargingSessionMeterValueResponse(BaseModel):
    """One canonical Wh meter sample of a session.

    Attributes:
        meter_value_id: Internal UUID of the sample.
        sampled_at: The time the sample was taken.
        session_id: UUID of the session that owns the sample.
        value_wh: The energy value in Wh.
    """

    model_config = ConfigDict(from_attributes=True)

    meter_value_id: UUID
    sampled_at: datetime
    session_id: UUID
    value_wh: Decimal


class ChargingSessionEventListResponse(BaseModel):
    """A paginated list of events for a session.

    Attributes:
        items: The events in the current page.
        total: The total number of events for the session.
        page: The current page, starting at one.
        page_size: The maximum number of items in the page.
    """

    items: list[ChargingSessionEventResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class ChargingSessionMeterValueListResponse(BaseModel):
    """A paginated list of meter samples for a session.

    Attributes:
        items: The meter samples in the current page.
        total: The total number of samples for the session.
        page: The current page, starting at one.
        page_size: The maximum number of items in the page.
    """

    items: list[ChargingSessionMeterValueResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)
