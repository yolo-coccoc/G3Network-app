"""Public service ingesting TransactionEvent and individual MeterValues messages, happy path.

The ideal MVP assumes a fixed message order and removes reliability
branching (retry, DLQ, out-of-order recovery, dedup - see
``docs/01-requirements/future.md`` item 27). On top of that, this service
enforces two correctness invariants that hold even on the happy path
(F-B2): a session's lifecycle state can only move forward (an event
arriving after ``COMPLETED`` is refused, not silently applied), and a
meter reading can only move the aggregate's ``meter_end_wh`` forward in
*time* (a sample stamped earlier than the one already applied is
discarded). Neither invariant implements retry/dedup/out-of-order
*recovery* - they only stop the happy path itself from writing a value
nothing can vouch for. The caller at the entry boundary still owns
commit/rollback of the transaction.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as repository
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionNotFoundError,
    ChargingSessionStateError,
)
from app.domains.charging_sessions.models import (
    ChargingSessionEventModel,
    ChargingSessionMeasurementModel,
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
    ENERGY_ACTIVE_IMPORT_REGISTER,
    ENERGY_UNIT_WH,
    MeterIngestResult,
    MeterSampleInput,
    SessionEventType,
    SessionStatus,
    TransactionIngestResult,
    TransactionSessionReference,
)
from app.libs.common.config import settings

logger = logging.getLogger(__name__)


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


def _seq_no(value: int | None, field_name: str) -> int | None:
    """Check that the OCPP sequence number is a non-negative integer (F-B2).

    Args:
        value: The sequence number from the adapter, nullable for callers
            that have none.
        field_name: The field name used in the error message.

    Returns:
        The validated sequence number, or ``None``.

    Raises:
        ChargingSessionInputError: If the value is not an ``int``, is a
            ``bool``, or is negative. ``bool`` is rejected explicitly
            because ``isinstance(True, int)`` is ``True`` in Python - a
            stray ``True`` must not silently collide with a real
            ``seqNo`` of 0.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChargingSessionInputError(f"{field_name} must be an int")
    if value < 0:
        raise ChargingSessionInputError(f"{field_name} must not be negative")
    return value


def _apply_charging_session_meter_end(
    session: ChargingSessionModel,
    meter_end_wh: Decimal | None,
    meter_end_sampled_at: datetime,
) -> None:
    """Update the final meter reading and energy delivered on the ORM session.

    Args:
        session: The ORM aggregate being processed in the transaction.
        meter_end_wh: The latest meter reading; no change is made if this is
            ``None``.
        meter_end_sampled_at: The measurement time of ``meter_end_wh``,
            already normalized to UTC (F-B2).

    Side Effects:
        If a watermark (``session.meter_end_sampled_at``) is already
        stored and ``meter_end_sampled_at`` is *older*, the update is
        discarded (logged at WARNING, operator-visible) and neither
        ``meter_end_wh`` nor ``energy_delivered_wh`` changes - a message
        that arrived out of order must not overwrite a newer reading with
        a stale one. Ties (``==``) apply: one OCPP message can carry
        several samples sharing one timestamp, and rejecting ties would
        drop legitimate ones. This is a *time*-ordering check only, never
        a value check: a register that decreases while time still moves
        forward (a meter reset) still applies and still yields a wrong
        total - reconciling that is the deferred reliability path
        (``future.md`` item 27) and must not be opened up on its own here.
    """
    if meter_end_wh is None:
        return
    if (
        session.meter_end_sampled_at is not None
        and meter_end_sampled_at < session.meter_end_sampled_at
    ):
        logger.warning(
            "Discarded stale charging meter reading",
            extra={
                "session_id": str(session.session_id),
                "stale_sampled_at": meter_end_sampled_at.isoformat(),
                "current_sampled_at": session.meter_end_sampled_at.isoformat(),
                "stale_value_wh": str(meter_end_wh),
            },
        )
        return
    session.meter_end_wh = meter_end_wh
    session.meter_end_sampled_at = meter_end_sampled_at
    if session.meter_start_wh is not None:
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
    measurement: ChargingSessionMeasurementModel,
) -> ChargingSessionMeterValueResponse:
    """Convert an energy measurement into the ``/meter-values`` response schema.

    The response contract predates the unified measurements table and is kept
    unchanged: ``value_wh`` is the measurement's canonical Wh ``value``, shown
    with three decimals like the ``Numeric(24, 3)`` column it used to come
    from (the new column keeps six for non-energy measurands).

    Args:
        measurement: An energy-register measurement queried by the repository.

    Returns:
        A canonical Wh meter response.
    """
    return ChargingSessionMeterValueResponse(
        meter_value_id=measurement.measurement_id,
        sampled_at=measurement.sampled_at,
        session_id=measurement.session_id,
        value_wh=measurement.value.quantize(Decimal("0.001")),
    )


