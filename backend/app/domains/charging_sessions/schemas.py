"""Pydantic schemas for the charging session endpoints.

The schemas expose the session (plus, on the detail read, a summary computed
from its measurements), the energy samples, the other measurements, the
per-station energy summary and time series, and the scan that creates a
PENDING session. Raw OCPP payloads are not part of the contract, and the
single-use token is returned only to the scan that created it (IS-07).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.charging_sessions.types import (
    EnergySeriesGranularity,
    SessionStatus,
)
from app.libs.common.config import settings


class ChargingSessionScanRequest(BaseModel):
    """The scan of a charger's QR code that creates a PENDING session (CE-10).

    Attributes:
        station_id: UUID of the charger the QR code names.
        organization_id: UUID of the organization that pays; internal staff
            only, defaults to the organization the caller acts for.
        vehicle_id: UUID of the truck being charged, if known (CE-13).
    """

    station_id: UUID
    organization_id: UUID | None = None
    vehicle_id: UUID | None = None


class ChargingSessionScanResponse(BaseModel):
    """The PENDING session a scan created.

    Attributes:
        session_id: UUID of the new session.
        station_id: UUID of the charger.
        status: Always ``PENDING``.
        id_token: The single-use token for the remote start; shown only here.
    """

    session_id: UUID
    station_id: UUID
    status: SessionStatus
    id_token: str


class ChargingSessionResponse(BaseModel):
    """A charging session as the monitoring endpoints show it.

    Attributes:
        session_id: Internal UUID of the session.
        station_id: UUID of the charger.
        evse_id: UUID of the EVSE, ``null`` until the charger starts.
        connector_id: UUID of the connector, ``null`` until the charger starts.
        organization_id: UUID of the organization that pays.
        started_by: UUID of the user who scanned the QR code.
        vehicle_id: UUID of the truck being charged, nullable.
        ocpp_transaction_id: The charger's transaction identity, ``null``
            until the charger starts.
        status: ``PENDING``, ``ACTIVE``, ``COMPLETED`` or ``ABANDONED``.
        started_at: The charger's start time, ``null`` until it starts.
        ended_at: The charger's stop time, ``null`` until completed.
        meter_start_wh: The meter reading declared at the start.
        stop_reason: Why the session stopped, as reported, nullable.
        meter_stop_wh: The meter reading declared in the stop message, the
            billing figure (CE-12), nullable.
        created_at: When the session was created: the scan time.
        updated_at: The time of the last update.
    """

    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    station_id: UUID
    evse_id: UUID | None
    connector_id: UUID | None
    organization_id: UUID
    started_by: UUID
    vehicle_id: UUID | None
    ocpp_transaction_id: str | None
    status: SessionStatus
    started_at: datetime | None
    ended_at: datetime | None
    meter_start_wh: Decimal | None
    stop_reason: str | None
    meter_stop_wh: Decimal | None
    created_at: datetime
    updated_at: datetime


class ChargingSessionDetailResponse(ChargingSessionResponse):
    """One session plus a summary computed at read time (F-B2).

    The summary fields are derived from the session and its measurements on
    every read, never stored.

    Attributes:
        energy_delivered_wh: Stop reading minus start reading; while the
            session runs the newest outlet energy measurement stands in for
            the stop reading. ``null`` without a start reading.
        duration_seconds: ``ended_at - started_at``, or now - ``started_at``
            while the session is active; never negative; ``0`` before the
            start.
        soc_start_percent: Value of the session's first ``SoC`` sample,
            nullable.
        soc_end_percent: Value of the session's latest ``SoC`` sample,
            nullable (the latest so far while active).
        max_power_kw: Highest outlet ``Power.Active.Import`` sample in kW
            (stored in W, CE-14), nullable.
    """

    energy_delivered_wh: Decimal | None
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
        value: The reading, in the fixed unit of a known measurand (CE-14).
        unit: Unit of ``value``, nullable for a vendor measurand.
        context: Why the charger sent the reading (OCPP default stored when
            it omitted it).
        phase: Electrical phase, nullable.
        measurement_location: Where it was measured (``Outlet``, ``EV``...).
    """

    model_config = ConfigDict(from_attributes=True)

    measurement_id: UUID
    sampled_at: datetime
    session_id: UUID
    measurand: str
    value: Decimal
    unit: str | None
    context: str
    phase: str | None
    measurement_location: str


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
        total_energy_kwh: Sum of stop minus start readings (in kWh) across
            completed sessions ending within the window.
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
    each session (its ``meter_start_wh`` at ``started_at``, its stored outlet
    samples, and its ``meter_stop_wh`` at ``ended_at``), each delta attributed to the bucket of
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
