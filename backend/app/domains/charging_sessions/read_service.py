"""Session reads for the API: lists, detail, meter values and measurements (CHG-02/04/05).

The staff list with its filters and the fleet limit (FL-14), the person's own
sessions (`mine`), one session's detail, its energy-register samples and all
its measurements. Internal module: the domain's router calls it;
``service.py`` wraps ``get_charging_session`` and
``get_completed_charging_session`` for the QR charge flow. Runs in the
caller's transaction and never commits.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.access as session_access
import app.domains.charging_sessions.mappers as session_mappers
import app.domains.charging_sessions.repository as charging_session_repository
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionStateError,
)
from app.domains.charging_sessions.schemas import (
    ChargingSessionDetailResponse,
    ChargingSessionListResponse,
    ChargingSessionMeasurementListResponse,
    ChargingSessionMeasurementResponse,
    ChargingSessionMeterValueListResponse,
)
from app.domains.charging_sessions.types import (
    ChargingSessionListFilter,
    SessionStatus,
)
from app.domains.identity.types import Principal
from app.libs.common.pagination import PageWindow, normalize_page_window


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


async def get_charging_session(
    db: AsyncSession,
    session_id: UUID,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> ChargingSessionDetailResponse:
    """Get the session aggregate and its read-time summary (F-B2).

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the aggregate to view.
        principal: The caller; a session out of their reach is not found.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a session on another truck or with no truck is not found unless
            the caller started it (hardening D5). `None` means no limit.

    Returns:
        The session response plus ``duration_seconds``,
        ``soc_start_percent``, ``soc_end_percent`` and ``max_power_kw``, all
        computed now from the stored measurements (never stored).

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.

    Side Effects:
        Performs one aggregate query and four measurement queries; does not
        commit or roll back.
    """
    session_record = await session_access.get_charging_session_record(
        db, session_id, principal, visible_vehicle_ids=visible_vehicle_ids
    )
    return await session_mappers.build_charging_session_detail_response(
        db, session_record
    )


async def get_completed_charging_session(
    db: AsyncSession,
    session_id: UUID,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> ChargingSessionDetailResponse:
    """Get a session a receipt may be shown for (CHG-03).

    A receipt exists only once the charge completed: a PENDING, ACTIVE or
    ABANDONED session has none.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session.
        principal: The caller; a session out of their reach is not found.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10),
            as for `get_charging_session`; `None` means no limit.

    Returns:
        The session detail, as `get_charging_session` returns it.

    Raises:
        ChargingSessionNotFoundError: The session does not exist or is out of
            the caller's reach.
        ChargingSessionStateError: The session has not completed.

    Side Effects:
        The read queries of `get_charging_session`; nothing is written.
    """
    session = await get_charging_session(
        db,
        session_id,
        principal=principal,
        visible_vehicle_ids=visible_vehicle_ids,
    )
    if session.status is not SessionStatus.COMPLETED:
        raise ChargingSessionStateError(
            f"Session '{session_id}' is {session.status.value}; "
            "a receipt exists only after the charge completed"
        )
    return session


async def list_charging_sessions(
    db: AsyncSession,
    *,
    principal: Principal,
    page: int,
    page_size: int,
    station_id: UUID | None = None,
    connector_id: UUID | None = None,
    organization_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    started_by: UUID | None = None,
    status: SessionStatus | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    visible_vehicle_ids: frozenset[UUID] | None = None,
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
        vehicle_id: Only sessions attributed to this truck, if given (CHG-07).
        started_by: Only sessions scanned by this user (the driver at the
            wheel), if given; a DRIVER-only caller always gets their own.
        status: Only sessions in this status, if given.
        started_from: Only sessions with ``started_at >= started_from``;
            must carry a timezone.
        started_to: Only sessions with ``started_at < started_to``; must
            carry a timezone and, with ``started_from``, be after it.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            only sessions on them, plus the ones the caller started, are
            listed (a session with no truck is hidden, hardening D5). `None`
            means no limit.

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
        session_access.normalize_utc(started_from, "started_from")
        if started_from is not None
        else None
    )
    normalized_to = (
        session_access.normalize_utc(started_to, "started_to")
        if started_to is not None
        else None
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
        started_by=session_access.session_started_by_scope(principal) or started_by,
        vehicle_id=vehicle_id,
        status=status,
        started_from=normalized_from,
        started_to=normalized_to,
        vehicle_ids=visible_vehicle_ids,
        caller_user_id=principal.user_id if visible_vehicle_ids is not None else None,
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
        to_item=session_mappers.to_charging_session_response,
    )


async def list_my_charging_sessions(
    db: AsyncSession,
    *,
    principal: Principal,
    page: int,
    page_size: int,
    vehicle_id: UUID | None = None,
    status: SessionStatus | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
) -> ChargingSessionListResponse:
    """List the caller's own charging history, newest first (CHG-04).

    The history follows the person, not the organization they act for now: the
    scanning user's own wallet pays (BL-13), so every session they started is
    listed whichever organization it was recorded under.

    Args:
        db: The async session owned by the HTTP boundary.
        principal: The caller; only sessions they started are listed.
        page: The page, starting at one.
        page_size: The page size.
        vehicle_id: Only sessions attributed to this truck, if given.
        status: Only sessions in this status, if given.
        started_from: Inclusive lower bound on ``started_at`` (timezone needed).
        started_to: Exclusive upper bound on ``started_at`` (timezone needed).

    Returns:
        The caller's sessions and pagination metadata.

    Raises:
        ChargingSessionInputError: A time bound lacks a timezone or
            ``started_to`` is not after ``started_from``.
    """
    normalized_from = (
        session_access.normalize_utc(started_from, "started_from")
        if started_from is not None
        else None
    )
    normalized_to = (
        session_access.normalize_utc(started_to, "started_to")
        if started_to is not None
        else None
    )
    if (
        normalized_from is not None
        and normalized_to is not None
        and normalized_to <= normalized_from
    ):
        raise ChargingSessionInputError("started_to must be after started_from")
    filters = ChargingSessionListFilter(
        started_by=principal.user_id,
        vehicle_id=vehicle_id,
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
        to_item=session_mappers.to_charging_session_response,
    )


async def list_charging_session_meter_values(
    db: AsyncSession,
    session_id: UUID,
    *,
    principal: Principal,
    page: int,
    page_size: int,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> ChargingSessionMeterValueListResponse:
    """Get paginated energy-register samples for the ``/meter-values`` endpoint.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session whose meter to view.
        principal: The caller; a session out of their reach is not found.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10),
            as in ``get_charging_session``; `None` means no limit.
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
    await session_access.get_charging_session_record(
        db, session_id, principal, visible_vehicle_ids=visible_vehicle_ids
    )
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
        to_item=session_mappers.to_charging_session_meter_value_response,
    )


async def list_charging_session_measurements(
    db: AsyncSession,
    session_id: UUID,
    *,
    principal: Principal,
    measurand: str | None,
    page: int,
    page_size: int,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> ChargingSessionMeasurementListResponse:
    """Get paginated measurements (any measurand) for the monitoring endpoint.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session whose measurements to view.
        principal: The caller; a session out of their reach is not found.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10),
            as in ``get_charging_session``; `None` means no limit.
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
    await session_access.get_charging_session_record(
        db, session_id, principal, visible_vehicle_ids=visible_vehicle_ids
    )
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
