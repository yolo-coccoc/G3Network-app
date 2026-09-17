"""Public service ingesting TransactionEvent and individual MeterValues messages, happy path.

The ideal MVP assumes a fixed message order and removes reliability
branching. The caller at the entry boundary still owns commit/rollback of
the transaction.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as repository
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionNotFoundError,
)
from app.domains.charging_sessions.models import (
    ChargingSessionEventModel,
    ChargingSessionMeterValueModel,
    ChargingSessionModel,
)
from app.domains.charging_sessions.schemas import (
    ChargingSessionEventListResponse,
    ChargingSessionEventResponse,
    ChargingSessionListResponse,
    ChargingSessionMeterValueListResponse,
    ChargingSessionMeterValueResponse,
    ChargingSessionResponse,
    StationEnergySummaryResponse,
)
from app.domains.charging_sessions.types import (
    MeterIngestResult,
    MeterSampleInput,
    SessionEventType,
    SessionStatus,
    TransactionIngestResult,
)
from app.libs.common.config import settings


def _utc(value: datetime, field_name: str) -> datetime:
    """Check that the timestamp is timezone-aware and normalize it to UTC.

    Args:
        value: The input timestamp from the adapter or API.
        field_name: The field name used in the error message.

    Returns:
        A timestamp with UTC timezone.

    Raises:
        ChargingSessionInputError: If the timestamp lacks a timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ChargingSessionInputError(f"{field_name} must have a timezone")
    return value.astimezone(timezone.utc)


def _energy(value: Decimal | None, field_name: str) -> Decimal | None:
    """Check that the energy value is a non-negative Decimal.

    Args:
        value: The energy value, which may be nullable.
        field_name: The field name used in the error message.

    Returns:
        A finite, non-negative Decimal, or ``None``.

    Raises:
        ChargingSessionInputError: If the value has the wrong type, is not
            finite, or is negative.
    """
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ChargingSessionInputError(f"{field_name} must be a finite Decimal")
    if value < 0:
        raise ChargingSessionInputError(f"{field_name} must not be negative")
    return value


def _apply_charging_session_meter_end(
    session: ChargingSessionModel, meter_end_wh: Decimal | None
) -> None:
    """Update the final meter reading and energy delivered on the ORM session.

    Args:
        session: The ORM aggregate being processed in the transaction.
        meter_end_wh: The latest meter reading; no change is made if this is
            ``None``.

    Side Effects:
        Updates ``meter_end_wh`` and recomputes the energy delivered if a
        start meter reading is present.
    """
    if meter_end_wh is None:
        return
    session.meter_end_wh = meter_end_wh
    if session.meter_start_wh is not None:
        # The MVP assumes the register increases monotonically; checking for
        # meter reset/decrease belongs to the reliability path and must not
        # be opened up on its own in this service.
        session.energy_delivered_wh = meter_end_wh - session.meter_start_wh


def _paging(page: int, page_size: int) -> tuple[int, int, int]:
    """Normalize monitoring pagination parameters against shared settings.

    Args:
        page: The page requested by the caller.
        page_size: The page size requested by the caller.

    Returns:
        A ``(page, page_size, offset)`` tuple already within API limits.
    """
    normalized_page = max(page, settings.API_DEFAULT_PAGE)
    normalized_page_size = min(
        max(page_size, settings.API_DEFAULT_PAGE_SIZE), settings.API_MAX_PAGE_SIZE
    )
    return (
        normalized_page,
        normalized_page_size,
        (normalized_page - 1) * normalized_page_size,
    )


