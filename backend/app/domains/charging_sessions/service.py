"""Public service ingesting TransactionEvent and individual MeterValues messages, happy path.

The ideal MVP assumes a fixed message order and removes reliability
branching (retry, DLQ, out-of-order recovery, dedup - see
``docs/decisions/deferred.md`` item 27). On top of that, this service
enforces two correctness invariants that hold even on the happy path
(F-B2): a session's lifecycle state can only move forward (an event
arriving after ``COMPLETED`` is refused, not silently applied), and a
meter reading can only move the aggregate's ``meter_end_wh`` forward in
*time* (a sample stamped earlier than the one already applied is
discarded). Neither invariant implements retry/dedup/out-of-order
*recovery* - they only stop the happy path itself from writing a value
nothing can vouch for. The caller at the entry boundary still owns
commit/rollback of the transaction.

The module also serves the read-only monitoring endpoints (session detail
with its read-time summary, the filtered session list, events, energy
samples, measurements, the station energy summary and the station energy
time series). The ingestion functions, ``allocate_ocpp16_transaction_id``,
``has_active_session_on_connector`` and ``resolve_session_by_transaction``
are the public entry points the ``charging_stations`` OCPP adapters call;
``resolve_station_energy_total`` is the one its all-stations energy report
calls.
"""

