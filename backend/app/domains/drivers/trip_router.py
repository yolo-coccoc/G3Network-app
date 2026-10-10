"""FastAPI router for the trip endpoints of the drivers domain (MON-11, DR-12).

Mounted at ``/api/v1/trips``. Domain exceptions are mapped to HTTP statuses by
the handlers in ``app/api/main.py``. A manager plans, reroutes and cancels; the
driver starts and finishes in the app; both list and open trips within their
own reach (the service decides, 404 outside it).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.trip_service as trip_service
from app.domains.drivers.schemas import (
    PersonalTripStartRequest,
    TripCancelRequest,
    TripListResponse,
    TripPlanRequest,
    TripResponse,
    TripStartRequest,
    TripUpdateRequest,
)
from app.domains.drivers.types import TripStatus
from app.domains.identity.dependencies import require_roles
from app.domains.identity.types import Principal, roles_for
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["trips"])

# MON-11 users: the driver runs trips, the fleet manager plans them (the
# service refuses a plan from a DRIVER-only caller).
TRIP_USERS = require_roles(*roles_for("MON-11"))


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=TripResponse,
    summary="Plan a trip",
    description=(
        "A fleet manager plans a trip: from A to B, planned departure and "
        "arrival, optionally a driver and a truck."
    ),
)
async def plan_trip_endpoint(
    trip_plan_request: TripPlanRequest,
    principal: Principal = Depends(TRIP_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TripResponse:
    """Plan a trip.

    Args:
        trip_plan_request: The plan.
        principal: The authenticated manager.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The PLANNED trip.

    Raises:
        DriverNotFoundError: 404 when the driver is not in the caller's reach.
        TripVehicleNotFoundError: 404 when the truck is not in the caller's reach.
    """
    return await trip_service.plan_trip(
        db_session, trip_plan_request, principal=principal
    )


@router.post(
    "/start-personal",
    status_code=status.HTTP_201_CREATED,
    response_model=TripResponse,
    summary="Start a personal trip",
    description=(
        "A driver with no plan creates and starts a trip in their open driving session."
    ),
)
async def start_personal_trip_endpoint(
    personal_trip_request: PersonalTripStartRequest,
    principal: Principal = Depends(TRIP_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TripResponse:
    """Create and start a personal trip.

    Args:
        personal_trip_request: Optional places and the declared load.
        principal: The authenticated driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The IN_PROGRESS trip.

    Raises:
        TripNotCheckedInError: 409 when the driver is not checked in.
        TripInProgressConflictError: 409 when a trip already runs.
    """
    return await trip_service.start_personal_trip(
        db_session, personal_trip_request, principal=principal
    )


@router.get(
    "/",
    response_model=TripListResponse,
    summary="Get the list of trips",
    description=(
        "The dispatch board for a manager, or the driver's own trips "
        "(assigned, in progress, done)."
    ),
)
async def list_trips_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    statuses: list[TripStatus] | None = Query(
        None, alias="status", description="Only these statuses (repeat the parameter)"
    ),
    mine: bool = Query(False, description="Only the caller's own trips"),
    driver_id: UUID | None = Query(None, description="A manager's driver filter"),
    from_time: datetime | None = Query(
        None, alias="from", description="Planned (or actual) start from"
    ),
    to_time: datetime | None = Query(
        None, alias="to", description="Planned (or actual) start before"
    ),
    principal: Principal = Depends(TRIP_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TripListResponse:
    """Get a paginated list of trips.

    Args:
        page: Page number.
        page_size: Number of records per page.
        statuses: Status filter, if any.
        mine: Own trips only.
        driver_id: Driver filter for a manager.
        from_time: Lower time bound.
        to_time: Exclusive upper time bound.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of trips.
    """
    return await trip_service.list_trips(
        db_session,
        principal=principal,
        page=page,
        page_size=page_size,
        statuses=statuses,
        mine=mine,
        driver_id=driver_id,
        from_time=from_time,
        to_time=to_time,
    )


@router.get(
    "/{trip_id}",
    response_model=TripResponse,
    summary="Get trip details",
    description="Plan, actual run, and distance, energy, kWh/km and cost.",
)
async def get_trip_endpoint(
    trip_id: UUID,
    principal: Principal = Depends(TRIP_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TripResponse:
    """Get one trip.

    Args:
        trip_id: Internal ID of the trip.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The trip.

    Raises:
        TripNotFoundError: 404 when the trip is not in the caller's reach.
    """
    return await trip_service.get_trip(db_session, trip_id, principal=principal)


@router.patch(
    "/{trip_id}",
    response_model=TripResponse,
    summary="Reroute or reassign a planned trip",
    description="Only a PLANNED trip can change; the reason goes to the history.",
)
async def update_trip_endpoint(
    trip_id: UUID,
    trip_update_request: TripUpdateRequest,
    principal: Principal = Depends(TRIP_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TripResponse:
    """Change a planned trip.

    Args:
        trip_id: Internal ID of the trip.
        trip_update_request: The new plan fields and the reason.
        principal: The authenticated manager.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The updated trip.

    Raises:
        TripNotFoundError: 404 when the trip is not in the caller's reach.
        TripStateConflictError: 409 when the trip is not PLANNED.
    """
    return await trip_service.update_trip(
        db_session, trip_id, trip_update_request, principal=principal
    )


@router.post(
    "/{trip_id}/cancel",
    response_model=TripResponse,
    summary="Cancel a planned trip",
    description="Only a PLANNED trip can be cancelled; the reason is required.",
)
async def cancel_trip_endpoint(
    trip_id: UUID,
    trip_cancel_request: TripCancelRequest,
    principal: Principal = Depends(TRIP_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TripResponse:
    """Cancel a planned trip.

    Args:
        trip_id: Internal ID of the trip.
        trip_cancel_request: Why.
        principal: The authenticated manager.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The CANCELLED trip.

    Raises:
        TripNotFoundError: 404 when the trip is not in the caller's reach.
        TripStateConflictError: 409 when the trip is not PLANNED.
    """
    return await trip_service.cancel_trip(
        db_session, trip_id, trip_cancel_request.reason, principal=principal
    )


@router.post(
    "/{trip_id}/start",
    response_model=TripResponse,
    summary="Start a trip",
    description=(
        "The driver presses Start: the trip joins the open driving session and "
        "the T-Box position, odometer and battery % are recorded."
    ),
)
async def start_trip_endpoint(
    trip_id: UUID,
    trip_start_request: TripStartRequest,
    principal: Principal = Depends(TRIP_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TripResponse:
    """Start a planned trip.

    Args:
        trip_id: Internal ID of the trip.
        trip_start_request: The declared load.
        principal: The authenticated driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The IN_PROGRESS trip.

    Raises:
        TripNotFoundError: 404 when the trip is not the caller's.
        TripStateConflictError: 409 when the trip is not PLANNED.
        TripNotCheckedInError: 409 when the driver is not checked in.
        TripInProgressConflictError: 409 when a trip already runs.
    """
    return await trip_service.start_trip(
        db_session, trip_id, trip_start_request, principal=principal
    )


@router.post(
    "/{trip_id}/finish",
    response_model=TripResponse,
    summary="Finish a trip",
    description="The driver presses Finish: the end readings are recorded.",
)
async def finish_trip_endpoint(
    trip_id: UUID,
    principal: Principal = Depends(TRIP_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TripResponse:
    """Finish a trip in progress.

    Args:
        trip_id: Internal ID of the trip.
        principal: The authenticated driver.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The COMPLETED trip.

    Raises:
        TripNotFoundError: 404 when the trip was not run by the caller.
        TripStateConflictError: 409 when the trip is not IN_PROGRESS.
    """
    return await trip_service.finish_trip(db_session, trip_id, principal=principal)
