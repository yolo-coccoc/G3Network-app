"""Pydantic response schemas for the charging session monitoring MVP.

The schemas only expose the session aggregate (plus, on the detail read, a
summary computed from its measurements), lifecycle events, canonical Wh meter
samples, the other measurements, the per-station energy summary and the
per-station energy time series. Raw OCPP payloads, authorization, payment or
debt are not part of the contract.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.charging_sessions.types import (
    EnergySeriesGranularity,
    SessionEventType,
    SessionStatus,
)
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
        meter_end_wh: The most recently observed meter reading, as of
            ``meter_end_sampled_at`` (F-B2).
        meter_end_sampled_at: The measurement time of ``meter_end_wh``,
            nullable (F-B2).
        energy_delivered_wh: The difference between the start/end meter
            readings.
        id_tag: The idTag that started the session (OCPP 1.6J), nullable.
        stop_reason: Why the session stopped, as reported, nullable.
        meter_stop_wh: The charger's authoritative closing meter reading
            (OCPP 1.6J ``meterStop``), nullable.
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
    meter_end_sampled_at: datetime | None
    energy_delivered_wh: Decimal | None
    id_tag: str | None
    stop_reason: str | None
    meter_stop_wh: Decimal | None
    created_at: datetime
    updated_at: datetime


class ChargingSessionDetailResponse(ChargingSessionResponse):
    """One session aggregate plus a summary computed at read time (F-B2).

    The summary fields are derived from ``charging_session_measurements`` on
    every read, never stored. OCPP 2.0.1 sessions store only the energy
    register, so their SoC and power fields are ``null``.

    Attributes:
        duration_seconds: ``ended_at - started_at``, or now - ``started_at``
            while the session is active; never negative.
        soc_start_percent: Value of the session's first ``SoC`` sample,
            nullable.
        soc_end_percent: Value of the session's latest ``SoC`` sample,
            nullable (the latest so far while active).
        max_power_kw: Highest ``Power.Active.Import`` sample, converted to
            kW (``W`` samples are divided by 1000; samples in another unit
            are ignored), nullable.
    """

    duration_seconds: int = Field(..., ge=0)
    soc_start_percent: float | None
    soc_end_percent: float | None
    max_power_kw: float | None


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
        seq_no: OCPP's own sequence number, nullable (F-B2).
    """

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    event_occurred_at: datetime
    session_id: UUID
    event_type: SessionEventType
    seq_no: int | None


class ChargingSessionMeterValueResponse(BaseModel):
    """One canonical Wh meter sample of a session.

    Attributes:
        meter_value_id: Internal UUID of the sample.
        sampled_at: The time the sample was taken.
        session_id: UUID of the session that owns the sample.
        value_wh: The energy value in Wh - the OCPP adapter owns
            normalizing measurand/unit into this canonical form (F-B2).
    """

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


class ChargingSessionMeasurementResponse(BaseModel):
    """One measurement of a session (any measurand).

    Attributes:
        measurement_id: Internal UUID of the measurement.
        sampled_at: The time of measurement.
        session_id: UUID of the session that owns it.
        measurand: OCPP measurand name as reported (``SoC``,
            ``Power.Active.Import``, a vendor-specific name…).
        value: The reading; canonical Wh for the energy register.
        unit: Unit of ``value``, nullable.
        context: OCPP reading context, nullable.
        phase: Electrical phase, nullable.
        location: Measurement location, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    measurement_id: UUID
    sampled_at: datetime
    session_id: UUID
    measurand: str
    value: Decimal
    unit: str | None
    context: str | None
    phase: str | None
    location: str | None


class ChargingSessionMeasurementListResponse(BaseModel):
    """A paginated list of a session's measurements.

    Attributes:
        items: The measurements in the current page.
        total: The total number of matching measurements.
        page: The current page, starting at one.
        page_size: The maximum number of items in the page.
    """

    items: list[ChargingSessionMeasurementResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


class StationEnergySummaryResponse(BaseModel):
    """Total energy sold at a station within a queried time window (F-C5).

    An aggregate over completed sessions only - an active session's energy
    isn't final yet, so it's excluded until it ends.

    Attributes:
        station_id: UUID of the station queried.
        start_time: Inclusive lower bound of the window, as given.
        end_time: Inclusive upper bound of the window, as given.
        total_energy_kwh: Sum of ``energy_delivered_wh`` (converted to kWh)
            across completed sessions ending within the window.
        session_count: Number of completed sessions included in the sum.
    """

    station_id: UUID
    start_time: datetime
    end_time: datetime
    total_energy_kwh: float = Field(..., ge=0)
    session_count: int = Field(..., ge=0)


class StationEnergySeriesBucketResponse(BaseModel):
    """Energy metered at a station within one time bucket (F-C5).

    Attributes:
        bucket_start: Start of the bucket as a UTC instant; the bucket is a
            clock hour or a calendar day of ``APP_REPORT_TIMEZONE``.
        energy_kwh: Energy attributed to the bucket, in kWh; ``0`` for an
            empty bucket.
    """

    bucket_start: datetime
    energy_kwh: float = Field(..., ge=0)


class StationEnergySeriesResponse(BaseModel):
    """Dense energy time series of a station (F-C5).

    Energy comes from energy-register deltas between consecutive readings of
    each session (its ``meter_start_wh`` at ``started_at``, its stored
    samples, and its latest reading), each delta attributed to the bucket of
    the later reading; a decreasing register contributes nothing. Active
    sessions count too, so the series can differ from the completed-session
    summary.

    Attributes:
        station_id: UUID of the station queried.
        start_time: Inclusive window start, in UTC.
        end_time: Exclusive window end, in UTC.
        granularity: ``hour`` or ``day``.
        report_timezone: The IANA time zone the buckets were cut in.
        total_energy_kwh: Sum of every bucket.
        items: One bucket per hour/day from the bucket containing
            ``start_time`` up to the one containing ``end_time``, in time
            order, empty buckets included.
    """

    station_id: UUID
    start_time: datetime
    end_time: datetime
    granularity: EnergySeriesGranularity
    report_timezone: str
    total_energy_kwh: float = Field(..., ge=0)
    items: list[StationEnergySeriesBucketResponse]