import bisect
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_session_repository
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
    ChargingSessionDetailResponse,
    ChargingSessionEventListResponse,
    ChargingSessionEventResponse,
    ChargingSessionListResponse,
    ChargingSessionMeasurementListResponse,
    ChargingSessionMeasurementResponse,
    ChargingSessionMeterValueListResponse,
    ChargingSessionMeterValueResponse,
    ChargingSessionResponse,
    StationEnergySeriesBucketResponse,
    StationEnergySeriesResponse,
    StationEnergySummaryResponse,
)
from app.domains.charging_sessions.types import (
    ENERGY_ACTIVE_IMPORT_REGISTER,
    ENERGY_UNIT_WH,
    ChargingSessionListFilter,
    EnergySeriesGranularity,
    MeasurementInput,
    MeterIngestResult,
    MeterSampleInput,
    SessionEventType,
    SessionStatus,
    StationEnergyTotal,
    TransactionIngestResult,
    TransactionSessionReference,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import PageWindow, normalize_page_window

logger = logging.getLogger(__name__)

# Measurands read by the session summary (F-B2). Names as OCPP defines them;
# only OCPP 1.6J sessions store them today (2.0.1 stores energy only).
_SOC_MEASURAND: Final[str] = "SoC"
_POWER_ACTIVE_IMPORT_MEASURAND: Final[str] = "Power.Active.Import"
# Conversion of a stored power unit (lowercased) into kW. OCPP's default
# power unit is W, so a sample stored without a unit is read as W; any other
# unit is not convertible and is left out of ``max_power_kw``.
_POWER_UNIT_FACTORS_KW: Final[dict[str | None, Decimal]] = {
    None: Decimal("0.001"),
    "w": Decimal("0.001"),
    "kw": Decimal(1),
}
_WH_PER_KWH: Final[Decimal] = Decimal(1000)

# Input length limits, mirroring the widths of the columns the values are
# stored in (``charging_sessions`` and ``charging_session_measurements``):
# validating here turns an over-long value into a ChargingSessionInputError
# instead of a database error at flush time.
_TRANSACTION_ID_MAX_LENGTH: Final[int] = 255
_ID_TAG_MAX_LENGTH: Final[int] = 20
_STOP_REASON_MAX_LENGTH: Final[int] = 30
_MEASURAND_MAX_LENGTH: Final[int] = 60
_MEASUREMENT_UNIT_MAX_LENGTH: Final[int] = 20
_MEASUREMENT_CONTEXT_MAX_LENGTH: Final[int] = 30
_MEASUREMENT_PHASE_MAX_LENGTH: Final[int] = 10
_MEASUREMENT_LOCATION_MAX_LENGTH: Final[int] = 20


@dataclasses.dataclass(frozen=True, slots=True)
class _ValidatedTransactionEvent:
    """The input of one TransactionEvent after validation and normalization.

    Private to this module: produced by ``_validate_transaction_event`` and
    consumed by ``ingest_transaction_event`` in the same call.

    Attributes:
        transaction_id: The OCPP transaction identity, stripped.
        occurred_at: The event time, in UTC.
        seq_no: OCPP's own sequence number, or ``None``.
        meter_start_wh: The start reading, or ``None``.
        meter_end_wh: The latest reading, or ``None``.
        meter_end_sampled_at: The time of ``meter_end_wh`` in UTC, defaulting
            to ``occurred_at``.
        meter_stop_wh: The charger's closing reading, or ``None``.
        id_tag: The idTag that started the session, or ``None``.
        stop_reason: Why the session stopped, or ``None``.
    """

    transaction_id: str
    occurred_at: datetime
    seq_no: int | None
    meter_start_wh: Decimal | None
    meter_end_wh: Decimal | None
    meter_end_sampled_at: datetime
    meter_stop_wh: Decimal | None
    id_tag: str | None
    stop_reason: str | None


def _normalize_utc(value: datetime, field_name: str) -> datetime:
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


def _validate_energy(value: Decimal | None, field_name: str) -> Decimal | None:
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


def _validate_seq_no(value: int | None, field_name: str) -> int | None:
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


def _validate_transaction_event(
    *,
    transaction_id: str,
    event_occurred_at: datetime,
    seq_no: int | None,
    meter_start_wh: Decimal | None,
    meter_end_wh: Decimal | None,
    meter_end_sampled_at: datetime | None,
    id_tag: str | None,
    stop_reason: str | None,
    meter_stop_wh: Decimal | None,
) -> _ValidatedTransactionEvent:
    """Validate and normalize every input field of one TransactionEvent.

    Pure: performs no I/O, so an invalid event is refused before anything
    is read or written. See ``ingest_transaction_event`` for the meaning of
    each argument.

    Args:
        transaction_id: The OCPP transaction identity, as sent.
        event_occurred_at: The event time; must have a timezone.
        seq_no: OCPP's own sequence number, nullable.
        meter_start_wh: The start reading, nullable.
        meter_end_wh: The latest reading, nullable.
        meter_end_sampled_at: The time of ``meter_end_wh``, nullable.
        id_tag: The idTag, nullable.
        stop_reason: The stop reason, nullable.
        meter_stop_wh: The closing reading, nullable.

    Returns:
        The validated values, timestamps in UTC.

    Raises:
        ChargingSessionInputError: On an empty or over-long identity,
            ``id_tag`` or ``stop_reason``, a naive timestamp, an invalid
            ``seq_no`` or a negative/non-finite energy value.
    """
    normalized_transaction_id = transaction_id.strip()
    if (
        not normalized_transaction_id
        or len(normalized_transaction_id) > _TRANSACTION_ID_MAX_LENGTH
    ):
        raise ChargingSessionInputError(
            f"transaction_id is empty or exceeds {_TRANSACTION_ID_MAX_LENGTH} "
            "characters"
        )
    occurred_at = _normalize_utc(event_occurred_at, "event_occurred_at")
    validated_seq_no = _validate_seq_no(seq_no, "seq_no")
    validated_meter_start_wh = _validate_energy(meter_start_wh, "meter_start_wh")
    validated_meter_end_wh = _validate_energy(meter_end_wh, "meter_end_wh")
    validated_meter_stop_wh = _validate_energy(meter_stop_wh, "meter_stop_wh")
    if id_tag is not None and len(id_tag) > _ID_TAG_MAX_LENGTH:
        raise ChargingSessionInputError(
            f"id_tag exceeds {_ID_TAG_MAX_LENGTH} characters"
        )
    if stop_reason is not None and len(stop_reason) > _STOP_REASON_MAX_LENGTH:
        raise ChargingSessionInputError(
            f"stop_reason exceeds {_STOP_REASON_MAX_LENGTH} characters"
        )
    return _ValidatedTransactionEvent(
        transaction_id=normalized_transaction_id,
        occurred_at=occurred_at,
        seq_no=validated_seq_no,
        meter_start_wh=validated_meter_start_wh,
        meter_end_wh=validated_meter_end_wh,
        # F-B2: the reading's own sample time when the adapter has it,
        # otherwise the event time.
        meter_end_sampled_at=(
            _normalize_utc(meter_end_sampled_at, "meter_end_sampled_at")
            if meter_end_sampled_at is not None
            else occurred_at
        ),
        meter_stop_wh=validated_meter_stop_wh,
        id_tag=id_tag,
        stop_reason=stop_reason,
    )


def _validate_measurement(sample: MeasurementInput) -> MeasurementInput:
    """Validate one non-energy measurement and normalize its time to UTC.

    Pure: performs no I/O. Values may be negative (a temperature, an
    exported power) and vendor-specific measurand names are accepted.

    Args:
        sample: The measurement as normalized by the OCPP adapter.

    Returns:
        A copy of the sample with ``sampled_at`` in UTC.

    Raises:
        ChargingSessionInputError: On an empty or over-long measurand, a
            field longer than its column, a non-finite value or a naive
            timestamp.
    """
    if not sample.measurand or len(sample.measurand) > _MEASURAND_MAX_LENGTH:
        raise ChargingSessionInputError(
            f"measurand must be 1-{_MEASURAND_MAX_LENGTH} characters"
        )
    for field_name, field_value, max_length in (
        ("unit", sample.unit, _MEASUREMENT_UNIT_MAX_LENGTH),
        ("context", sample.context, _MEASUREMENT_CONTEXT_MAX_LENGTH),
        ("phase", sample.phase, _MEASUREMENT_PHASE_MAX_LENGTH),
        ("location", sample.location, _MEASUREMENT_LOCATION_MAX_LENGTH),
    ):
        if field_value is not None and len(field_value) > max_length:
            raise ChargingSessionInputError(
                f"{field_name} exceeds {max_length} characters"
            )
    if not isinstance(sample.value, Decimal) or not sample.value.is_finite():
        raise ChargingSessionInputError("value must be a finite Decimal")
    return dataclasses.replace(
        sample, sampled_at=_normalize_utc(sample.sampled_at, "sampled_at")
    )


def _apply_charging_session_meter_end(
    session_record: ChargingSessionModel,
    meter_end_wh: Decimal | None,
    meter_end_sampled_at: datetime,
) -> None:
    """Update the final meter reading and energy delivered on the ORM session.

    Args:
        session_record: The ORM aggregate being processed in the transaction.
        meter_end_wh: The latest meter reading; no change is made if this is
            ``None``.
        meter_end_sampled_at: The measurement time of ``meter_end_wh``,
            already normalized to UTC (F-B2).

    Side Effects:
        If a watermark (``session_record.meter_end_sampled_at``) is already
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
        (``deferred.md`` item 27) and must not be opened up on its own here.
    """
    if meter_end_wh is None:
        return
    if (
        session_record.meter_end_sampled_at is not None
        and meter_end_sampled_at < session_record.meter_end_sampled_at
    ):
        logger.warning(
            "Discarded stale charging meter reading",
            extra={
                "session_id": str(session_record.session_id),
                "stale_sampled_at": meter_end_sampled_at.isoformat(),
                "current_sampled_at": (session_record.meter_end_sampled_at.isoformat()),
                "stale_value_wh": str(meter_end_wh),
            },
        )
        return
    session_record.meter_end_wh = meter_end_wh
    session_record.meter_end_sampled_at = meter_end_sampled_at
    if session_record.meter_start_wh is not None:
        session_record.energy_delivered_wh = (
            meter_end_wh - session_record.meter_start_wh
        )


def _touch_session(session_record: ChargingSessionModel) -> None:
    """Stamp the aggregate's ``updated_at`` with the current time.

    The column's ``onupdate`` is not enough on its own: it only fires when
    the flush emits an ``UPDATE``, i.e. when another column changed. An
    ``Updated`` event without a reading, a stale sample or a non-energy
    measurement changes nothing else, yet still counts as activity on the
    session.

    Args:
        session_record: The ORM aggregate being processed in the transaction.

    Side Effects:
        Sets ``updated_at``; written at the caller's next flush/commit.
    """
    session_record.updated_at = utc_now()


async def _get_charging_session_record(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionModel:
    """Load a session aggregate that must exist.

    Private on purpose: it returns the ORM model, which must never cross
    the domain boundary.

    Args:
        db: The current async session.
        session_id: UUID of the session to load.

    Returns:
        The existing session aggregate.

    Raises:
        ChargingSessionNotFoundError: If the session is not found.
    """
    session_record = await charging_session_repository.get_session_by_id(db, session_id)
    if session_record is None:
        raise ChargingSessionNotFoundError(f"Session '{session_id}' not found")
    return session_record


async def _get_open_charging_session_record(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionModel:
    """Load a session aggregate that must exist and still accept data (F-B2).

    Args:
        db: The current async session.
        session_id: UUID of the session to load.

    Returns:
        The existing, not yet ``COMPLETED`` session aggregate.

    Raises:
        ChargingSessionNotFoundError: If the session is not found.
        ChargingSessionStateError: If the session is already ``COMPLETED``:
            data landing after ``Ended`` must not silently rewrite a
            finished session.
    """
    session_record = await _get_charging_session_record(db, session_id)
    if session_record.status is SessionStatus.COMPLETED:
        raise ChargingSessionStateError(f"Session '{session_id}' is already completed")
    return session_record


async def _get_open_session_by_transaction(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
) -> ChargingSessionModel:
    """Load the aggregate an ``Updated``/``Ended`` event belongs to.

    Args:
        db: The current async session.
        station_id: UUID of the station that raised the transaction.
        evse_id: UUID of the EVSE the event names.
        connector_id: UUID of the connector the event names.
        transaction_id: The validated OCPP transaction identity.

    Returns:
        The existing, not yet ``COMPLETED`` aggregate.

    Raises:
        ChargingSessionNotFoundError: If no ``Started`` created it yet.
        ChargingSessionInputError: If the event's EVSE/connector differ from
            the aggregate's.
        ChargingSessionStateError: If the session is already ``COMPLETED``.
    """
    session_record = await charging_session_repository.get_session_by_transaction(
        db, station_id, transaction_id
    )
    if session_record is None:
        raise ChargingSessionNotFoundError(
            f"Transaction '{transaction_id}' has no Started yet"
        )
    if session_record.evse_id != evse_id or session_record.connector_id != connector_id:
        raise ChargingSessionInputError("Transaction topology does not match")
    if session_record.status is SessionStatus.COMPLETED:
        # COMPLETED is terminal: re-stamping ended_at or re-applying a meter
        # reading would silently rewrite a finished session and corrupt
        # F-C5's energy totals. Distinguishing a harmless replay from a
        # genuinely different late event needs seq_no-keyed dedup
        # (deferred.md item 27), so every post-Ended event is refused the same
        # way, whether it's a duplicate Ended or a late Updated.
        raise ChargingSessionStateError(
            f"Transaction '{transaction_id}' is already completed"
        )
    return session_record


async def _build_page_response[RowT, ItemT: BaseModel, PageT: BaseModel](
    page_response_type: type[PageT],
    *,
    page: int,
    page_size: int,
    list_rows: Callable[[PageWindow], Awaitable[Sequence[RowT]]],
    count_rows: Callable[[], Awaitable[int]],
    to_item: Callable[[RowT], ItemT],
) -> PageT:
    """Load one page of rows plus the total and wrap them in a page response.

    Shared by every monitoring list: each page response has the same
    ``items``/``total``/``page``/``page_size`` shape.

    Args:
        page_response_type: The ``*ListResponse`` schema to build.
        page: The requested page, starting at one.
        page_size: The requested page size.
        list_rows: Loads the rows of a normalized page window.
        count_rows: Counts every row matching the same filter.
        to_item: Converts one row into its response item.

    Returns:
        The page response, with the normalized page and page size.

    Side Effects:
        Runs one items query and one count query; no ORM relationships are
        loaded, so there is no N+1 pattern. Does not commit or roll back.
    """
    page_window = normalize_page_window(page, page_size)
    rows = await list_rows(page_window)
    total = await count_rows()
    return page_response_type(
        items=[to_item(row) for row in rows],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


def calculate_session_duration_seconds(
    session_record: ChargingSessionModel, *, now: datetime
) -> int:
    """Compute how long a session lasted, or has lasted so far (F-B2).

    Args:
        session_record: The session aggregate.
        now: Reference time used while the session has no ``ended_at``.

    Returns:
        Whole seconds from ``started_at`` to ``ended_at`` (or ``now``),
        floored at zero so a charger clock ahead of the server never yields
        a negative duration.
    """
    ended_at = session_record.ended_at if session_record.ended_at is not None else now
    return max(int((ended_at - session_record.started_at).total_seconds()), 0)


def calculate_max_power_kw(
    max_values_by_unit: Sequence[tuple[str | None, Decimal]],
) -> float | None:
    """Pick the highest power across units, converted to kW (F-B2).

    Args:
        max_values_by_unit: ``(unit, max_value)`` pairs as stored; ``W`` (or
            no unit, OCPP's default) is divided by 1000, ``kW`` is kept, any
            other unit is ignored.

    Returns:
        The highest power in kW, or ``None`` if no pair is convertible.
    """
    converted_values_kw: list[Decimal] = []
    for unit, max_value in max_values_by_unit:
        factor = _POWER_UNIT_FACTORS_KW.get(unit.lower() if unit is not None else None)
        if factor is not None:
            converted_values_kw.append(max_value * factor)
    return float(max(converted_values_kw)) if converted_values_kw else None


async def _build_charging_session_detail_response(
    db: AsyncSession, session_record: ChargingSessionModel
) -> ChargingSessionDetailResponse:
    """Build the session detail, computing its summary from the measurements.

    Args:
        db: The async session owned by the HTTP boundary.
        session_record: The session aggregate already loaded.

    Returns:
        The session response plus ``duration_seconds``, the first/last SoC
        and the maximum import power in kW (``None`` where the session has
        no such sample).

    Side Effects:
        Runs three measurement queries; does not commit or roll back.
    """
    soc_start = await charging_session_repository.find_first_measurement_value(
        db, session_record.session_id, measurand=_SOC_MEASURAND
    )
    soc_end = await charging_session_repository.find_last_measurement_value(
        db, session_record.session_id, measurand=_SOC_MEASURAND
    )
    max_power_values = (
        await charging_session_repository.list_max_measurement_values_by_unit(
            db, session_record.session_id, measurand=_POWER_ACTIVE_IMPORT_MEASURAND
        )
    )
    session_response = ChargingSessionResponse.model_validate(session_record)
    return ChargingSessionDetailResponse(
        **session_response.model_dump(),
        duration_seconds=calculate_session_duration_seconds(
            session_record, now=utc_now()
        ),
        soc_start_percent=float(soc_start) if soc_start is not None else None,
        soc_end_percent=float(soc_end) if soc_end is not None else None,
        max_power_kw=calculate_max_power_kw(max_power_values),
    )


async def get_charging_session(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionDetailResponse:
    """Get the session aggregate and its read-time summary (F-B2).

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the aggregate to view.

    Returns:
        The session response plus ``duration_seconds``,
        ``soc_start_percent``, ``soc_end_percent`` and ``max_power_kw``, all
        computed now from the stored measurements (never stored).

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.

    Side Effects:
        Performs one aggregate query and three measurement queries; does not
        commit or roll back.
    """
    session_record = await _get_charging_session_record(db, session_id)
    return await _build_charging_session_detail_response(db, session_record)


async def list_charging_sessions(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    station_id: UUID | None = None,
    connector_id: UUID | None = None,
    status: SessionStatus | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
) -> ChargingSessionListResponse:
    """Get the most recent sessions matching optional filters (F-B2).

    Args:
        db: The async session owned by the HTTP boundary.
        page: The page, starting at one.
        page_size: The page size.
        station_id: Only sessions of this station, if given.
        connector_id: Only sessions on this connector, if given.
        status: Only sessions in this status, if given.
        started_from: Only sessions with ``started_at >= started_from``;
            must carry a timezone.
        started_to: Only sessions with ``started_at < started_to``; must
            carry a timezone and, with ``started_from``, be after it.

    Returns:
        The list of sessions and pagination metadata; the items keep the
        plain session schema (no read-time summary).

    Raises:
        ChargingSessionInputError: If a time bound lacks a timezone or
            ``started_to`` is not after ``started_from``.

    Side Effects:
        Performs one items query and one count query; does not commit or
        roll back.
    """
    normalized_from = (
        _normalize_utc(started_from, "started_from")
        if started_from is not None
        else None
    )
    normalized_to = (
        _normalize_utc(started_to, "started_to") if started_to is not None else None
    )
    if (
        normalized_from is not None
        and normalized_to is not None
        and normalized_to <= normalized_from
    ):
        raise ChargingSessionInputError("started_to must be after started_from")
    filters = ChargingSessionListFilter(
        station_id=station_id,
        connector_id=connector_id,
        status=status,
        started_from=normalized_from,
        started_to=normalized_to,
    )
    return await _build_page_response(
        ChargingSessionListResponse,
        page=page,
        page_size=page_size,
        list_rows=lambda page_window: charging_session_repository.list_sessions(
            db,
            filters=filters,
            offset=page_window.offset,
            limit=page_window.page_size,
        ),
        count_rows=lambda: charging_session_repository.count_sessions(db, filters),
        to_item=ChargingSessionResponse.model_validate,
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
        Performs one session lookup and two event queries (items/count).
    """
    await _get_charging_session_record(db, session_id)
    return await _build_page_response(
        ChargingSessionEventListResponse,
        page=page,
        page_size=page_size,
        list_rows=lambda page_window: charging_session_repository.list_events(
            db, session_id, offset=page_window.offset, limit=page_window.page_size
        ),
        count_rows=lambda: charging_session_repository.count_events(db, session_id),
        to_item=to_charging_session_event_response,
    )


async def list_charging_session_meter_values(
    db: AsyncSession,
    session_id: UUID,
    *,
    page: int,
    page_size: int,
) -> ChargingSessionMeterValueListResponse:
    """Get paginated energy-register samples for the ``/meter-values`` endpoint.

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
        Performs one session lookup and two measurement queries
        (items/count).
    """
    await _get_charging_session_record(db, session_id)
    return await _build_page_response(
        ChargingSessionMeterValueListResponse,
        page=page,
        page_size=page_size,
        list_rows=lambda page_window: (
            charging_session_repository.list_energy_measurements(
                db, session_id, offset=page_window.offset, limit=page_window.page_size
            )
        ),
        count_rows=lambda: charging_session_repository.count_energy_measurements(
            db, session_id
        ),
        to_item=to_charging_session_meter_value_response,
    )


async def list_charging_session_measurements(
    db: AsyncSession,
    session_id: UUID,
    *,
    measurand: str | None,
    page: int,
    page_size: int,
) -> ChargingSessionMeasurementListResponse:
    """Get paginated measurements (any measurand) for the monitoring endpoint.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session whose measurements to view.
        measurand: Return only this measurand, or all of them if ``None``.
        page: The page, starting at one.
        page_size: The page size.

    Returns:
        The measurements and pagination metadata.

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.

    Side Effects:
        Performs one session lookup and two measurement queries (items and
        count).
    """
    await _get_charging_session_record(db, session_id)
    return await _build_page_response(
        ChargingSessionMeasurementListResponse,
        page=page,
        page_size=page_size,
        list_rows=lambda page_window: charging_session_repository.list_measurements(
            db,
            session_id,
            measurand=measurand,
            offset=page_window.offset,
            limit=page_window.page_size,
        ),
        count_rows=lambda: charging_session_repository.count_measurements(
            db, session_id, measurand=measurand
        ),
        to_item=ChargingSessionMeasurementResponse.model_validate,
    )


def to_charging_session_event_response(
    event_record: ChargingSessionEventModel,
) -> ChargingSessionEventResponse:
    """Convert an ORM event into the monitoring response schema.

    Args:
        event_record: The ORM event already queried by the repository.

    Returns:
        An event response containing no raw payload.
    """
    return ChargingSessionEventResponse.model_validate(event_record)


def to_charging_session_meter_value_response(
    measurement_record: ChargingSessionMeasurementModel,
) -> ChargingSessionMeterValueResponse:
    """Convert an energy measurement into the ``/meter-values`` response schema.

    The response contract predates the unified measurements table and is kept
    unchanged: ``value_wh`` is the measurement's canonical Wh ``value``, shown
    with three decimals like the ``Numeric(24, 3)`` column it used to come
    from (the new column keeps six for non-energy measurands).

    Args:
        measurement_record: An energy-register measurement queried by the
            repository.

    Returns:
        A canonical Wh meter response.
    """
    return ChargingSessionMeterValueResponse(
        meter_value_id=measurement_record.measurement_id,
        sampled_at=measurement_record.sampled_at,
        session_id=measurement_record.session_id,
        value_wh=measurement_record.value.quantize(Decimal("0.001")),
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
        1. Every input field is validated and normalized before anything is
           read or written (``_validate_transaction_event``).
        2. ``Started`` creates a new aggregate. ``Updated``/``Ended`` load
           the existing one, which must exist, match the event's topology
           and not be ``COMPLETED`` (F-B2) - a duplicate ``Ended`` or a late
           ``Updated`` must not silently re-mutate a finished record
           (``_get_open_session_by_transaction``).
        3. The event is appended to the history, always before the
           aggregate is updated.
        4. The meter reading is applied unless it is stale (older than one
           already applied) - see ``_apply_charging_session_meter_end``.
        5. ``Ended`` completes the aggregate: ``ended_at``, status,
           ``stop_reason`` and ``meter_stop_wh``.
        6. ``updated_at`` is stamped (``_touch_session``).

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
        A result containing the session UUID and its status after the
        event.

    Raises:
        ChargingSessionInputError: If the input violates the contract (an
            ``id_tag`` longer than 20 or a ``stop_reason`` longer than 30
            characters, a negative energy) or the topology does not match.
        ChargingSessionNotFoundError: If the event is not ``Started`` but
            the aggregate does not yet exist.
        ChargingSessionStateError: If the session is already ``COMPLETED``.

    Side Effects:
        Creates or updates the aggregate and appends an event in the
        current transaction; does not commit or roll back on its own. A
        duplicate ``Started`` has no dedicated branch yet: the unique
        ``(station_id, ocpp_transaction_id)`` constraint refuses it at
        flush time.
    """
    validated_event = _validate_transaction_event(
        transaction_id=transaction_id,
        event_occurred_at=event_occurred_at,
        seq_no=seq_no,
        meter_start_wh=meter_start_wh,
        meter_end_wh=meter_end_wh,
        meter_end_sampled_at=meter_end_sampled_at,
        id_tag=id_tag,
        stop_reason=stop_reason,
        meter_stop_wh=meter_stop_wh,
    )

    session_record: ChargingSessionModel
    if event_type == SessionEventType.STARTED:
        session_record = await charging_session_repository.create_session(
            db,
            station_id=station_id,
            evse_id=evse_id,
            connector_id=connector_id,
            transaction_id=validated_event.transaction_id,
            started_at=validated_event.occurred_at,
            meter_start_wh=validated_event.meter_start_wh,
            id_tag=validated_event.id_tag,
        )
    else:
        session_record = await _get_open_session_by_transaction(
            db,
            station_id=station_id,
            evse_id=evse_id,
            connector_id=connector_id,
            transaction_id=validated_event.transaction_id,
        )

    await charging_session_repository.insert_event(
        db,
        session_id=session_record.session_id,
        event_occurred_at=validated_event.occurred_at,
        event_type=event_type,
        seq_no=validated_event.seq_no,
    )
    _apply_charging_session_meter_end(
        session_record,
        validated_event.meter_end_wh,
        validated_event.meter_end_sampled_at,
    )
    if event_type is SessionEventType.ENDED:
        session_record.ended_at = validated_event.occurred_at
        session_record.status = SessionStatus.COMPLETED
        session_record.stop_reason = validated_event.stop_reason
        session_record.meter_stop_wh = validated_event.meter_stop_wh
    _touch_session(session_record)
    return TransactionIngestResult(
        session_id=session_record.session_id,
        status=session_record.status,
    )


async def ingest_meter_values(
    db: AsyncSession,
    *,
    session_id: UUID,
    sample: MeterSampleInput,
) -> MeterIngestResult:
    """Store one energy-register sample and update the aggregate.

    Rule:
        1. The session must exist and not be ``COMPLETED`` (F-B2), for the
           same reason as ``ingest_transaction_event``'s guard: a sample
           landing after ``Ended`` must not silently rewrite a finished
           session's energy total.
        2. The sample's time and Wh value are validated.
        3. The sample is always appended to the history (append-only).
        4. The aggregate's ``meter_end_wh`` only advances if the sample is
           not stale - see ``_apply_charging_session_meter_end`` (F-B2).
        5. ``updated_at`` is stamped (``_touch_session``).

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the aggregate to update.
        sample: The sample, already canonicalized to Wh by the adapter.

    Returns:
        A result containing the session UUID and its status.

    Raises:
        ChargingSessionInputError: If the sample lacks a timezone or the
            energy value is invalid.
        ChargingSessionNotFoundError: If the aggregate does not exist.
        ChargingSessionStateError: If the session is already ``COMPLETED``.

    Side Effects:
        Appends a measurement row and updates the aggregate in the same
        transaction; the caller must commit or roll back the transaction at
        the entry boundary.
    """
    session_record = await _get_open_charging_session_record(db, session_id)
    sampled_at = _normalize_utc(sample.sampled_at, "sampled_at")
    value_wh = _validate_energy(sample.value_wh, "value_wh")
    if value_wh is None:
        raise ChargingSessionInputError("value_wh is required")
    await charging_session_repository.insert_measurement(
        db,
        session_id=session_record.session_id,
        sampled_at=sampled_at,
        measurand=ENERGY_ACTIVE_IMPORT_REGISTER,
        value=value_wh,
        unit=ENERGY_UNIT_WH,
        context=sample.context,
    )
    _apply_charging_session_meter_end(session_record, value_wh, sampled_at)
    _touch_session(session_record)
    return MeterIngestResult(
        session_id=session_record.session_id,
        status=session_record.status,
    )


async def ingest_measurements(
    db: AsyncSession,
    *,
    session_id: UUID,
    samples: Sequence[MeasurementInput],
) -> int:
    """Store non-energy measurements of an active session (SoC, power, voltage…).

    Rule:
        1. The session must exist and not be ``COMPLETED``, for the same
           reason as in ``ingest_meter_values``.
        2. Every sample is validated, in payload order, before the first
           insert (``_validate_measurement``). Vendor-specific measurand
           names are accepted as sent; values may be negative (a
           temperature, an exported power).
        3. Each sample is inserted individually (append-only history, no
           batching). The aggregate's energy fields are **not** affected:
           only the energy register drives ``meter_end_wh``/
           ``energy_delivered_wh`` (see ``ingest_meter_values``).
        4. ``updated_at`` is stamped (``_touch_session``).

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the session that owns the samples.
        samples: The measurements to store, in payload order.

    Returns:
        The number of measurements stored.

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.
        ChargingSessionStateError: If the session is already ``COMPLETED``.
        ChargingSessionInputError: If a sample lacks a timezone, has a
            non-finite value, an empty measurand, or a field longer than its
            column (measurand 60, unit 20, context 30, phase 10, location 20).

    Side Effects:
        Appends rows and updates the session's ``updated_at`` in the caller's
        transaction; does not commit or roll back.
    """
    session_record = await _get_open_charging_session_record(db, session_id)
    validated_samples = [_validate_measurement(sample) for sample in samples]
    for validated_sample in validated_samples:
        await charging_session_repository.insert_measurement(
            db,
            session_id=session_record.session_id,
            sampled_at=validated_sample.sampled_at,
            measurand=validated_sample.measurand,
            value=validated_sample.value,
            unit=validated_sample.unit,
            context=validated_sample.context,
            phase=validated_sample.phase,
            location=validated_sample.location,
        )
    _touch_session(session_record)
    return len(validated_samples)


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
    energy_total = await resolve_station_energy_total(
        db, station_id=station_id, start_time=start_time, end_time=end_time
    )
    return StationEnergySummaryResponse(
        station_id=station_id,
        start_time=_normalize_utc(start_time, "start_time"),
        end_time=_normalize_utc(end_time, "end_time"),
        total_energy_kwh=float(energy_total.total_energy_wh / _WH_PER_KWH),
        session_count=energy_total.session_count,
    )


async def resolve_station_energy_total(
    db: AsyncSession,
    *,
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> StationEnergyTotal:
    """Total energy sold at a station within a window, as a DTO (F-C5).

    Public entry point for ``charging_stations``' all-stations report, and
    the core of ``get_station_energy_summary``: completed sessions of the
    station whose ``ended_at`` falls within the inclusive window, summing
    ``energy_delivered_wh``.

    Args:
        db: The async session owned by the caller's entry boundary.
        station_id: UUID of the station to aggregate over.
        start_time: Inclusive lower bound on ``ended_at``; must carry a
            timezone.
        end_time: Inclusive upper bound on ``ended_at``; must carry a
            timezone and be after ``start_time``.

    Returns:
        The total in Wh and the session count; zero for a station with no
        matching session (existence is not checked here).

    Raises:
        ChargingSessionInputError: If either timestamp lacks a timezone, or
            ``end_time`` isn't after ``start_time``.

    Side Effects:
        Performs one aggregate query; does not commit or roll back.
    """
    normalized_start = _normalize_utc(start_time, "start_time")
    normalized_end = _normalize_utc(end_time, "end_time")
    if normalized_end <= normalized_start:
        raise ChargingSessionInputError("end_time must be after start_time")
    (
        total_energy_wh,
        session_count,
    ) = await charging_session_repository.get_station_energy_summary(
        db,
        station_id=station_id,
        start_time=normalized_start,
        end_time=normalized_end,
    )
    return StationEnergyTotal(
        station_id=station_id,
        total_energy_wh=total_energy_wh,
        session_count=session_count,
    )


def _floor_to_bucket_start(
    instant: datetime,
    *,
    granularity: EnergySeriesGranularity,
    report_zone: ZoneInfo,
) -> datetime:
    """Return the UTC start of the report-time-zone bucket containing an instant.

    Args:
        instant: A timezone-aware instant.
        granularity: Hour or day buckets.
        report_zone: The time zone that defines an hour/day (D4).

    Returns:
        The bucket's start, as a UTC instant.
    """
    local_instant = instant.astimezone(report_zone)
    if granularity is EnergySeriesGranularity.HOUR:
        local_start = local_instant.replace(minute=0, second=0, microsecond=0)
    else:
        local_start = local_instant.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc)


def _next_bucket_start(
    bucket_start: datetime,
    *,
    granularity: EnergySeriesGranularity,
    report_zone: ZoneInfo,
) -> datetime:
    """Return the UTC start of the bucket following ``bucket_start``.

    Hours advance by one absolute hour; days advance to the next local
    midnight, so a daylight-saving day of 23 or 25 hours stays one bucket.

    Args:
        bucket_start: A bucket start produced by ``_floor_to_bucket_start``.
        granularity: Hour or day buckets.
        report_zone: The time zone that defines a day.

    Returns:
        The next bucket's start, as a UTC instant.
    """
    if granularity is EnergySeriesGranularity.HOUR:
        return bucket_start + timedelta(hours=1)
    next_local_date = bucket_start.astimezone(report_zone).date() + timedelta(days=1)
    return datetime(
        next_local_date.year,
        next_local_date.month,
        next_local_date.day,
        tzinfo=report_zone,
    ).astimezone(timezone.utc)


def build_energy_series_bucket_starts(
    *,
    start_time: datetime,
    end_time: datetime,
    granularity: EnergySeriesGranularity,
    report_zone: ZoneInfo,
) -> list[datetime]:
    """List every bucket start of a window, for a dense series (F-C5, D4).

    Args:
        start_time: Inclusive window start (UTC).
        end_time: Exclusive window end (UTC), after ``start_time``.
        granularity: Hour or day buckets.
        report_zone: The time zone buckets are cut in.

    Returns:
        UTC bucket starts in time order, from the bucket containing
        ``start_time`` to the last one starting before ``end_time``.
    """
    bucket_starts: list[datetime] = []
    bucket_start = _floor_to_bucket_start(
        start_time, granularity=granularity, report_zone=report_zone
    )
    while bucket_start < end_time:
        bucket_starts.append(bucket_start)
        bucket_start = _next_bucket_start(
            bucket_start, granularity=granularity, report_zone=report_zone
        )
    return bucket_starts


def calculate_energy_deltas(
    readings: Sequence[tuple[datetime, Decimal]],
) -> list[tuple[datetime, Decimal]]:
    """Turn one session's energy-register readings into timed deltas (D5).

    Rule:
        Readings are sorted by time (stable, so readings sharing a time keep
        their given order); each consecutive pair yields ``(later reading's
        time, later value - earlier value)``. A negative delta (a register
        that went backwards, e.g. a meter reset) yields nothing:
        reconciling it is the deferred reliability path (``deferred.md`` item
        27), and a negative bucket would be meaningless.

    Args:
        readings: ``(time, register_wh)`` pairs of one session, in any order.

    Returns:
        ``(time, delta_wh)`` pairs, one per non-negative consecutive delta.
    """
    ordered_readings = sorted(readings, key=lambda reading: reading[0])
    deltas: list[tuple[datetime, Decimal]] = []
    for (_, previous_wh), (current_at, current_wh) in zip(
        ordered_readings, ordered_readings[1:], strict=False
    ):
        delta_wh = current_wh - previous_wh
        if delta_wh >= 0:
            deltas.append((current_at, delta_wh))
    return deltas


def _session_energy_readings(
    session_record: ChargingSessionModel,
    samples: Sequence[tuple[datetime, Decimal]],
) -> list[tuple[datetime, Decimal]]:
    """Assemble every energy-register reading known for one session.

    The stored samples alone miss energy: ``meter_start_wh`` (taken at
    ``started_at``) is never stored as a sample, and a 2.0.1 ``Ended``
    reading only moves the aggregate's ``meter_end_wh``. Adding both as
    readings makes a finished session's deltas sum to its
    ``energy_delivered_wh``; when the latest reading is also a stored sample
    it only adds a zero delta.

    Args:
        session_record: The session aggregate.
        samples: The session's stored energy samples, ``(sampled_at,
            value_wh)``.

    Returns:
        The start reading (if any), the samples, then the latest reading (if
        any), in that order before sorting by time.
    """
    readings: list[tuple[datetime, Decimal]] = []
    if session_record.meter_start_wh is not None:
        readings.append((session_record.started_at, session_record.meter_start_wh))
    readings.extend(samples)
    if (
        session_record.meter_end_wh is not None
        and session_record.meter_end_sampled_at is not None
    ):
        readings.append(
            (session_record.meter_end_sampled_at, session_record.meter_end_wh)
        )
    return readings


async def get_station_energy_series(
    db: AsyncSession,
    *,
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
    granularity: EnergySeriesGranularity,
) -> StationEnergySeriesResponse:
    """Energy metered at a station per hour or day within a window (F-C5).

    Rule (decisions D4 and D5 of the happy-path completion planner):
        1. The window is ``[start_time, end_time)``, timezone-aware, at most
           ``CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS`` long.
        2. Buckets are clock hours or calendar days of
           ``APP_REPORT_TIMEZONE``, returned as UTC instants; every bucket
           from the one containing ``start_time`` is listed (dense series).
        3. For each session of the station that overlaps the window (active
           sessions included), consecutive energy-register readings yield
           deltas (``calculate_energy_deltas``); a delta belongs to the
           bucket of its later reading and counts only when that reading
           falls inside the window - so energy crossing a bucket boundary is
           split at the readings, not attributed whole at ``ended_at``.

    Args:
        db: The async session owned by the HTTP boundary.
        station_id: UUID of the station. An unknown station yields an
            all-zero series, like ``get_station_energy_summary``.
        start_time: Inclusive window start; must carry a timezone.
        end_time: Exclusive window end; must carry a timezone.
        granularity: ``hour`` or ``day``.

    Returns:
        The dense series and its total, in kWh.

    Raises:
        ChargingSessionInputError: If a bound lacks a timezone, ``end_time``
            is not after ``start_time``, or the window is longer than
            ``CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS``.

    Side Effects:
        Performs one session query plus one sample query per overlapping
        session (deliberately unbatched); does not commit or roll back.
    """
    normalized_start = _normalize_utc(start_time, "start_time")
    normalized_end = _normalize_utc(end_time, "end_time")
    if normalized_end <= normalized_start:
        raise ChargingSessionInputError("end_time must be after start_time")
    max_range = timedelta(days=settings.CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS)
    if normalized_end - normalized_start > max_range:
        raise ChargingSessionInputError(
            "The time window must not exceed "
            f"{settings.CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS} days"
        )
    report_zone = ZoneInfo(settings.APP_REPORT_TIMEZONE)
    bucket_starts = build_energy_series_bucket_starts(
        start_time=normalized_start,
        end_time=normalized_end,
        granularity=granularity,
        report_zone=report_zone,
    )
    bucket_energy_wh = [Decimal(0)] * len(bucket_starts)

    sessions = await charging_session_repository.list_sessions_by_station_in_window(
        db, station_id, start_time=normalized_start, end_time=normalized_end
    )
    for session_record in sessions:
        samples = await charging_session_repository.list_energy_samples_before(
            db, session_record.session_id, end_time=normalized_end
        )
        readings = _session_energy_readings(session_record, samples)
        for delta_at, delta_wh in calculate_energy_deltas(readings):
            if not normalized_start <= delta_at < normalized_end:
                continue
            # bucket_starts[0] <= normalized_start <= delta_at, so the index
            # is never negative.
            bucket_index = bisect.bisect_right(bucket_starts, delta_at) - 1
            bucket_energy_wh[bucket_index] += delta_wh

    return StationEnergySeriesResponse(
        station_id=station_id,
        start_time=normalized_start,
        end_time=normalized_end,
        granularity=granularity,
        report_timezone=settings.APP_REPORT_TIMEZONE,
        total_energy_kwh=float(sum(bucket_energy_wh, Decimal(0)) / _WH_PER_KWH),
        items=[
            StationEnergySeriesBucketResponse(
                bucket_start=bucket_start,
                energy_kwh=float(energy_wh / _WH_PER_KWH),
            )
            for bucket_start, energy_wh in zip(
                bucket_starts, bucket_energy_wh, strict=True
            )
        ],
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
    return await charging_session_repository.next_ocpp16_transaction_id(db)


async def has_active_session_on_connector(db: AsyncSession, connector_id: UUID) -> bool:
    """Tell whether a connector already has an open (``active``) session.

    Args:
        db: The async session owned by the entry boundary.
        connector_id: UUID of the connector.

    Returns:
        ``True`` if at least one session on the connector is still active.
    """
    active_session_count = (
        await charging_session_repository.count_active_sessions_by_connector_id(
            db, connector_id
        )
    )
    return active_session_count > 0


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
    session_record = await charging_session_repository.get_session_by_transaction(
        db, station_id, transaction_id
    )
    if session_record is None:
        raise ChargingSessionNotFoundError(
            f"Transaction '{transaction_id}' not found for this station"
        )
    return TransactionSessionReference(
        session_id=session_record.session_id,
        station_id=session_record.station_id,
        evse_id=session_record.evse_id,
        connector_id=session_record.connector_id,
        status=session_record.status,
    )