async def ingest_transaction_event(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    event_type: SessionEventType,
    event_occurred_at: datetime,
    seq_no: int | None,
    meter_start_wh: Decimal | None = None,
    meter_end_wh: Decimal | None = None,
    meter_end_sampled_at: datetime | None = None,
    id_tag: str | None = None,
    stop_reason: str | None = None,
    meter_stop_wh: Decimal | None = None,
) -> TransactionIngestResult:
    """Process one TransactionEvent according to the happy-path lifecycle.

    Rule:
        ``Started`` creates a new aggregate; ``Updated`` and ``Ended``
        require the aggregate to already exist, with matching topology.
        Once a session is ``COMPLETED``, every subsequent event for it is
        refused (F-B2) - a duplicate ``Ended`` or a late ``Updated`` must
        not silently re-mutate a finished record. The event is always
        appended before the aggregate is updated, and a stale meter
        reading (older ``meter_end_sampled_at`` than one already applied)
        is discarded rather than overwriting a newer value - see
        ``_apply_charging_session_meter_end``.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the station that raised the transaction.
        evse_id: UUID of the transaction's EVSE.
        connector_id: UUID of the transaction's connector.
        transaction_id: The OCPP transaction identity.
        event_type: The canonical event type.
        event_occurred_at: The event time; must have a timezone.
        seq_no: OCPP's own sequence number for this event, nullable for a
            caller with none (F-B2). Persisted, not yet used for dedup.
        meter_start_wh: The meter reading at the start of the session, for
            ``Started``.
        meter_end_wh: The latest meter reading, for ``Updated``/``Ended``.
        meter_end_sampled_at: The measurement time of ``meter_end_wh``, if
            the adapter has the embedded sample's own timestamp; falls
            back to ``event_occurred_at`` when omitted (F-B2).
        id_tag: The idTag that started the session, for ``Started`` only
            (OCPP 1.6J; at most 20 characters). Stored as sent.
        stop_reason: Why the session stopped, for ``Ended`` only (at most 30
            characters).
        meter_stop_wh: The charger's authoritative closing meter reading, for
            ``Ended`` only. Always stored, even when ``meter_end_wh`` is
            discarded by the stale-sample rule, because it is the
            charger's own final figure.

    Returns:
        A result containing the session UUID, current status and number of
        events appended.

    Raises:
        ChargingSessionInputError: If the input violates the contract (an
            ``id_tag`` longer than 20 or a ``stop_reason`` longer than 30
            characters, a negative energy) or the topology does not match.
        ChargingSessionNotFoundError: If the event is not ``Started`` but
            the aggregate does not yet exist.
        ChargingSessionStateError: If the session is already ``COMPLETED``.

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
    validated_seq_no = _seq_no(seq_no, "seq_no")
    meter_start = _energy(meter_start_wh, "meter_start_wh")
    meter_end = _energy(meter_end_wh, "meter_end_wh")
    meter_stop = _energy(meter_stop_wh, "meter_stop_wh")
    if id_tag is not None and len(id_tag) > 20:
        raise ChargingSessionInputError("id_tag exceeds 20 characters")
    if stop_reason is not None and len(stop_reason) > 30:
        raise ChargingSessionInputError("stop_reason exceeds 30 characters")
    sampled_at = (
        _utc(meter_end_sampled_at, "meter_end_sampled_at")
        if meter_end_sampled_at is not None
        else occurred_at
    )

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
            id_tag=id_tag,
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
        if session.status is SessionStatus.COMPLETED:
            # COMPLETED is terminal: re-stamping ended_at or re-applying a
            # meter reading would silently rewrite a finished session and
            # corrupt F-C5's energy totals. Distinguishing a harmless
            # replay from a genuinely different late event needs
            # seq_no-keyed dedup (future.md item 27), so every post-Ended
            # event is refused the same way, whether it's a duplicate
            # Ended or a late Updated.
            raise ChargingSessionStateError(
                f"Transaction '{transaction_id}' is already completed"
            )

    await repository.insert_event(
        db,
        session_id=session.session_id,
        event_occurred_at=occurred_at,
        event_type=event_type,
        seq_no=validated_seq_no,
    )
    _apply_charging_session_meter_end(session, meter_end, sampled_at)

    if event_type is SessionEventType.ENDED:
        session.ended_at = occurred_at
        session.status = SessionStatus.COMPLETED
        session.stop_reason = stop_reason
        session.meter_stop_wh = meter_stop
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
        Each call processes exactly one sample. The sample row is always
        appended to history (append-only), but the aggregate's
        ``meter_end_wh`` only advances if the sample is not stale - see
        ``_apply_charging_session_meter_end`` (F-B2). An event for an
        already-``COMPLETED`` session is refused (F-B2), for the same
        reason as ``ingest_transaction_event``'s guard: a MeterValues
        landing after ``Ended`` must not silently rewrite a finished
        session's energy total.

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
        ChargingSessionStateError: If the session is already ``COMPLETED``.

    Side Effects:
        Appends a meter sample and updates the aggregate in the same
        transaction; the caller must commit or roll back the transaction at
        the entry boundary.
    """
    session = await repository.get_session_by_id(db, session_id)
    if session is None:
        raise ChargingSessionNotFoundError(f"Session '{session_id}' not found")
    if session.status is SessionStatus.COMPLETED:
        raise ChargingSessionStateError(f"Session '{session_id}' is already completed")
    sampled_at = _utc(sample.sampled_at, "sampled_at")
    value_wh = _energy(sample.value_wh, "value_wh")
    if value_wh is None:
        raise ChargingSessionInputError("value_wh is required")
    await repository.insert_measurement(
        db,
        session_id=session.session_id,
        sampled_at=sampled_at,
        measurand=ENERGY_ACTIVE_IMPORT_REGISTER,
        value=value_wh,
        unit=ENERGY_UNIT_WH,
        context=sample.context,
    )
    _apply_charging_session_meter_end(session, value_wh, sampled_at)

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


