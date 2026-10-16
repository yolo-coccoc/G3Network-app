"""Business service and public contract of the drivers domain.

This module holds the business rules for driver profiles and driving
sessions (check-in / check-out). Other domains may only call the public
`resolve_*` functions to obtain internal DTOs, and must never receive the ORM
model or HTTP response schema of the drivers domain. This domain depends on
the `vehicles` domain's public service (VIN to vehicle, VIN for a response)
and on the `identity` domain's public service (the person behind a membership:
name, phone number, status); both edges are one-directional.

Every router-facing function takes the authenticated `Principal`: a profile
is read through its membership's organization, a driving session through the
truck owner's organization recorded on it (DM-24), and both answer "not
found" outside the caller's data reach. A driver who is only a DRIVER acts on
their own profile and sees only their own sessions (DR-11).

A check-in compares the phone with the truck's last T-Box position (the
`telemetry` public service, DR-07); an idle session is ended by
`end_idle_driving_sessions` (run by ``monitoring/``), and every way a session
ends also closes the trip still running in it (DR-12). The driver's own
summary (DR-11) reads the distance from the telemetry odometer. Trips
themselves (plan / start / finish) are in ``trip_service.py``; the lookups
both modules need (the person behind a profile, the truck's recent T-Box
reading) are in ``lookups.py``.

Where the code lives (CV-20, CV-23): this module holds the driver profiles
and what other domains call (the references, the check-in lookups, the
membership-end, role-revoked and ownership-change hooks); the driving
sessions (check-in, check-out, lists, auto-end, the driver's summary) are in
``driving_session_service.py``, which the hooks reach through
``end_session``; who may manage drivers and the licence rule are in
``eligibility.py``.
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.driving_session_service as driving_session_service
import app.domains.drivers.eligibility as driver_eligibility
import app.domains.drivers.lookups as driver_lookups
import app.domains.drivers.repository as driver_repository
import app.domains.identity.service as identity_service
import app.domains.vehicles.service as vehicle_service
from app.domains.drivers.exceptions import (
    DriverConflictError,
    DriverLicenseExpiredError,
    DriverMembershipEndedError,
    DriverMembershipLockedError,
    DriverMembershipNotFoundError,
    DriverNotFoundError,
    DriverRoleMissingError,
)
from app.domains.drivers.models import DriverModel
from app.domains.drivers.schemas import (
    DriverCreateRequest,
    DriverListResponse,
    DriverResponse,
    DriverUpdateRequest,
)
from app.domains.drivers.types import (
    DriverReference,
    DriverStatus,
    DriverWarning,
    DrivingSessionEndCause,
)
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    MembershipEndKind,
    MembershipPersonReference,
    MembershipStatus,
    Principal,
    UserRole,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window

logger = logging.getLogger(__name__)


DRIVER_DELETED_STATUS_REASON = "Driver profile deleted"


MEMBERSHIP_ENDED_STATUS_REASON = "Membership ended"


def to_driver_reference(
    driver_record: DriverModel, person: MembershipPersonReference | None
) -> DriverReference:
    """Convert an ORM record into a minimal DTO for other domains.

    Args:
        driver_record: An active driver record.
        person: The person behind the profile's membership, if it resolved.

    Returns:
        DTO containing the internal ID and the person's full name (empty if
        the membership no longer resolves, which the foreign key prevents).
    """
    return DriverReference(
        driver_id=driver_record.driver_id,
        full_name=person.full_name if person else "",
    )


async def resolve_driver_reference_by_id(
    db_session: AsyncSession,
    driver_id: UUID,
    *,
    organization_id: UUID | None = None,
) -> DriverReference | None:
    """Find an active driver by ID and return its internal DTO.

    Args:
        db_session: Database session owned by the entry boundary.
        driver_id: Internal ID of the driver.
        organization_id: Data scope of an HTTP caller: a profile whose
            membership is in another organization is not found. `None`
            means no restriction.

    Returns:
        `DriverReference` if the driver is found; otherwise `None`.

    Side Effects:
        Performs read-only queries only (the driver, then its person through
        the identity service); does not commit or rollback.
    """
    driver_record = await driver_repository.get_by_id(
        db_session, driver_id, organization_id=organization_id
    )
    if driver_record is None:
        return None
    person = await identity_service.resolve_membership_person_reference(
        db_session, driver_record.membership_id
    )
    return to_driver_reference(driver_record, person)


async def resolve_own_driver_reference(
    db_session: AsyncSession,
    membership_id: UUID,
) -> DriverReference | None:
    """Find the live driver profile of a membership. Cross-domain entry point.

    Args:
        db_session: Database session owned by the entry boundary.
        membership_id: Internal ID of the person's membership.

    Returns:
        `DriverReference` of the membership's live profile; `None` if the
        membership has none (or it was deleted).

    Side Effects:
        Read-only queries; does not commit or rollback.
    """
    driver_record = await driver_repository.find_by_membership_id(
        db_session, membership_id
    )
    if driver_record is None or driver_record.deleted_at is not None:
        return None
    person = await identity_service.resolve_membership_person_reference(
        db_session, membership_id
    )
    return to_driver_reference(driver_record, person)


async def build_driver_response(
    db_session: AsyncSession,
    driver_record: DriverModel,
    warnings: list[DriverWarning] | None = None,
) -> DriverResponse:
    """Build a driver response enriched with the person and the current truck.

    Args:
        db_session: Current database session.
        driver_record: Driver ORM record already queried, created, or
            updated by the caller (all columns populated).
        warnings: Notices to carry in the response (create / licence change).

    Returns:
        Response data with the person's name/phone/organization (identity) and
        `current_vehicle_id`/`current_vehicle_vin` from the driver's open
        driving session (both `None` when the driver is not checked in).

    Side Effects:
        Calls the identity and vehicles domains' public services. Read-only;
        does not commit or rollback.
    """
    person = await identity_service.resolve_membership_person_reference(
        db_session, driver_record.membership_id
    )
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    current_vehicle_id: UUID | None = None
    current_vehicle_vin: str | None = None
    if open_session is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, open_session.vehicle_id
        )
        current_vehicle_id = open_session.vehicle_id
        current_vehicle_vin = vehicle_reference.vin if vehicle_reference else None
    return DriverResponse(
        driver_id=driver_record.driver_id,
        membership_id=driver_record.membership_id,
        organization_id=person.organization_id if person else None,
        full_name=person.full_name if person else None,
        phone_number=person.phone_number if person else None,
        license_number=driver_record.license_number,
        license_class=driver_record.license_class,
        license_expires_on=driver_record.license_expires_on,
        is_license_expired=driver_eligibility.is_license_expired(
            driver_record.license_expires_on
        ),
        days_until_license_expiry=(
            driver_record.license_expires_on - utc_now().date()
        ).days,
        status=driver_record.status,
        status_reason=driver_record.status_reason,
        current_vehicle_id=current_vehicle_id,
        current_vehicle_vin=current_vehicle_vin,
        created_at=driver_record.created_at,
        updated_at=driver_record.updated_at,
        warnings=warnings or [],
    )


async def _license_warnings(
    db_session: AsyncSession, license_number: str, user_id: UUID
) -> list[DriverWarning]:
    """Build the notices about a licence number being recorded (DR-09).

    Args:
        db_session: Current database session.
        license_number: The number being recorded.
        user_id: The person it is recorded for.

    Returns:
        `LICENSE_NUMBER_ON_OTHER_PERSON` when a live profile of someone else
        already has the number (a yes/no only, so one organization learns
        nothing about another's driver); otherwise empty.
    """
    if await driver_repository.exists_license_on_other_person(
        db_session, license_number, user_id
    ):
        return [DriverWarning.LICENSE_NUMBER_ON_OTHER_PERSON]
    return []


async def create_driver(
    db_session: AsyncSession,
    driver_create_request: DriverCreateRequest,
    *,
    principal: Principal,
) -> DriverResponse:
    """Create the driver profile of a membership (DR-09).

    Args:
        db_session: Database session owned by the entry boundary.
        driver_create_request: Request data that has passed Pydantic validation.
        principal: The caller; the membership must be in the caller's data
            reach.

    Returns:
        Response for the newly created driver.

    Raises:
        DriverMembershipNotFoundError: When the membership does not exist or
            is in an organization out of the caller's reach.
        DriverMembershipEndedError: When the person already left the
            organization.
        DriverMembershipLockedError: When the membership is locked (an
            invited, not yet accepted membership is allowed).
        DriverRoleMissingError: When the membership holds no DRIVER role.
        DriverLicenseExpiredError: When the licence is already expired.
        DriverConflictError: When the membership already has a profile
            (deleted ones included: a new membership gets a new profile).
    """
    person = await identity_service.resolve_membership_person_reference(
        db_session, driver_create_request.membership_id
    )
    if person is None or not principal.can_access_organization(person.organization_id):
        raise DriverMembershipNotFoundError(
            f"Membership with id '{driver_create_request.membership_id}' not found"
        )
    if person.left_at is not None:
        raise DriverMembershipEndedError(
            "The person has left the organization; a new membership is needed"
        )
    if person.membership_status == MembershipStatus.LOCKED.value:
        raise DriverMembershipLockedError("The person's membership is locked")
    if not await identity_service.membership_holds_role(
        db_session, driver_create_request.membership_id, UserRole.DRIVER
    ):
        raise DriverRoleMissingError(
            "The membership does not hold the DRIVER role; grant it first"
        )
    if driver_eligibility.is_license_expired(driver_create_request.license_expires_on):
        raise DriverLicenseExpiredError("The licence is already expired")

    existing_driver = await driver_repository.find_by_membership_id(
        db_session, driver_create_request.membership_id
    )
    if existing_driver is not None:
        raise DriverConflictError(
            "This membership already has a driver profile "
            f"('{existing_driver.driver_id}')"
        )

    try:
        driver_record = await driver_repository.insert(
            db_session,
            {
                "membership_id": driver_create_request.membership_id,
                "license_number": driver_create_request.license_number,
                "license_class": driver_create_request.license_class.value,
                "license_expires_on": driver_create_request.license_expires_on,
                "status": driver_create_request.status,
            },
        )
    except IntegrityError as error:
        raise DriverConflictError(
            "This membership already has a driver profile"
        ) from error

    return await build_driver_response(
        db_session,
        driver_record,
        await _license_warnings(
            db_session, driver_create_request.license_number, person.user_id
        ),
    )


async def get_driver(
    db_session: AsyncSession,
    driver_id: UUID,
    *,
    principal: Principal,
    client_context: ClientContext | None,
) -> DriverResponse:
    """Get an active driver by ID inside the caller's data reach.

    The profile holds a person's name, phone and licence, so opening it
    writes one personal-data `VIEW` row (ACC-18) for the profile's
    organization.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        principal: The caller; a profile of another organization is not found
            unless the caller is internal.
        client_context: IP address and user agent of the request, for the
            audit row; `None` when unknown.

    Returns:
        Response for the driver.

    Raises:
        DriverNotFoundError: When the driver does not exist or has been soft-deleted.

    Side Effects:
        Inserts one `access_audit_logs` row (`DRIVER_PROFILE`) through
        `identity.service.record_data_access`; does not commit.
    """
    driver_record = await driver_repository.get_by_id(
        db_session, driver_id, organization_id=principal.data_scope
    )
    if not driver_record:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    driver_response = await build_driver_response(db_session, driver_record)
    # Name, phone and licence of a person: one VIEW row per profile opened.
    await identity_service.record_data_access(
        db_session,
        principal=principal,
        action=AccessAuditAction.VIEW,
        resource_type="DRIVER_PROFILE",
        resource_id=str(driver_id),
        client_context=client_context,
        organization_id=driver_response.organization_id,
    )
    return driver_response


async def list_drivers(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: DriverStatus | None = None,
    search_text: str | None = None,
    vehicle_vin: str | None = None,
    license_expires_within_days: int | None = None,
) -> DriverListResponse:
    """Get a paginated list of active drivers.

    Args:
        db_session: Current database session.
        principal: The caller; only profiles of the caller's organization are
            listed unless the caller is internal.
        page: Page number, starting from 1.
        page_size: Maximum number of drivers per page.
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the licence number, or of
            the person's name or phone number, if any.
        vehicle_vin: Only the driver at the wheel of this vehicle now, if
            given. A VIN that doesn't resolve to an active vehicle yields an
            empty page, not an error: it is a filter, like the others.
        license_expires_within_days: Only profiles whose licence expires within
            this many days (already expired ones included), for the expiry
            reminder (DRV-01).

    Returns:
        Paginated driver list response.

    Side Effects:
        Calls the vehicles domain's public service to resolve
        ``vehicle_vin`` and the identity domain's to find the people matching
        ``search_text``. Read-only; does not commit or rollback.
    """
    page_window = normalize_page_window(page, page_size)
    person_membership_ids: list[UUID] | None = None
    if search_text:
        person_membership_ids = await identity_service.search_membership_ids_by_person(
            db_session, search_text, organization_id=principal.data_scope
        )
    license_expires_by = (
        utc_now().date() + timedelta(days=license_expires_within_days)
        if license_expires_within_days is not None
        else None
    )

    vehicle_id: UUID | None = None
    if vehicle_vin is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
            db_session, vehicle_vin
        )
        if vehicle_reference is None:
            return DriverListResponse(
                items=[],
                total=0,
                page=page_window.page,
                page_size=page_window.page_size,
            )
        vehicle_id = vehicle_reference.vehicle_id

    driver_records = await driver_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=principal.data_scope,
        person_membership_ids=person_membership_ids,
        license_expires_by=license_expires_by,
    )
    total = await driver_repository.count(
        db_session,
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=principal.data_scope,
        person_membership_ids=person_membership_ids,
        license_expires_by=license_expires_by,
    )

    return DriverListResponse(
        items=[
            await build_driver_response(db_session, driver_record)
            for driver_record in driver_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_driver(
    db_session: AsyncSession,
    driver_id: UUID,
    driver_update_request: DriverUpdateRequest,
    *,
    principal: Principal,
) -> DriverResponse:
    """Partially update a driver profile.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        driver_update_request: Field data to update.
        principal: The caller; a profile of another organization is not found
            unless the caller is internal.

    Returns:
        Response for the updated driver.

    Raises:
        DriverNotFoundError: When the driver does not exist or has been soft-deleted.
        DriverLicenseExpiredError: When the new licence expiry is in the past.

    Side Effects:
        The change is written to `driver_history` with the caller as actor; a
        `status_reason` typed in the request becomes the change reason,
        otherwise a fixed text. Setting the profile INACTIVE (deactivation)
        ends the driver's open driving session (`DRIVER_REMOVED`) and the trip
        running in it, in the same transaction.
    """
    driver_record = await driver_repository.get_by_id(
        db_session, driver_id, organization_id=principal.data_scope
    )
    if not driver_record:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    update_values = {
        field_name: value
        for field_name, value in driver_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if "license_class" in update_values:
        update_values["license_class"] = update_values["license_class"].value
    if not update_values:
        return await build_driver_response(db_session, driver_record)
    new_expiry = update_values.get("license_expires_on")
    if new_expiry is not None and driver_eligibility.is_license_expired(new_expiry):
        raise DriverLicenseExpiredError("The licence is already expired")

    if update_values.get("status") == DriverStatus.INACTIVE:
        open_session = await driver_repository.find_open_session_by_driver(
            db_session, driver_id
        )
        if open_session is not None:
            await driving_session_service.end_session(
                db_session,
                open_session,
                ended_at=utc_now(),
                end_cause=DrivingSessionEndCause.DRIVER_REMOVED,
                changed_by=principal.user_id,
            )

    reason = update_values.get("status_reason")
    updated_driver_record = await driver_repository.update_fields(
        db_session,
        driver_id,
        update_values,
        change_reason=reason or driver_repository.DRIVER_EDITED_REASON,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if updated_driver_record is None:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    warnings: list[DriverWarning] = []
    if "license_number" in update_values:
        person = await identity_service.resolve_membership_person_reference(
            db_session, updated_driver_record.membership_id
        )
        if person is not None:
            warnings = await _license_warnings(
                db_session, update_values["license_number"], person.user_id
            )
    return await build_driver_response(db_session, updated_driver_record, warnings)


async def soft_delete_driver(
    db_session: AsyncSession,
    driver_id: UUID,
    *,
    principal: Principal,
    reason: str | None = None,
) -> dict[str, str]:
    """Soft-delete a driver, ending the open driving session first.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        principal: The caller; a profile of another organization is not found
            unless the caller is internal.
        reason: Why the profile leaves the system, stored as its status
            reason; a fixed text when omitted.

    Returns:
        Success deletion message.

    Raises:
        DriverNotFoundError: When the driver does not exist or has been soft-deleted.

    Side Effects:
        Ends the driver's open driving session (`DRIVER_REMOVED`) in the same
        transaction, so a deleted driver never holds a truck against the
        open-session unique index.
    """
    if (
        await driver_repository.get_by_id(
            db_session, driver_id, organization_id=principal.data_scope
        )
        is None
    ):
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_id
    )
    if open_session is not None:
        await driving_session_service.end_session(
            db_session,
            open_session,
            ended_at=utc_now(),
            end_cause=DrivingSessionEndCause.DRIVER_REMOVED,
            changed_by=principal.user_id,
        )

    driver_record = await driver_repository.soft_delete(
        db_session,
        driver_id,
        status_reason=reason or DRIVER_DELETED_STATUS_REASON,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if not driver_record:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    return {"message": "Driver deleted successfully"}


async def is_membership_checked_in_to_vehicle(
    db_session: AsyncSession, membership_id: UUID, vehicle_id: UUID
) -> bool:
    """Tell whether a person is at the wheel of a truck right now. Cross-domain.

    Used by telemetry to let a driver read the live data of the truck they
    are checked in to, whichever organization owns it (mobile scenario 2).

    Args:
        db_session: Session owned by the caller's entry boundary.
        membership_id: The person's membership (their driver profile's owner).
        vehicle_id: Internal ID of the truck.

    Returns:
        True when the membership has a live driver profile with an open
        driving session on that truck.

    Side Effects:
        Read-only queries; does not commit or rollback.
    """
    driver_record = await driver_repository.find_by_membership_id(
        db_session, membership_id
    )
    if driver_record is None or driver_record.deleted_at is not None:
        return False
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    return open_session is not None and open_session.vehicle_id == vehicle_id


async def find_open_vehicle_id_by_membership(
    db_session: AsyncSession, membership_id: UUID
) -> UUID | None:
    """Find the truck a person is checked in to right now. Cross-domain.

    Used by the QR charging start to attribute a session to the truck the
    scanning driver is at the wheel of (CE-13, CHG-07).

    Args:
        db_session: Session owned by the caller's entry boundary.
        membership_id: The person's membership (their driver profile's owner).

    Returns:
        The truck's internal ID, or `None` when the person has no live driver
        profile or no open driving session.

    Side Effects:
        Read-only queries; does not commit or rollback.
    """
    driver_record = await driver_repository.find_by_membership_id(
        db_session, membership_id
    )
    if driver_record is None or driver_record.deleted_at is not None:
        return None
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    return None if open_session is None else open_session.vehicle_id


async def find_checked_in_user_id_by_vehicle(
    db_session: AsyncSession, vehicle_id: UUID
) -> UUID | None:
    """Find the person at the wheel of a truck right now. Cross-domain.

    Used by notification routing: an alert meant for a truck's driver goes to
    the driver checked in to it, even from another organization (NT-07).

    Args:
        db_session: Session owned by the caller's entry boundary.
        vehicle_id: Internal ID of the truck.

    Returns:
        The checked-in driver's user ID, or `None` when nobody is checked in
        (or the profile was deleted).

    Side Effects:
        Read-only queries; does not commit or rollback.
    """
    open_session = await driver_repository.find_open_session_by_vehicle(
        db_session, vehicle_id
    )
    if open_session is None:
        return None
    return await driver_lookups.find_user_id_by_driver_id(
        db_session, open_session.driver_id
    )


async def end_open_session_on_ownership_change(
    db_session: AsyncSession,
    vehicle_id: UUID,
    *,
    ended_at: datetime,
    changed_by: UUID | None,
) -> UUID | None:
    """End the open driving session of a truck that was sold (VH-12).

    Public cross-domain entry point, called by the ownership-transfer
    orchestration (``app/api/vehicle_transfer.py``): the driver belongs to the
    seller's organization, so the session ends with `OWNER_CHANGED`.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_id: Internal ID of the sold truck.
        ended_at: Effective date of the sale.
        changed_by: The staff member who recorded the sale; written as the
            actor of the trip this closes (``trip_history``).

    Returns:
        The ID of the session that was ended, or `None` when nobody was
        checked in to the truck.

    Side Effects:
        Stamps ``ended_at`` and ``end_cause`` and closes the trip still
        running in the session; does not commit or roll back.
    """
    open_session = await driver_repository.find_open_session_by_vehicle(
        db_session, vehicle_id
    )
    if open_session is None:
        return None
    await driving_session_service.end_session(
        db_session,
        open_session,
        # A sale dated before the check-in must not give a negative duration.
        ended_at=max(ended_at, open_session.started_at),
        end_cause=DrivingSessionEndCause.OWNER_CHANGED,
        changed_by=changed_by,
    )
    return open_session.driving_session_id


async def handle_membership_end(
    db_session: AsyncSession,
    *,
    membership_id: UUID,
    kind: MembershipEndKind,
    acting_user_id: UUID,
    reason: str,
) -> None:
    """Close a person's driver profile and driving session when they leave (DR-10).

    Registered with the identity domain's membership-end hook by
    ``app/api/membership_end_hooks.py``, so it runs inside the same
    transaction as the removal, the leave or the lock.

    Rules:
        The open driving session ends `DRIVER_REMOVED` (and its running trip
        closes) for both kinds. An ended membership also sets the profile
        INACTIVE and soft-deletes it with the reason (DM-25); a lock leaves
        the profile alone, because it is reversible and check-in already
        refuses a locked membership.

    Args:
        db_session: Session owned by the entry boundary.
        membership_id: The membership that ended or was locked.
        kind: Whether it ended or was locked.
        acting_user_id: Who did it.
        reason: Why.

    Side Effects:
        Updates the profile (history row with the actor and reason) and the
        session in the caller's transaction; does nothing when the membership
        has no profile.
    """
    driver_record = await driver_repository.find_by_membership_id(
        db_session, membership_id
    )
    if driver_record is None:
        return
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    if open_session is not None:
        await driving_session_service.end_session(
            db_session,
            open_session,
            ended_at=max(utc_now(), open_session.started_at),
            end_cause=DrivingSessionEndCause.DRIVER_REMOVED,
            changed_by=acting_user_id,
        )
    if kind == MembershipEndKind.ENDED and driver_record.deleted_at is None:
        await driver_repository.soft_delete(
            db_session,
            driver_record.driver_id,
            status_reason=reason or MEMBERSHIP_ENDED_STATUS_REASON,
            changed_by=acting_user_id,
        )


async def handle_role_revoked(
    db_session: AsyncSession,
    *,
    membership_id: UUID,
    role: UserRole,
    acting_user_id: UUID,
) -> None:
    """End a person's open driving session when their DRIVER role is revoked.

    Registered with the identity domain's role-revoked hook by
    ``app/api/membership_end_hooks.py``, so it runs inside the same
    transaction as the revocation. Check-in requires the DRIVER role, so a
    session left open would let a person without it keep driving.

    Rules:
        Only the DRIVER role matters; any other role is ignored. The open
        session ends `DRIVER_REMOVED` (and its running trip closes). The
        profile is left alone: it stores facts that outlive the role (a
        former driver's history stays), and check-in refuses it until the
        role is granted again.

    Args:
        db_session: Session owned by the entry boundary.
        membership_id: The membership that lost the role.
        role: The revoked role.
        acting_user_id: Who revoked it; the actor of the trip this closes.

    Side Effects:
        Updates the session and its running trip in the caller's
        transaction; does nothing when the membership has no profile or no
        open session.
    """
    if role != UserRole.DRIVER:
        return
    driver_record = await driver_repository.find_by_membership_id(
        db_session, membership_id
    )
    if driver_record is None:
        return
    open_session = await driver_repository.find_open_session_by_driver(
        db_session, driver_record.driver_id
    )
    if open_session is None:
        return
    await driving_session_service.end_session(
        db_session,
        open_session,
        ended_at=max(utc_now(), open_session.started_at),
        end_cause=DrivingSessionEndCause.DRIVER_REMOVED,
        changed_by=acting_user_id,
    )