async def get_charging_session(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionResponse:
    """Get the session aggregate for the monitoring endpoint.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the aggregate to view.

    Returns:
        A session response containing only the active MVP schema.

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.

    Side Effects:
        Performs one aggregate query; does not commit or roll back.
    """
    session = await repository.get_session_by_id(db, session_id)
    if session is None:
        raise ChargingSessionNotFoundError(f"Session '{session_id}' not found")
    return ChargingSessionResponse.model_validate(session)


async def list_charging_sessions(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
) -> ChargingSessionListResponse:
    """Get the list of most recent sessions for the monitoring endpoint.

    Args:
        db: The async session owned by the HTTP boundary.
        page: The page, starting at one.
        page_size: The page size.

    Returns:
        The list of sessions and pagination metadata.

    Side Effects:
        Performs one items query and one count query; no ORM relationships
        are loaded, so the endpoint creates no N+1 queries and does not
        commit/roll back.
    """
    normalized_page, normalized_page_size, offset = _paging(page, page_size)
    sessions = await repository.list_charging_sessions(
        db,
        offset=offset,
        limit=normalized_page_size,
    )
    total = await repository.count_sessions(db)
    return ChargingSessionListResponse(
        items=[ChargingSessionResponse.model_validate(session) for session in sessions],
        total=total,
        page=normalized_page,
        page_size=normalized_page_size,
    )


async def list_charging_session_events(
    db: AsyncSession,
    session_id: UUID,
    *,
    page: int,
    page_size: int,
) -> ChargingSessionEventListResponse:
    """Get paginated lifecycle events for the monitoring endpoint.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session whose events to view.
        page: The page, starting at one.
        page_size: The page size.

    Returns:
        The event response and pagination metadata.

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.

    Side Effects:
        Performs one session lookup and two event queries (items/count); no
        ORM relationships are loaded, so the endpoint creates no N+1
        queries.
    """
    await require_charging_session(db, session_id)
    normalized_page, normalized_page_size, offset = _paging(page, page_size)
    events = await repository.list_charging_session_events(
        db,
        session_id,
        offset=offset,
        limit=normalized_page_size,
    )
    total = await repository.count_session_events(db, session_id)
    return ChargingSessionEventListResponse(
        items=[to_charging_session_event_response(event) for event in events],
        total=total,
        page=normalized_page,
        page_size=normalized_page_size,
    )


async def list_charging_session_meter_values(
    db: AsyncSession,
    session_id: UUID,
    *,
    page: int,
    page_size: int,
) -> ChargingSessionMeterValueListResponse:
    """Get paginated meter samples for the monitoring endpoint.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session whose meter to view.
        page: The page, starting at one.
        page_size: The page size.

    Returns:
        The meter response and pagination metadata.

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.

    Side Effects:
        Performs one session lookup and two meter queries (items/count); no
        ORM relationships are loaded, so the endpoint creates no N+1
        queries.
    """
    await require_charging_session(db, session_id)
    normalized_page, normalized_page_size, offset = _paging(page, page_size)
    meter_values = await repository.list_charging_session_meter_values(
        db,
        session_id,
        offset=offset,
        limit=normalized_page_size,
    )
    total = await repository.count_session_meter_values(db, session_id)
    return ChargingSessionMeterValueListResponse(
        items=[
            to_charging_session_meter_value_response(meter) for meter in meter_values
        ],
        total=total,
        page=normalized_page,
        page_size=normalized_page_size,
    )


async def require_charging_session(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionModel:
    """Ensure the session exists before reading its history.

    Args:
        db: The current async session.
        session_id: UUID of the session to check.

    Returns:
        The existing session aggregate.

    Raises:
        ChargingSessionNotFoundError: If the session is not found.
    """
    session = await repository.get_session_by_id(db, session_id)
    if session is None:
        raise ChargingSessionNotFoundError(f"Session '{session_id}' not found")
    return session


def to_charging_session_event_response(
    event: ChargingSessionEventModel,
) -> ChargingSessionEventResponse:
    """Convert an ORM event into the monitoring response schema.

    Args:
        event: The ORM event already queried by the repository.

    Returns:
        An event response containing no raw payload.
    """
    return ChargingSessionEventResponse.model_validate(event)


def to_charging_session_meter_value_response(
    meter_value: ChargingSessionMeterValueModel,
) -> ChargingSessionMeterValueResponse:
    """Convert an ORM meter sample into the monitoring response schema.

    Args:
        meter_value: The ORM meter sample already queried by the repository.

    Returns:
        A canonical Wh meter response.
    """
    return ChargingSessionMeterValueResponse.model_validate(meter_value)


async def ingest_transaction_event(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    event_type: SessionEventType,
    event_occurred_at: datetime,
    meter_start_wh: Decimal | None = None,
    meter_end_wh: Decimal | None = None,
) -> TransactionIngestResult:
    """Process one TransactionEvent according to the happy-path lifecycle.

    Rule:
        ``Started`` creates a new aggregate; ``Updated`` and ``Ended``
        require the aggregate to already exist, with matching topology and
        messages arriving in the correct order. The event is always
        appended before the aggregate is updated.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the station that raised the transaction.
        evse_id: UUID of the transaction's EVSE.
        connector_id: UUID of the transaction's connector.
        transaction_id: The OCPP transaction identity.
        event_type: The canonical event type.
        event_occurred_at: The event time; must have a timezone.
        meter_start_wh: The meter reading at the start of the session, for
            ``Started``.
        meter_end_wh: The latest meter reading, for ``Updated``/``Ended``.

    Returns:
        A result containing the session UUID, current status and number of
        events appended.

    Raises:
        ChargingSessionInputError: If the input violates the contract or
            the topology does not match.
        ChargingSessionNotFoundError: If the event is not ``Started`` but
            the aggregate does not yet exist.

    Side Effects:
        Creates or updates the aggregate and appends an event in the
        current transaction; does not commit or roll back on its own.
    """
    transaction_id = transaction_id.strip()
    if not transaction_id or len(transaction_id) > 255:
        raise ChargingSessionInputError(
            "transaction_id is empty or exceeds 255 characters"
        )
    occurred_at = _utc(event_occurred_at, "event_occurred_at")
    meter_start = _energy(meter_start_wh, "meter_start_wh")
    meter_end = _energy(meter_end_wh, "meter_end_wh")

    session: ChargingSessionModel | None
    if event_type == SessionEventType.STARTED:
        # Duplicate/idempotency and conflict handling are protected by the
        # unique constraint but have no dedicated branch in the MVP happy
        # path yet.
        session = await repository.create_session(
            db,
            station_id=station_id,
            evse_id=evse_id,
            connector_id=connector_id,
            transaction_id=transaction_id,
            started_at=occurred_at,
            meter_start_wh=meter_start,
        )
    else:
        session = await repository.get_session_by_transaction(
            db, station_id, transaction_id
        )
        if session is None:
            raise ChargingSessionNotFoundError(
                f"Transaction '{transaction_id}' has no Started yet"
            )
        if session.evse_id != evse_id or session.connector_id != connector_id:
            raise ChargingSessionInputError("Transaction topology does not match")

    await repository.insert_event(
        db,
        session_id=session.session_id,
        event_occurred_at=occurred_at,
        event_type=event_type,
    )
    _apply_charging_session_meter_end(session, meter_end)

    if event_type is SessionEventType.ENDED:
        session.ended_at = occurred_at
        session.status = SessionStatus.COMPLETED
    session.updated_at = repository.utc_now()
    return TransactionIngestResult(
        session_id=session.session_id,
        status=session.status,
        event_count=1,
    )


async def ingest_meter_values(
    db: AsyncSession,
    *,
    session_id: UUID,
    sample: MeterSampleInput,
) -> MeterIngestResult:
    """Store one MeterValues message and update the aggregate.

    Rule:
        Each call processes exactly one sample. The MVP assumes messages
        arrive in order and without duplicates; the current sample becomes
        the aggregate's final meter reading.

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the aggregate to update.
        sample: The sample to canonicalize to Wh and append to the history.

    Returns:
        A result containing the session UUID, status and number of samples
        accepted.

    Raises:
        ChargingSessionInputError: If the sample lacks a timezone or the
            energy value is invalid.
        ChargingSessionNotFoundError: If the aggregate does not exist.

    Side Effects:
        Appends a meter sample and updates the aggregate in the same
        transaction; the caller must commit or roll back the transaction at
        the entry boundary.
    """
    session = await repository.get_session_by_id(db, session_id)
    if session is None:
        raise ChargingSessionNotFoundError(f"Session '{session_id}' not found")
    sampled_at = _utc(sample.sampled_at, "sampled_at")
    value_wh = _energy(sample.value_wh, "value_wh")
    if value_wh is None:
        raise ChargingSessionInputError("value_wh is required")
    await repository.insert_meter_value(
        db,
        session_id=session.session_id,
        sampled_at=sampled_at,
        value_wh=value_wh,
    )
    _apply_charging_session_meter_end(session, value_wh)

    session.updated_at = repository.utc_now()
    return MeterIngestResult(
        session_id=session.session_id,
        status=session.status,
        accepted_count=1,
    )


async def get_station_energy_summary(
    db: AsyncSession,
    *,
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> StationEnergySummaryResponse:
    """Total energy sold at a station within a time window (F-C5).

    Pure aggregation over already-stored session data; no writes.

    Args:
        db: The async session owned by the HTTP boundary.
        station_id: UUID of the station to aggregate over.
        start_time: Inclusive lower bound on ``ended_at``.
        end_time: Inclusive upper bound on ``ended_at``.

    Returns:
        Total energy (kWh) and count of completed sessions ending within
        the window. An unknown ``station_id`` returns a zero summary rather
        than a 404 - this domain doesn't own station existence, and a
        report endpoint legitimately answers "no sessions" for one.

    Raises:
        ChargingSessionInputError: If either timestamp lacks a timezone, or
            ``end_time`` isn't after ``start_time``.
    """
    normalized_start = _utc(start_time, "start_time")
    normalized_end = _utc(end_time, "end_time")
    if normalized_end <= normalized_start:
        raise ChargingSessionInputError("end_time must be after start_time")

    total_energy_wh, session_count = await repository.get_station_energy_summary(
        db,
        station_id=station_id,
        start_time=normalized_start,
        end_time=normalized_end,
    )
    return StationEnergySummaryResponse(
        station_id=station_id,
        start_time=normalized_start,
        end_time=normalized_end,
        total_energy_kwh=float(total_energy_wh / Decimal(1000)),
        session_count=session_count,
    )