async def allocate_ocpp16_transaction_id(db: AsyncSession) -> int:
    """Allocate the next OCPP 1.6J transaction ID.

    OCPP 1.6J requires the CSMS (this backend) to assign the integer
    ``transactionId`` in the ``StartTransaction`` response. IDs come from a
    database sequence so they survive restarts and never repeat.

    Args:
        db: The async session owned by the entry boundary.

    Returns:
        The next integer transaction ID (32-bit range).

    Side Effects:
        Advances the sequence; a rolled-back transaction leaves a harmless
        gap in the numbers.
    """
    return await repository.next_ocpp16_transaction_id(db)


async def has_active_session_on_connector(db: AsyncSession, connector_id: UUID) -> bool:
    """Tell whether a connector already has an open (``active``) session.

    Args:
        db: The async session owned by the entry boundary.
        connector_id: UUID of the connector.

    Returns:
        ``True`` if at least one session on the connector is still active.
    """
    return await repository.count_active_sessions_by_connector_id(db, connector_id) > 0


async def resolve_session_by_transaction(
    db: AsyncSession,
    *,
    station_id: UUID,
    transaction_id: str,
) -> TransactionSessionReference:
    """Look up a session by the OCPP transaction identity a charger sent.

    Used by messages that carry a ``transactionId`` but no topology (OCPP 1.6J
    ``StopTransaction`` and ``MeterValues``), so nothing has to be remembered
    per connection: the database is the source of truth.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the station that sent the message.
        transaction_id: The transaction identity, as stored.

    Returns:
        A frozen reference with the session's ID, topology and status.

    Raises:
        ChargingSessionNotFoundError: If the station has no such transaction.
    """
    session = await repository.get_session_by_transaction(
        db, station_id, transaction_id
    )
    if session is None:
        raise ChargingSessionNotFoundError(
            f"Transaction '{transaction_id}' not found for this station"
        )
    return TransactionSessionReference(
        session_id=session.session_id,
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        status=session.status,
    )
