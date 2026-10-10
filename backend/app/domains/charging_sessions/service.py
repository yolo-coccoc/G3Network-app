"""Public service of the charging sessions: scan, start, stop and measurements.

A session is created ``PENDING`` at the QR scan (``create_pending_session``,
CE-10) and turned ``ACTIVE`` by the charger's start message when its token
matches a PENDING session of the same charger (``activate_pending_session``,
CE-11); the stop message completes it (``complete_session``). The ideal MVP
assumes a fixed message order and removes reliability branching (retry, DLQ,
out-of-order recovery, dedup - see ``docs/decisions/deferred.md`` item 27).
Two correctness invariants hold even on the happy path (F-B2): a session's
status can only move forward (data arriving after ``COMPLETED`` is refused,
not silently applied), and the session keeps only the readings the charger
declares (``meter_start_wh``, ``meter_stop_wh``, CE-12). The caller at the
entry boundary owns commit/rollback of the transaction.

Access (ACC-15): a session belongs to the organization that paid for it
(DM-24 C) and was started by the user who scanned. An HTTP caller sees the
sessions of their organization (internal staff all); a caller who is only a
DRIVER sees just the sessions they started. The owner of the charger does not
see other organizations' sessions at their chargers through this module
(`charging_sessions` cannot ask `charging_stations` who owns a charger).

The module also serves the read-only monitoring endpoints (session detail with
its read-time summary, the filtered session list, energy samples,
measurements, the station energy summary and the station energy time series).
The ingestion functions, ``allocate_ocpp16_transaction_id``,
``has_active_session_on_connector`` and ``resolve_session_by_transaction`` are
the public entry points the ``charging_stations`` OCPP adapters call;
``resolve_station_energy_total`` is the one its all-stations energy report
calls.
"""

import bisect
import dataclasses
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_session_repository
import app.domains.identity.service as identity_service
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionNotFoundError,
    ChargingSessionStateError,
    ChargingSessionTokenError,
)
from app.domains.charging_sessions.models import (
    ChargingSessionMeasurementModel,
    ChargingSessionModel,
)
from app.domains.charging_sessions.schemas import (
    ChargingSessionDetailResponse,
    ChargingSessionListResponse,
    ChargingSessionMeasurementListResponse,
    ChargingSessionMeasurementResponse,
    ChargingSessionMeterValueListResponse,
    ChargingSessionMeterValueResponse,
    ChargingSessionResponse,
    ChargingSessionScanRequest,
    ChargingSessionScanResponse,
    StationEnergySeriesBucketResponse,
    StationEnergySeriesResponse,
    StationEnergySummaryResponse,
)
from app.domains.charging_sessions.types import (
    ENERGY_ACTIVE_IMPORT_REGISTER,
    ENERGY_UNIT_WH,
    ID_TOKEN_MAX_LENGTH,
    MEASUREMENT_LOCATION_OUTLET,
    OCPP_TRANSACTION_ID_MAX_LENGTH,
    QR_TOKEN_LENGTH,
    STOP_REASON_MAX_LENGTH,
    ChargingSessionListFilter,
    EnergySeriesGranularity,
    MeasurementInput,
    MeterIngestResult,
    MeterSampleInput,
    PendingSessionReference,
    SessionCommandReference,
    SessionStatus,
    StationEnergyTotal,
    TransactionIngestResult,
    TransactionSessionReference,
)
from app.domains.identity.types import Principal, UserRole, roles_for
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import PageWindow, normalize_page_window

logger = logging.getLogger(__name__)

# Roles that read every session of their organization (CHG-02/04/05); a
# caller holding only DRIVER sees the sessions they started themselves.
SESSION_STAFF_ROLES = roles_for("CHG-02", "CHG-04", "CHG-05") - {UserRole.DRIVER}

# Measurands read by the session summary (F-B2). Names as OCPP defines them.
# Values are stored in one fixed unit per measurand (CE-14): power in W, SoC in
# percent, so the summary converts nothing but W to kW for display.
_SOC_MEASURAND: Final[str] = "SoC"
_POWER_ACTIVE_IMPORT_MEASURAND: Final[str] = "Power.Active.Import"
_W_PER_KW: Final[Decimal] = Decimal(1000)
_WH_PER_KWH: Final[Decimal] = Decimal(1000)

