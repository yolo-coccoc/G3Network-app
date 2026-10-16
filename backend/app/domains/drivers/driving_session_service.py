"""Driving sessions: check-in, check-out, lists, auto-end and the driver's summary (DRV-02, DR-07, DR-11).

A driver checks in to a truck by QR in the app or is checked in by a manager
from the portal (DR-18: the DRIVER role, the truck in the caller's reach, the
fleet limit); a check-out or a newer check-in ends the session, closing its
running trip; the auto-end worker ends a session whose truck has been idle
(DR-07). Also the session lists for managers and the driver's own summary.
Internal module: the domain's routers and the auto-end worker call it;
``service.py`` keeps the cross-domain lookups and hooks and reaches the
session end through ``end_session``. Runs in the caller's transaction and
never commits.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.eligibility as driver_eligibility
import app.domains.drivers.lookups as driver_lookups
import app.domains.drivers.repository as driver_repository
import app.domains.identity.service as identity_service
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.domains.drivers.exceptions import (
    DriverCheckInLocationRequiredError,
    DriverNotEligibleError,
    DriverNotFoundError,
    DriverRoleMissingError,
    DriverTooFarFromVehicleError,
    DriverVehicleNotFoundError,
    DrivingSessionConflictError,
    DrivingSessionNotFoundError,
    DrivingSummaryRangeError,
)
from app.domains.drivers.models import DriverModel, DrivingSessionModel
from app.domains.drivers.schemas import (
    DrivingSessionCheckInRequest,
    DrivingSessionCheckOutRequest,
    DrivingSessionListResponse,
    DrivingSessionResponse,
    DrivingSummaryResponse,
    DrivingSummarySessionItem,
    DrivingSummaryTotal,
)
from app.domains.drivers.types import (
    AutoEndSweepResult,
    CheckInMethod,
    CheckInWarning,
    DriverStatus,
    DrivingSessionEndCause,
    TripStatus,
)
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    MembershipStatus,
    Principal,
    UserRole,
    UserStatus,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import (
    calculate_distance_m,
    coordinates_to_location,
)
from app.libs.common.pagination import normalize_page_window

logger = logging.getLogger(__name__)

TRIP_AUTO_CLOSED_REASON = "Closed automatically when the driving session ended"


# The driver's own summary is cut at this range and number of sessions, so one
# call stays bounded (a driver has a few sessions a day).
SUMMARY_DEFAULT_RANGE_DAYS = 30


SUMMARY_MAX_RANGE_DAYS = 366


SUMMARY_MAX_SESSIONS = 1000


async def _auto_close_trip(
    db_session: AsyncSession,
    session_record: DrivingSessionModel,
    *,
    ended_at: datetime,
    changed_by: UUID | None,
) -> None:
    """Close the trip running in a driving session that ends (DR-12).

    The trip becomes COMPLETED with the reason that the system closed it. The
    end position, odometer and battery % are taken from the truck's latest
    sample when it is recent, otherwise left empty.

    Args:
        db_session: Current database session.
        session_record: The session that ends.
        ended_at: When the session ends.
        changed_by: The acting user, or `None` for the auto-end worker.

    Side Effects:
        Updates the trip (and its history row) in the caller's transaction.
    """
    trip_record = await driver_repository.find_in_progress_trip_by_session(
        db_session, session_record.driving_session_id
    )
    if trip_record is None:
        return
    # A session may end before the trip started when the auto-end reference is
    # a past movement time: the trip then lasts zero seconds, never less.
    trip_ended_at = (
        max(ended_at, trip_record.started_at) if trip_record.started_at else ended_at
    )
    values: dict[str, object] = {
        "status": TripStatus.COMPLETED.value,
        "status_reason": TRIP_AUTO_CLOSED_REASON,
        "ended_at": trip_ended_at,
    }
    reading = await driver_lookups.read_recent_truck_reading(
        db_session, session_record.vehicle_id, now=utc_now()
    )
    if reading is not None:
        values["end_location"] = coordinates_to_location(
            reading.latitude, reading.longitude
        )
        if reading.odometer_km is not None:
            values["end_odometer_km"] = Decimal(str(round(reading.odometer_km, 1)))
        if reading.soc_percent is not None:
            values["end_soc_percent"] = Decimal(str(round(reading.soc_percent, 2)))
    # Conditional: a trip the driver finished meanwhile stays as they left it.
    await driver_repository.update_trip_fields(
        db_session,
        trip_record,
        values,
        change_reason=TRIP_AUTO_CLOSED_REASON,
        changed_by=changed_by,
        expected_status=TripStatus.IN_PROGRESS.value,
    )


async def end_session(
    db_session: AsyncSession,
    session_record: DrivingSessionModel,
    *,
    ended_at: datetime,
    end_cause: DrivingSessionEndCause,
    changed_by: UUID | None,
) -> DrivingSessionModel:
    """End a driving session and close the trip still running in it (DR-12).

    Every way a session can end goes through here, so a trip never stays
    IN_PROGRESS against a closed session.

    Args:
        db_session: Current database session.
        session_record: The open session.
        ended_at: When it ends.
        end_cause: Why it ends.
        changed_by: The acting user, or `None` for the system.

    Returns:
        The closed session.

    Raises:
        DrivingSessionConflictError: Another transaction already ended it.
    """
    closed_session = await driver_repository.close_session(
        db_session, session_record, ended_at=ended_at, end_cause=end_cause
    )
    if closed_session is None:
        raise DrivingSessionConflictError("The driving session has already ended")
    await _auto_close_trip(
        db_session, closed_session, ended_at=ended_at, changed_by=changed_by
    )
    return closed_session


async def build_driving_session_response(
    db_session: AsyncSession,
    session_record: DrivingSessionModel,
    ended_session_records: list[DrivingSessionModel] | None = None,
    warnings: list[CheckInWarning] | None = None,
) -> DrivingSessionResponse:
    """Build a driving session response with the truck's VIN.

    Args:
        db_session: Current database session.
        session_record: The session to present.
        ended_session_records: Sessions the same check-in ended, if any.
        warnings: Notices of the check-in, if this response answers one.

    Returns:
        Response data; `vehicle_vin` is `None` if the truck no longer resolves.

    Side Effects:
        One per-row call to the vehicles domain's public service (no
        batching, per the repo's query-batching rule). Read-only.
    """
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db_session, session_record.vehicle_id
    )
    return DrivingSessionResponse(
        driving_session_id=session_record.driving_session_id,
        organization_id=session_record.organization_id,
        driver_id=session_record.driver_id,
        vehicle_id=session_record.vehicle_id,
        vehicle_vin=vehicle_reference.vin if vehicle_reference else None,
        check_in_method=CheckInMethod(session_record.check_in_method),
        started_at=session_record.started_at,
        ended_at=session_record.ended_at,
        end_cause=(
            DrivingSessionEndCause(session_record.end_cause)
            if session_record.end_cause
            else None
        ),
        ended_sessions=[
            await build_driving_session_response(db_session, ended_record)
            for ended_record in (ended_session_records or [])
        ],
        warnings=warnings or [],
    )


async def _resolve_acting_driver(
    db_session: AsyncSession, requested_driver_id: UUID | None, principal: Principal
) -> DriverModel:
    """Find the driver profile a check-in or check-out acts for.

    Args:
        db_session: Current database session.
        requested_driver_id: The driver named in the request, if any.
        principal: The caller.

    Returns:
        The caller's own live profile when no driver is named (or the caller
        names themselves); otherwise the named profile, which only a manager
        may name and only inside their data reach.

    Raises:
        DriverNotFoundError: The caller has no live profile, or the named
            driver does not exist in the caller's reach.
        AccessDeniedError: A caller who is not a manager named another driver.
    """
    own_record = await driver_repository.find_by_membership_id(
        db_session, principal.membership_id
    )
    if own_record is not None and own_record.deleted_at is not None:
        own_record = None
    if requested_driver_id is None:
        if own_record is None:
            raise DriverNotFoundError("The caller has no driver profile")
        return own_record
    if own_record is not None and own_record.driver_id == requested_driver_id:
        return own_record
    if not principal.has_any_role(*driver_eligibility.DRIVER_MANAGER_ROLES):
        raise AccessDeniedError("Only a manager can act for another driver")
    named_record = await driver_repository.get_by_id(
        db_session, requested_driver_id, organization_id=principal.data_scope
    )
    if named_record is None:
        raise DriverNotFoundError(f"Driver with id '{requested_driver_id}' not found")
    return named_record


async def check_in_driver(
    db_session: AsyncSession,
    check_in_request: DrivingSessionCheckInRequest,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> DrivingSessionResponse:
    """Check a driver in to a truck (DR-07, DR-10; DRV-02, DRV-04).

    Checking in also identifies who drives the shift (DRV-04): the session is
    the shift record, with its start and end.

    Rules:
        The driver must be ACTIVE, not deleted, with an unexpired licence, the
        membership must hold the DRIVER role, and the membership and the user
        must be ACTIVE. A driver checking themselves in by QR or from the app
        may take a truck of another organization (DR-07, DR-11); the response
        only warns. A managed check-in - a manager acting for another driver,
        or any PORTAL check-in, the manager's own profile included - needs the
        truck in the caller's reach (its organization, internal staff keep
        theirs) and inside the caller's fleet limit (FL-10), otherwise the
        truck is not found: the portal has no phone position to prove the
        caller stands at the truck. A QR or APP
        check-in carries the phone position and is refused when it is farther
        than ``settings.DRIVERS_CHECKIN_MAX_DISTANCE_M`` from the truck's last
        T-Box position; a truck with no recent position cannot be checked, so
        the check-in goes through with a `NO_RECENT_TRUCK_POSITION` warning
        (and a log line). Checking in to the truck the driver is already at
        the wheel of returns that session unchanged. Otherwise the driver's
        open session on another truck ends `OTHER_TRUCK` and the truck's
        current session ends `TAKEN_OVER` (each also closes its running
        trip), then the new session opens with `organization_id` = the
        truck's owner now.

    Args:
        db_session: Database session owned by the entry boundary.
        check_in_request: Driver, truck code (VIN or plate), method and the
            phone position.
        principal: The caller. Without a driver in the request the caller's
            own profile checks in (by QR or APP the truck may belong to another
            organization: a driver scans any truck, DR-07); a manager may
            check in another driver of their organization, then the truck
            must be in the manager's reach too, and only a manager may use
            the PORTAL method.
        visible_vehicle_ids: The caller's fleet limit (FL-10), `None` when
            the caller is not limited; applied to a managed check-in only.

    Returns:
        The open session; `ended_sessions` lists the ones this check-in ended
        and `warnings` the notices.

    Raises:
        DriverNotFoundError: When the driver does not exist in the caller's
            reach.
        AccessDeniedError: A non-manager named another driver or used PORTAL.
        DriverCheckInLocationRequiredError: A QR or APP check-in without the
            phone position.
        DriverVehicleNotFoundError: When the code does not resolve to a
            vehicle, or a managed check-in names a truck outside the caller's
            reach or fleet limit.
        DriverNotEligibleError: When the driver may not check in.
        DriverRoleMissingError: When the driver's membership does not hold
            the DRIVER role.
        DriverTooFarFromVehicleError: The phone is too far from the truck.
        DrivingSessionConflictError: When a concurrent check-in won the race.

    Side Effects:
        May close up to two sessions (and their running trips) and inserts
        one, in the caller's transaction; does not commit or rollback.
    """
    is_manager = principal.has_any_role(*driver_eligibility.DRIVER_MANAGER_ROLES)
    if check_in_request.check_in_method == CheckInMethod.PORTAL and not is_manager:
        raise AccessDeniedError("Only a manager can check a driver in from the portal")
    if (
        check_in_request.check_in_method != CheckInMethod.PORTAL
        and check_in_request.latitude is None
    ):
        raise DriverCheckInLocationRequiredError(
            "The phone position is needed to check in by QR or from the app"
        )
    driver_record = await _resolve_acting_driver(
        db_session, check_in_request.driver_id, principal
    )
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_code(
        db_session, check_in_request.truck_code
    )
    # A manager acting for someone, or anyone using the portal (no phone
    # position proves they stand at the truck), works inside their own reach
    # and fleet limit. Only a driver's own QR/APP check-in may borrow a truck
    # of another organization (DR-11).
    is_managed_check_in = (
        driver_record.membership_id != principal.membership_id
        or check_in_request.check_in_method == CheckInMethod.PORTAL
    )
    if vehicle_reference is None or (
        is_managed_check_in
        and (
            not principal.can_access_organization(vehicle_reference.organization_id)
            or (
                visible_vehicle_ids is not None
                and vehicle_reference.vehicle_id not in visible_vehicle_ids
            )
        )
    ):
        raise DriverVehicleNotFoundError(
            f"Vehicle '{check_in_request.truck_code}' not found"
        )

    person = await identity_service.resolve_membership_person_reference(
        db_session, driver_record.membership_id
    )
    if driver_record.status != DriverStatus.ACTIVE:
        raise DriverNotEligibleError("The driver profile is not ACTIVE")
    if driver_eligibility.is_license_expired(driver_record.license_expires_on):
        raise DriverNotEligibleError("The driver's licence has expired")
    if (
        person is None
        or person.membership_status != MembershipStatus.ACTIVE.value
        or person.left_at is not None
        or person.user_status != UserStatus.ACTIVE.value
    ):
        raise DriverNotEligibleError("The driver's membership or account is not ACTIVE")
    if not await identity_service.membership_holds_role(
        db_session, driver_record.membership_id, UserRole.DRIVER
    ):
        raise DriverRoleMissingError(
            "The membership does not hold the DRIVER role; it cannot check in"
        )

    driver_open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    if (
        driver_open_session is not None
        and driver_open_session.vehicle_id == vehicle_reference.vehicle_id
    ):
        return await build_driving_session_response(db_session, driver_open_session)

    now = utc_now()
    warnings: list[CheckInWarning] = []
    if person.organization_id != vehicle_reference.organization_id:
        warnings.append(CheckInWarning.OTHER_ORGANIZATION)
    if (
        check_in_request.check_in_method != CheckInMethod.PORTAL
        and check_in_request.latitude is not None
        and check_in_request.longitude is not None
    ):
        truck_reading = await driver_lookups.read_recent_truck_reading(
            db_session, vehicle_reference.vehicle_id, now=now
        )
        if truck_reading is None:
            warnings.append(CheckInWarning.NO_RECENT_TRUCK_POSITION)
            logger.info(
                "check-in without a recent truck position",
                extra={
                    "vehicle_id": str(vehicle_reference.vehicle_id),
                    "driver_id": str(driver_record.driver_id),
                },
            )
        else:
            distance_m = calculate_distance_m(
                check_in_request.latitude,
                check_in_request.longitude,
                truck_reading.latitude,
                truck_reading.longitude,
            )
            if distance_m > settings.DRIVERS_CHECKIN_MAX_DISTANCE_M:
                raise DriverTooFarFromVehicleError(
                    "The phone is too far from the truck "
                    f"({round(distance_m)} m; at most "
                    f"{round(settings.DRIVERS_CHECKIN_MAX_DISTANCE_M)} m)"
                )

    ended_session_records: list[DrivingSessionModel] = []
    if driver_open_session is not None:
        ended_session_records.append(
            await end_session(
                db_session,
                driver_open_session,
                ended_at=now,
                end_cause=DrivingSessionEndCause.OTHER_TRUCK,
                changed_by=principal.user_id,
            )
        )
    vehicle_open_session = await driver_repository.find_open_session_by_vehicle(
        db_session, vehicle_reference.vehicle_id
    )
    if vehicle_open_session is not None:
        ended_session_records.append(
            await end_session(
                db_session,
                vehicle_open_session,
                ended_at=now,
                end_cause=DrivingSessionEndCause.TAKEN_OVER,
                changed_by=principal.user_id,
            )
        )

    try:
        session_record = await driver_repository.insert_session(
            db_session,
            organization_id=vehicle_reference.organization_id,
            driver_id=driver_record.driver_id,
            vehicle_id=vehicle_reference.vehicle_id,
            check_in_method=check_in_request.check_in_method,
            check_in_location=coordinates_to_location(
                check_in_request.latitude, check_in_request.longitude
            ),
            started_at=now,
        )
    except IntegrityError as error:
        raise DrivingSessionConflictError(
            "The truck or the driver already has an open driving session"
        ) from error

    return await build_driving_session_response(
        db_session, session_record, ended_session_records, warnings
    )


async def check_out_driver(
    db_session: AsyncSession,
    check_out_request: DrivingSessionCheckOutRequest,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> DrivingSessionResponse:
    """End a driver's open driving session (`CHECKED_OUT`).

    Args:
        db_session: Database session owned by the entry boundary.
        check_out_request: The driver checking out.
        principal: The caller; without a driver in the request the caller's
            own profile checks out, a manager may check out another driver of
            their organization.
        visible_vehicle_ids: The caller's fleet limit (FL-10), `None` when
            the caller is not limited. A manager acting for another driver
            may only end a session on a truck inside it; a driver's own
            check-out is never limited.

    Returns:
        The closed session.

    Raises:
        DriverNotFoundError: When the driver does not exist in the caller's
            reach.
        AccessDeniedError: A non-manager named another driver.
        DrivingSessionNotFoundError: When the driver has no open session, or
            a manager's fleet limit excludes the session's truck.

    Side Effects:
        A trip still in progress in the session is closed as COMPLETED with
        the reason that the system closed it (DR-12).
    """
    driver_record = await _resolve_acting_driver(
        db_session, check_out_request.driver_id, principal
    )
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    acts_for_another = driver_record.membership_id != principal.membership_id
    if open_session is None or (
        acts_for_another
        and visible_vehicle_ids is not None
        and open_session.vehicle_id not in visible_vehicle_ids
    ):
        raise DrivingSessionNotFoundError(
            f"Driver with id '{driver_record.driver_id}' has no open session"
        )
    closed_session = await end_session(
        db_session,
        open_session,
        ended_at=utc_now(),
        end_cause=DrivingSessionEndCause.CHECKED_OUT,
        changed_by=principal.user_id,
    )
    return await build_driving_session_response(db_session, closed_session)


async def get_current_driving_session(
    db_session: AsyncSession, *, principal: Principal
) -> DrivingSessionResponse:
    """Get the caller's own open driving session (the app's home, DR-07).

    Args:
        db_session: Current database session.
        principal: The caller; their own driver profile is used.

    Returns:
        The open session.

    Raises:
        DriverNotFoundError: The caller has no driver profile.
        DrivingSessionNotFoundError: The caller is not checked in.
    """
    driver_record = await _resolve_acting_driver(db_session, None, principal)
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    if open_session is None:
        raise DrivingSessionNotFoundError("You are not checked in to a truck")
    return await build_driving_session_response(db_session, open_session)


async def list_driving_sessions(
    db_session: AsyncSession,
    *,
    principal: Principal,
    client_context: ClientContext | None,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    driver_id: UUID | None = None,
    vehicle_vin: str | None = None,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> DrivingSessionListResponse:
    """Get a paginated list of driving sessions, newest first.

    A manager (`DRIVER_MANAGER_ROLES`) sees who drove where, which is
    personal data (DR-08): each call writes one `VIEW` row (ACC-18), empty
    pages included. A driver reading their own sessions writes none.

    Args:
        db_session: Current database session.
        principal: The caller. Sessions are read through the truck owner's
            organization recorded on them; a caller who is only a DRIVER sees
            their own sessions, whatever the filters say (DR-11).
        client_context: IP address and user agent of the request, for the
            audit row; `None` when unknown.
        page: Page number, starting from 1.
        page_size: Maximum number of sessions per page.
        driver_id: Only this driver's sessions, if given.
        vehicle_vin: Only this truck's sessions, if given; an unknown VIN
            yields an empty page.
        visible_vehicle_ids: The caller's fleet limit (FL-10): a manager
            limited to some fleets sees only sessions on those trucks.
            `None` means no limit. A DRIVER-only caller's own sessions are
            not limited.

    Returns:
        Paginated session list, open and closed sessions.

    Side Effects:
        For a manager, inserts one `access_audit_logs` row
        (`DRIVING_SESSIONS`) through `identity.service.record_data_access`;
        does not commit.
    """
    session_list_response = await _list_driving_session_page(
        db_session,
        principal=principal,
        page=page,
        page_size=page_size,
        driver_id=driver_id,
        vehicle_vin=vehicle_vin,
        visible_vehicle_ids=visible_vehicle_ids,
    )
    if principal.has_any_role(*driver_eligibility.DRIVER_MANAGER_ROLES):
        # A manager sees who drove where (personal data, DR-08): one VIEW row
        # per screen. A driver reading their own summary needs none.
        await identity_service.record_data_access(
            db_session,
            principal=principal,
            action=AccessAuditAction.VIEW,
            resource_type="DRIVING_SESSIONS",
            resource_id=None if driver_id is None else str(driver_id),
            client_context=client_context,
            details={"vehicle_vin": vehicle_vin} if vehicle_vin else None,
        )
    return session_list_response


async def _list_driving_session_page(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int,
    page_size: int,
    driver_id: UUID | None,
    vehicle_vin: str | None,
    visible_vehicle_ids: frozenset[UUID] | None,
) -> DrivingSessionListResponse:
    """Read one page of driving sessions for `list_driving_sessions`.

    Args:
        db_session: Current database session.
        principal: The caller (see `list_driving_sessions`).
        page: Page number, starting from 1.
        page_size: Maximum number of sessions per page.
        driver_id: Only this driver's sessions, if given; replaced by the
            caller's own profile for a DRIVER-only caller.
        vehicle_vin: Only this truck's sessions, if given.
        visible_vehicle_ids: The caller's fleet limit (FL-10), `None` if none.

    Returns:
        Paginated session list, open and closed sessions.

    Side Effects:
        Read-only queries; does not commit.
    """
    page_window = normalize_page_window(page, page_size)
    organization_id = principal.data_scope
    if not principal.has_any_role(*driver_eligibility.DRIVER_MANAGER_ROLES):
        # A DRIVER-only caller: own sessions only, on any owner's trucks.
        own_record = await driver_repository.find_by_membership_id(
            db_session, principal.membership_id
        )
        if own_record is None:
            return DrivingSessionListResponse(
                items=[],
                total=0,
                page=page_window.page,
                page_size=page_window.page_size,
            )
        driver_id = own_record.driver_id
        organization_id = None
        visible_vehicle_ids = None
    vehicle_id: UUID | None = None
    if vehicle_vin is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
            db_session, vehicle_vin
        )
        if vehicle_reference is None:
            return DrivingSessionListResponse(
                items=[],
                total=0,
                page=page_window.page,
                page_size=page_window.page_size,
            )
        vehicle_id = vehicle_reference.vehicle_id

    session_records = await driver_repository.list_sessions(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
        vehicle_ids=visible_vehicle_ids,
    )
    total = await driver_repository.count_sessions(
        db_session,
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        organization_id=organization_id,
        vehicle_ids=visible_vehicle_ids,
    )
    return DrivingSessionListResponse(
        items=[
            await build_driving_session_response(db_session, session_record)
            for session_record in session_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def end_idle_driving_sessions(
    db_session: AsyncSession, *, now: datetime
) -> AutoEndSweepResult:
    """End the open driving sessions whose truck has not moved for too long (DR-07).

    One sweep, run by ``monitoring/auto_end_worker.py``. For each open session
    the organization's auto-end time is read from its settings (ID-45). The
    countdown starts at the truck's last movement since the check-in, or at
    the check-in itself when it has not moved; stopping or switching off only
    starts it. A session ends when the countdown passed and the truck has
    reported since it started (proof that it stood still), and its end time is
    the countdown's start, so the shift does not include the idle wait.

    Args:
        db_session: Session whose transaction the caller owns.
        now: The current time.

    Returns:
        How many sessions were checked, ended and skipped (a truck with no
        telemetry, such as one without a T-Box, is never auto-ended: nothing
        can tell that it stood still).

    Side Effects:
        Per-session reads and, for an idle one, the same writes as a
        check-out (the running trip is closed too); does not commit.
    """
    open_sessions = await driver_repository.list_open_sessions(db_session)
    ended_count = 0
    skipped_count = 0
    for session_record in open_sessions:
        # identity answers with its own default when the organization has no
        # settings row (`DEFAULT_DRIVING_SESSION_AUTO_END_MINUTES` in
        # identity's organization_service), so drivers keeps no copy of that
        # number. `None` means an unknown organization, which the session's
        # NOT NULL foreign key (ON DELETE RESTRICT) rules out; such a session
        # would be skipped rather than ended on a guessed time.
        organization_settings = await identity_service.resolve_organization_settings(
            db_session, session_record.organization_id
        )
        if organization_settings is None:
            skipped_count += 1
            continue
        auto_end_minutes = organization_settings.driving_session_auto_end_minutes
        last_telemetry_at = await telemetry_service.resolve_last_telemetry_at(
            db_session, session_record.vehicle_id
        )
        if last_telemetry_at is None:
            skipped_count += 1
            continue
        last_movement_at = await telemetry_service.resolve_last_movement_at(
            db_session,
            session_record.vehicle_id,
            since=session_record.started_at,
            min_speed_kmh=settings.DRIVERS_MOVING_SPEED_KMH,
        )
        countdown_start = (
            max(last_movement_at, session_record.started_at)
            if last_movement_at is not None
            else session_record.started_at
        )
        if now - countdown_start < timedelta(minutes=auto_end_minutes):
            continue
        if last_telemetry_at <= countdown_start:
            skipped_count += 1
            continue
        try:
            await end_session(
                db_session,
                session_record,
                ended_at=countdown_start,
                end_cause=DrivingSessionEndCause.AUTO_ENDED,
                changed_by=None,
            )
        except DrivingSessionConflictError:
            # The driver checked out while the sweep ran: their end stands.
            skipped_count += 1
            continue
        ended_count += 1
    return AutoEndSweepResult(
        checked=len(open_sessions), ended=ended_count, skipped=skipped_count
    )


async def get_own_driving_summary(
    db_session: AsyncSession,
    *,
    principal: Principal,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> DrivingSummaryResponse:
    """Build the driver's own driving summary: sessions and totals (DR-11).

    A driver sees only a summary of their own sessions on any owner's trucks:
    date, truck plate, check-in and check-out, duration and distance, plus
    driving-time totals per day and per week. Never the route, the GPS trail,
    stops or places; the distance is the odometer difference.

    Args:
        db_session: Current database session.
        principal: The caller; their own driver profile is used, whatever the
            roles.
        from_time: Start of the range (with a zone); defaults to 30 days
            before ``to_time``.
        to_time: End of the range (with a zone); defaults to now.

    Returns:
        Sessions newest first and totals per day and week; days and weeks are
        cut in ``settings.APP_REPORT_TIMEZONE`` (weeks start on Monday) and a
        session counts in the period it started in. Empty when the caller has
        no driver profile.

    Raises:
        DrivingSummaryRangeError: A time has no zone, the range is empty or
            longer than a year.

    Side Effects:
        One vehicle lookup and one telemetry read per session (no batching,
        per the repo's rule). Read-only.
    """
    now = utc_now()
    range_end = to_time or now
    range_start = from_time or range_end - timedelta(days=SUMMARY_DEFAULT_RANGE_DAYS)
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise DrivingSummaryRangeError("The range times must carry a time zone")
    if range_end <= range_start:
        raise DrivingSummaryRangeError("The range must end after it starts")
    if range_end - range_start > timedelta(days=SUMMARY_MAX_RANGE_DAYS):
        raise DrivingSummaryRangeError(
            f"The range is at most {SUMMARY_MAX_RANGE_DAYS} days"
        )

    empty_response = DrivingSummaryResponse(
        from_time=range_start,
        to_time=range_end,
        sessions=[],
        totals_per_day=[],
        totals_per_week=[],
    )
    driver_record = await driver_repository.find_by_membership_id(
        db_session, principal.membership_id
    )
    if driver_record is None:
        return empty_response
    session_records = await driver_repository.list_sessions_in_range(
        db_session,
        driver_id=driver_record.driver_id,
        start_time=range_start,
        end_time=range_end,
        limit=SUMMARY_MAX_SESSIONS,
    )

    report_zone = ZoneInfo(settings.APP_REPORT_TIMEZONE)
    items: list[DrivingSummarySessionItem] = []
    day_totals: dict[date, list[float]] = defaultdict(lambda: [0, 0, 0.0])
    week_totals: dict[date, list[float]] = defaultdict(lambda: [0, 0, 0.0])
    for session_record in session_records:
        session_end = session_record.ended_at or now
        duration_minutes = max(
            0, int((session_end - session_record.started_at).total_seconds() // 60)
        )
        distance_km = await telemetry_service.resolve_distance_km_in_window(
            db_session,
            session_record.vehicle_id,
            start_time=session_record.started_at,
            end_time=session_end,
        )
        vehicle_summary = await vehicle_service.resolve_vehicle_summary_by_id(
            db_session, session_record.vehicle_id
        )
        start_day = session_record.started_at.astimezone(report_zone).date()
        week_start = start_day - timedelta(days=start_day.weekday())
        for totals, key in ((day_totals, start_day), (week_totals, week_start)):
            totals[key][0] += 1
            totals[key][1] += duration_minutes
            totals[key][2] += distance_km or 0.0
        items.append(
            DrivingSummarySessionItem(
                driving_session_id=session_record.driving_session_id,
                day=start_day,
                license_plate=(
                    vehicle_summary.license_plate if vehicle_summary else None
                ),
                started_at=session_record.started_at,
                ended_at=session_record.ended_at,
                duration_minutes=duration_minutes,
                distance_km=distance_km,
                end_cause=(
                    DrivingSessionEndCause(session_record.end_cause)
                    if session_record.end_cause
                    else None
                ),
            )
        )

    def to_totals(
        totals: dict[date, list[float]],
    ) -> list[DrivingSummaryTotal]:
        """Turn the accumulated figures into response rows, newest first."""
        return [
            DrivingSummaryTotal(
                period_start=period_start,
                session_count=int(figures[0]),
                driving_minutes=int(figures[1]),
                distance_km=round(figures[2], 1),
            )
            for period_start, figures in sorted(totals.items(), reverse=True)
        ]

    return DrivingSummaryResponse(
        from_time=range_start,
        to_time=range_end,
        sessions=items,
        totals_per_day=to_totals(day_totals),
        totals_per_week=to_totals(week_totals),
    )