# Input length limits, mirroring the widths of the columns the values are
# stored in (``charging_session_measurements``): validating here turns an
# over-long value into a ChargingSessionInputError instead of a database error
# at flush time. The session columns' widths live in ``types.py``.
_MEASURAND_MAX_LENGTH: Final[int] = 60
_MEASUREMENT_UNIT_MAX_LENGTH: Final[int] = 20
_MEASUREMENT_CONTEXT_MAX_LENGTH: Final[int] = 30
_MEASUREMENT_PHASE_MAX_LENGTH: Final[int] = 10
_MEASUREMENT_LOCATION_MAX_LENGTH: Final[int] = 20


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


def _validate_measurement(sample: MeasurementInput) -> MeasurementInput:
    """Validate one measurement and normalize its time to UTC.

    Pure: performs no I/O. Values may be negative (a temperature, an
    exported power) and vendor-specific measurand names are accepted. The
    gateway has already converted known measurands to their fixed unit.

    Args:
        sample: The measurement as normalized by the OCPP adapter.

    Returns:
        A copy of the sample with ``sampled_at`` in UTC.

    Raises:
        ChargingSessionInputError: On an empty or over-long measurand, an empty
            context or location, a field longer than its column, a
            non-finite value or a naive timestamp.
    """
    if not sample.measurand or len(sample.measurand) > _MEASURAND_MAX_LENGTH:
        raise ChargingSessionInputError(
            f"measurand must be 1-{_MEASURAND_MAX_LENGTH} characters"
        )
    for field_name, field_value, max_length in (
        ("unit", sample.unit, _MEASUREMENT_UNIT_MAX_LENGTH),
        ("context", sample.context, _MEASUREMENT_CONTEXT_MAX_LENGTH),
        ("phase", sample.phase, _MEASUREMENT_PHASE_MAX_LENGTH),
        (
            "measurement_location",
            sample.measurement_location,
            _MEASUREMENT_LOCATION_MAX_LENGTH,
        ),
    ):
        if field_value is not None and len(field_value) > max_length:
            raise ChargingSessionInputError(
                f"{field_name} exceeds {max_length} characters"
            )
    if not sample.context or not sample.measurement_location:
        raise ChargingSessionInputError(
            "context and measurement_location are required (CE-14)"
        )
    if not isinstance(sample.value, Decimal) or not sample.value.is_finite():
        raise ChargingSessionInputError("value must be a finite Decimal")
    return dataclasses.replace(
        sample, sampled_at=_normalize_utc(sample.sampled_at, "sampled_at")
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


def _session_started_by_scope(principal: Principal) -> UUID | None:
    """Tell whose sessions a caller may read inside their organization.

    Args:
        principal: The HTTP caller.

    Returns:
        `None` for staff roles (every session of the organization); the
        caller's own user ID for a DRIVER-only caller.
    """
    if principal.has_any_role(*SESSION_STAFF_ROLES):
        return None
    return principal.user_id


async def _get_charging_session_record(
    db: AsyncSession, session_id: UUID, principal: Principal | None = None
) -> ChargingSessionModel:
    """Load a session aggregate that must exist.

    Private on purpose: it returns the ORM model, which must never cross
    the domain boundary.

    Args:
        db: The current async session.
        session_id: UUID of the session to load.
        principal: The HTTP caller whose data reach limits the lookup; `None`
            for a system caller (OCPP adapters).

    Returns:
        The existing session aggregate.

    Raises:
        ChargingSessionNotFoundError: If the session is not found.
    """
    if principal is None:
        session_record = await charging_session_repository.get_session_by_id(
            db, session_id
        )
    else:
        session_record = await charging_session_repository.get_session_by_id(
            db,
            session_id,
            organization_id=principal.data_scope,
            started_by=_session_started_by_scope(principal),
        )
    if session_record is None:
        raise ChargingSessionNotFoundError(f"Session '{session_id}' not found")
    return session_record


async def _get_open_charging_session_record(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionModel:
    """Load a session that must exist and still accept readings (F-B2).

    Args:
        db: The current async session.
        session_id: UUID of the session to load.

    Returns:
        The existing ``ACTIVE`` session.

    Raises:
        ChargingSessionNotFoundError: If the session is not found.
        ChargingSessionStateError: If the session is not ``ACTIVE``: data
            landing after the stop must not silently rewrite a finished
            session, and a session that never started has no readings.
    """
    session_record = await _get_charging_session_record(db, session_id)
    if session_record.status is not SessionStatus.ACTIVE:
        raise ChargingSessionStateError(
            f"Session '{session_id}' is {session_record.status.value}, not active"
        )
    return session_record


async def _get_open_session_by_transaction(
    db: AsyncSession,
    *,
    station_id: UUID,
    transaction_id: str,
    evse_id: UUID | None,
    connector_id: UUID | None,
) -> ChargingSessionModel:
    """Load the session a stop message belongs to.

    Args:
        db: The current async session.
        station_id: UUID of the station that raised the transaction.
        transaction_id: The validated OCPP transaction identity.
        evse_id: UUID of the EVSE the message names, if it names one.
        connector_id: UUID of the connector the message names, if it names one.

    Returns:
        The existing ``ACTIVE`` session.

    Raises:
        ChargingSessionNotFoundError: If no start created it.
        ChargingSessionInputError: If the message's EVSE/connector differ from
            the session's.
        ChargingSessionStateError: If the session is not ``ACTIVE`` (already
            ``COMPLETED``, or never started).
    """
    session_record = await charging_session_repository.get_session_by_transaction(
        db, station_id, transaction_id
    )
    if session_record is None:
        raise ChargingSessionNotFoundError(
            f"Transaction '{transaction_id}' has no start yet"
        )
    if (evse_id is not None and session_record.evse_id != evse_id) or (
        connector_id is not None and session_record.connector_id != connector_id
    ):
        raise ChargingSessionInputError("Transaction topology does not match")
    if session_record.status is not SessionStatus.ACTIVE:
        # COMPLETED is terminal: re-stamping ended_at or re-applying a reading
        # would silently rewrite a finished session and corrupt F-C5's energy
        # totals. Distinguishing a harmless replay from a genuinely different
        # late message needs sequence-number dedup (deferred.md item 27), so
        # every post-stop message is refused the same way.
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
        session_record: The session.
        now: Reference time used while the session has no ``ended_at``.

    Returns:
        Whole seconds from ``started_at`` to ``ended_at`` (or ``now``),
        floored at zero so a charger clock ahead of the server never yields
        a negative duration; ``0`` for a session that never started.
    """
    if session_record.started_at is None:
        return 0
    ended_at = session_record.ended_at if session_record.ended_at is not None else now
    return max(int((ended_at - session_record.started_at).total_seconds()), 0)


async def _resolve_energy_delivered_wh(
    db: AsyncSession, session_record: ChargingSessionModel
) -> Decimal | None:
    """Compute the energy a session delivered, at read time (CE-12).

    Args:
        db: The current async session.
        session_record: The session.

    Returns:
        The charger's stop reading minus its start reading; while the session
        runs (or when the stop message carried no reading) the newest outlet
        energy measurement stands in for the stop reading. ``None`` without a
        start reading or any closing figure.

    Side Effects:
        Runs one measurement query when the stop reading is missing.
    """
    if session_record.meter_start_wh is None:
        return None
    closing_wh = session_record.meter_stop_wh
    if closing_wh is None:
        closing_wh = await charging_session_repository.find_last_measurement_value(
            db,
            session_record.session_id,
            measurand=ENERGY_ACTIVE_IMPORT_REGISTER,
            measurement_location=MEASUREMENT_LOCATION_OUTLET,
        )
    if closing_wh is None:
        return None
    return closing_wh - session_record.meter_start_wh


async def _build_charging_session_detail_response(
    db: AsyncSession, session_record: ChargingSessionModel
) -> ChargingSessionDetailResponse:
    """Build the session detail, computing its summary from the measurements.

    Args:
        db: The async session owned by the HTTP boundary.
        session_record: The session already loaded.

    Returns:
        The session response plus the delivered energy, ``duration_seconds``,
        the first/last SoC and the maximum outlet import power in kW
        (``None`` where the session has no such sample).

    Side Effects:
        Runs up to four measurement queries; does not commit or roll back.
    """
    soc_start = await charging_session_repository.find_first_measurement_value(
        db, session_record.session_id, measurand=_SOC_MEASURAND
    )
    soc_end = await charging_session_repository.find_last_measurement_value(
        db, session_record.session_id, measurand=_SOC_MEASURAND
    )
    max_power_w = await charging_session_repository.find_max_measurement_value(
        db,
        session_record.session_id,
        measurand=_POWER_ACTIVE_IMPORT_MEASURAND,
        measurement_location=MEASUREMENT_LOCATION_OUTLET,
    )
    session_response = ChargingSessionResponse.model_validate(session_record)
    return ChargingSessionDetailResponse(
        **session_response.model_dump(),
        energy_delivered_wh=await _resolve_energy_delivered_wh(db, session_record),
        duration_seconds=calculate_session_duration_seconds(
            session_record, now=utc_now()
        ),
        soc_start_percent=float(soc_start) if soc_start is not None else None,
        soc_end_percent=float(soc_end) if soc_end is not None else None,
        max_power_kw=(
            float(max_power_w / _W_PER_KW) if max_power_w is not None else None
        ),
    )


async def get_charging_session(
    db: AsyncSession, session_id: UUID, *, principal: Principal
) -> ChargingSessionDetailResponse:
    """Get the session aggregate and its read-time summary (F-B2).

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the aggregate to view.
        principal: The caller; a session out of their reach is not found.

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
    session_record = await _get_charging_session_record(db, session_id, principal)
    return await _build_charging_session_detail_response(db, session_record)


async def list_charging_sessions(
    db: AsyncSession,
    *,
    principal: Principal,
    page: int,
    page_size: int,
    station_id: UUID | None = None,
    connector_id: UUID | None = None,
    organization_id: UUID | None = None,
    status: SessionStatus | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
) -> ChargingSessionListResponse:
    """Get the most recent sessions matching optional filters (F-B2).

    Args:
        db: The async session owned by the HTTP boundary.
        principal: The caller; only sessions of their organization are listed
            (internal staff: all), and a DRIVER-only caller sees just the
            sessions they started.
        page: The page, starting at one.
        page_size: The page size.
        station_id: Only sessions of this station, if given.
        connector_id: Only sessions on this connector, if given.
        organization_id: Only sessions paid by this organization, if given; a
            caller who cannot reach that organization gets an empty page.
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
    scope = principal.data_scope
    if scope is not None and organization_id not in (None, scope):
        # Another organization's sessions do not exist for this caller.
        page_window = normalize_page_window(page, page_size)
        return ChargingSessionListResponse(
            items=[], total=0, page=page_window.page, page_size=page_window.page_size
        )
    filters = ChargingSessionListFilter(
        station_id=station_id,
        connector_id=connector_id,
        organization_id=scope if scope is not None else organization_id,
        started_by=_session_started_by_scope(principal),
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


async def list_charging_session_meter_values(
    db: AsyncSession,
    session_id: UUID,
    *,
    principal: Principal,
    page: int,
    page_size: int,
) -> ChargingSessionMeterValueListResponse:
    """Get paginated energy-register samples for the ``/meter-values`` endpoint.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session whose meter to view.
        principal: The caller; a session out of their reach is not found.
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
    await _get_charging_session_record(db, session_id, principal)
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
    principal: Principal,
    measurand: str | None,
    page: int,
    page_size: int,
) -> ChargingSessionMeasurementListResponse:
    """Get paginated measurements (any measurand) for the monitoring endpoint.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session whose measurements to view.
        principal: The caller; a session out of their reach is not found.
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
    await _get_charging_session_record(db, session_id, principal)
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


async def create_pending_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    organization_id: UUID,
    started_by: UUID,
    vehicle_id: UUID | None = None,
) -> PendingSessionReference:
    """Create a PENDING session for a QR scan and issue its single-use token.

    The token is sent to the charger in the remote start; the charger echoes it
    in its start message, which is how that message finds this row (CE-11).
    Sending the remote start, checking the scanning user's wallet and taking
    the truck from the user's driving session come with the QR start flow
    (WP8); this function is the row creation they build on.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the charger the scan named.
        organization_id: UUID of the organization that pays.
        started_by: UUID of the user who scanned the code.
        vehicle_id: UUID of the truck being charged, if known (CE-13).

    Returns:
        A reference holding the new session's ID and its token.

    Side Effects:
        Inserts one PENDING row in the caller's transaction; an unknown
        station, organization, user or vehicle fails at flush time with an
        ``IntegrityError``.
    """
    id_token = secrets.token_urlsafe(QR_TOKEN_LENGTH)[:QR_TOKEN_LENGTH]
    session_record = await charging_session_repository.create_pending_session(
        db,
        station_id=station_id,
        organization_id=organization_id,
        started_by=started_by,
        vehicle_id=vehicle_id,
        id_token=id_token,
    )
    return PendingSessionReference(
        session_id=session_record.session_id,
        station_id=station_id,
        id_token=id_token,
    )


async def scan_charging_session(
    db: AsyncSession,
    scan_request: ChargingSessionScanRequest,
    *,
    principal: Principal,
) -> ChargingSessionScanResponse:
    """Create the PENDING session of a scan made by the authenticated caller.

    The payer is the organization the caller acts for (internal staff may name
    another one) and the scanning user is the caller (CE-10).

    Args:
        db: The async session owned by the HTTP boundary.
        scan_request: The charger and optional truck / paying organization.
        principal: The caller.

    Returns:
        The new session's ID, status ``PENDING`` and single-use token.

    Raises:
        OrganizationNotFoundError: The named paying organization does not
            exist or is out of the caller's reach.

    Side Effects:
        One insert (see ``create_pending_session``).
    """
    paying_organization_id = await identity_service.resolve_organization_for_new_record(
        db, principal, scan_request.organization_id
    )
    pending_session = await create_pending_session(
        db,
        station_id=scan_request.station_id,
        organization_id=paying_organization_id,
        started_by=principal.user_id,
        vehicle_id=scan_request.vehicle_id,
    )
    return ChargingSessionScanResponse(
        session_id=pending_session.session_id,
        station_id=pending_session.station_id,
        status=SessionStatus.PENDING,
        id_token=pending_session.id_token,
    )


async def activate_pending_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    id_token: str,
    transaction_id: str,
    started_at: datetime,
    meter_start_wh: Decimal | None,
) -> TransactionIngestResult:
    """Turn the PENDING session holding a start message's token ACTIVE (CE-11).

    Rule:
        1. Every input field is validated and normalized before anything is
           read or written.
        2. The token must belong to a PENDING session of the same charger;
           otherwise the start is refused and no row is created.
        3. The charger's gun, transaction ID, start time and start reading are
           filled in and the status becomes ``ACTIVE``.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the charger that sent the message.
        evse_id: UUID of the EVSE the transaction runs on.
        connector_id: UUID of the connector the transaction runs on.
        id_token: The token the message carries.
        transaction_id: The charger's transaction identity (a 1.6J number as
            text, or a 2.0.1 string of at most 36 characters).
        started_at: The charger's start time; must have a timezone.
        meter_start_wh: The start reading in Wh; required, because an ACTIVE
            session always has one (CE-10).

    Returns:
        The session ID and its status after the start.

    Raises:
        ChargingSessionInputError: If a field is empty, over-long, naive or
            negative, or the start reading is missing.
        ChargingSessionTokenError: If no PENDING session of this charger holds
            the token (CE-11).

    Side Effects:
        Updates the session in the caller's transaction; a duplicate
        ``(station, transaction ID)`` is refused by the unique index at flush
        time.
    """
    normalized_transaction_id = transaction_id.strip()
    if (
        not normalized_transaction_id
        or len(normalized_transaction_id) > OCPP_TRANSACTION_ID_MAX_LENGTH
    ):
        raise ChargingSessionInputError(
            f"transaction_id is empty or exceeds {OCPP_TRANSACTION_ID_MAX_LENGTH} "
            "characters"
        )
    if not id_token or len(id_token) > ID_TOKEN_MAX_LENGTH:
        raise ChargingSessionInputError(
            f"id_token is empty or exceeds {ID_TOKEN_MAX_LENGTH} characters"
        )
    started_at_utc = _normalize_utc(started_at, "started_at")
    validated_meter_start_wh = _validate_energy(meter_start_wh, "meter_start_wh")
    if validated_meter_start_wh is None:
        raise ChargingSessionInputError("meter_start_wh is required to start a session")
    session_record = await charging_session_repository.find_pending_session_by_token(
        db, station_id, id_token
    )
    if session_record is None:
        # The token is deliberately left out of the message and the log (IS-07).
        raise ChargingSessionTokenError(
            "The start message carries no token issued for this charger"
        )
    session_record.evse_id = evse_id
    session_record.connector_id = connector_id
    session_record.ocpp_transaction_id = normalized_transaction_id
    session_record.started_at = started_at_utc
    session_record.meter_start_wh = validated_meter_start_wh
    session_record.status = SessionStatus.ACTIVE
    _touch_session(session_record)
    return TransactionIngestResult(
        session_id=session_record.session_id, status=session_record.status
    )


async def complete_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    transaction_id: str,
    ended_at: datetime,
    stop_reason: str | None = None,
    meter_stop_wh: Decimal | None = None,
    evse_id: UUID | None = None,
    connector_id: UUID | None = None,
) -> TransactionIngestResult:
    """Complete the ACTIVE session a stop message belongs to.

    Rule:
        1. Every input field is validated and normalized before anything is
           read or written.
        2. The session must exist, match the message's topology (when it
           names one) and be ``ACTIVE`` (F-B2): a duplicate stop or a late
           message must not silently re-mutate a finished record.
        3. The stop time, reason and the charger's closing reading (the
           billing figure, CE-12, stored as sent) are written and the status
           becomes ``COMPLETED``.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the station that raised the transaction.
        transaction_id: The charger's transaction identity.
        ended_at: The charger's stop time; must have a timezone.
        stop_reason: Why the session stopped, as sent (at most 30 characters).
        meter_stop_wh: The closing reading in Wh, if the message carries one.
        evse_id: UUID of the EVSE the message names, if it names one.
        connector_id: UUID of the connector the message names, if it names one.

    Returns:
        The session ID and its status after the stop.

    Raises:
        ChargingSessionInputError: If a field is invalid or the topology does
            not match.
        ChargingSessionNotFoundError: If the station has no such transaction.
        ChargingSessionStateError: If the session is not ``ACTIVE``.

    Side Effects:
        Updates the session in the caller's transaction.
    """
    normalized_transaction_id = transaction_id.strip()
    if not normalized_transaction_id:
        raise ChargingSessionInputError("transaction_id is empty")
    ended_at_utc = _normalize_utc(ended_at, "ended_at")
    validated_meter_stop_wh = _validate_energy(meter_stop_wh, "meter_stop_wh")
    if stop_reason is not None and len(stop_reason) > STOP_REASON_MAX_LENGTH:
        raise ChargingSessionInputError(
            f"stop_reason exceeds {STOP_REASON_MAX_LENGTH} characters"
        )
    session_record = await _get_open_session_by_transaction(
        db,
        station_id=station_id,
        transaction_id=normalized_transaction_id,
        evse_id=evse_id,
        connector_id=connector_id,
    )
    session_record.ended_at = ended_at_utc
    session_record.status = SessionStatus.COMPLETED
    session_record.stop_reason = stop_reason
    session_record.meter_stop_wh = validated_meter_stop_wh
    _touch_session(session_record)
    return TransactionIngestResult(
        session_id=session_record.session_id, status=session_record.status
    )


async def ingest_meter_values(
    db: AsyncSession,
    *,
    session_id: UUID,
    sample: MeterSampleInput,
) -> MeterIngestResult:
    """Store one energy-register sample (Wh) of an ACTIVE session.

    Rule:
        1. The session must exist and be ``ACTIVE`` (F-B2), for the same
           reason as ``complete_session``'s guard.
        2. The sample's time and Wh value are validated.
        3. The sample is appended to the measurements (append-only); the
           session row keeps only the readings the charger declares, so
           nothing else changes except ``updated_at`` (CE-12).

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the session to update.
        sample: The sample, already converted to Wh by the gateway.

    Returns:
        A result containing the session UUID and its status.

    Raises:
        ChargingSessionInputError: If the sample lacks a timezone or the
            energy value is invalid.
        ChargingSessionNotFoundError: If the session does not exist.
        ChargingSessionStateError: If the session is not ``ACTIVE``.

    Side Effects:
        Appends a measurement row and stamps the session in the same
        transaction; the caller must commit or roll back at the entry
        boundary.
    """
    session_record = await _get_open_charging_session_record(db, session_id)
    sampled_at = _normalize_utc(sample.sampled_at, "sampled_at")
    value_wh = _validate_energy(sample.value_wh, "value_wh")
    if value_wh is None:
        raise ChargingSessionInputError("value_wh is required")
    if not sample.context or not sample.measurement_location:
        raise ChargingSessionInputError(
            "context and measurement_location are required (CE-14)"
        )
    await charging_session_repository.insert_measurement(
        db,
        session_id=session_record.session_id,
        sampled_at=sampled_at,
        measurand=ENERGY_ACTIVE_IMPORT_REGISTER,
        value=value_wh,
        unit=ENERGY_UNIT_WH,
        context=sample.context,
        measurement_location=sample.measurement_location,
    )
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
    """Store non-energy measurements of an ACTIVE session (SoC, power, voltage…).

    Rule:
        1. The session must exist and be ``ACTIVE``, for the same reason as in
           ``ingest_meter_values``.
        2. Every sample is validated, in payload order, before the first
           insert (``_validate_measurement``). Vendor-specific measurand
           names are accepted as sent; values may be negative (a
           temperature, an exported power).
        3. Each sample is inserted individually (append-only history, no
           batching).
        4. ``updated_at`` is stamped (``_touch_session``).

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the session that owns the samples.
        samples: The measurements to store, in payload order.

    Returns:
        The number of measurements stored.

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.
        ChargingSessionStateError: If the session is not ``ACTIVE``.
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
            measurement_location=validated_sample.measurement_location,
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
    ``started_at``) and the charger's closing ``meter_stop_wh`` (taken at
    ``ended_at``) are declared on the session row, not stored as samples.
    Adding both as readings makes a finished session's deltas sum to its
    delivered energy; when the closing reading is also a stored sample it only
    adds a zero delta.

    Args:
        session_record: The session.
        samples: The session's stored outlet energy samples, ``(sampled_at,
            value_wh)``.

    Returns:
        The start reading (if any), the samples, then the closing reading (if
        any), in that order before sorting by time.
    """
    readings: list[tuple[datetime, Decimal]] = []
    if session_record.meter_start_wh is not None and session_record.started_at:
        readings.append((session_record.started_at, session_record.meter_start_wh))
    readings.extend(samples)
    if session_record.meter_stop_wh is not None and session_record.ended_at:
        readings.append((session_record.ended_at, session_record.meter_stop_wh))
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
    """Tell whether a connector already has an open (``ACTIVE``) session.

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
    if session_record.evse_id is None or session_record.connector_id is None:
        raise ChargingSessionStateError(
            f"Transaction '{transaction_id}' has no gun recorded"
        )
    return TransactionSessionReference(
        session_id=session_record.session_id,
        station_id=session_record.station_id,
        evse_id=session_record.evse_id,
        connector_id=session_record.connector_id,
        status=session_record.status,
    )


async def resolve_session_command_reference(
    db: AsyncSession, session_id: UUID
) -> SessionCommandReference:
    """Read the token and transaction ID the gateway sends in a remote command.

    The remote start's token is kept on the session, not copied into the
    command (CS-20); the gateway reads it here when it sends the command.

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the session.

    Returns:
        A frozen reference with the session's token and transaction ID.

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.
    """
    session_record = await charging_session_repository.get_session_by_id(db, session_id)
    if session_record is None:
        raise ChargingSessionNotFoundError(f"Session '{session_id}' not found")
    return SessionCommandReference(
        session_id=session_record.session_id,
        id_token=session_record.id_token,
        ocpp_transaction_id=session_record.ocpp_transaction_id,
    )
