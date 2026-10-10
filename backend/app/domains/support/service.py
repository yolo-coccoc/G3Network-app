"""Business service and public contract of the support domain.

This module holds the business rules for support case tickets (F-I1) and
SOS reports (F-I2). No other domain calls into it yet; one that does must go
through this service and must never receive the ORM model or HTTP response
schema of the support domain. This domain depends on the
`vehicles` and `drivers` domains' public services to resolve a VIN/driver ID
into an internal reference, and on the `notifications` domain's public
service to raise an `SOS_ALERT` for every new SOS (F-I2) - all
one-directional edges, the same shape as the existing `telematics ->
vehicles` and `telemetry -> notifications` edges, and on `identity` for the
caller and the owning organization.

Access (ACC-15): a case belongs to an organization - the owner of the truck it
names, else the organization the caller acts for. An HTTP caller sees the
cases of their organization (internal staff: all); a caller who is only a
DRIVER sees just the cases raised by their own driver profile, which every
case they create carries. A truck of another organization can be named only
by internal staff, by someone with that organization's reach, or by the driver
checked in to it (an SOS must work on a borrowed truck).
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
import app.domains.identity.service as identity_service
import app.domains.notifications.service as notifications_service
import app.domains.support.repository as support_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.identity.types import Principal, UserRole, roles_for
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.support.exceptions import (
    SupportCaseNotFoundError,
    SupportCaseStateError,
    SupportDriverNotFoundError,
    SupportVehicleNotFoundError,
)
from app.domains.support.models import SupportCaseModel
from app.domains.support.schemas import (
    SupportCaseListResponse,
    SupportCaseResponse,
    SupportCaseUpdateRequest,
    SupportSosCreateRequest,
    SupportTicketCreateRequest,
)
from app.domains.support.types import (
    SupportCaseCategory,
    SupportCaseChannel,
    SupportCaseListFilter,
    SupportCaseStatus,
    SupportCaseType,
    is_terminal_status,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location, location_to_coordinates
from app.libs.common.pagination import normalize_page_window

# Roles that read every case of their organization (SUP-01..03); a caller
# holding only DRIVER sees the cases their own driver profile raised.
SUPPORT_STAFF_ROLES = roles_for("SUP-01", "SUP-02", "SUP-03") - {UserRole.DRIVER}


def calculate_is_sla_breached(case_record: SupportCaseModel) -> bool:
    """Compute whether a case's response SLA has been breached.

    Never stored - recomputed at response time. The deadline
    (`response_due_at`) is compared with, in order of preference:
    the first response time; for a case CANCELLED before anyone responded,
    its cancellation time (`closed_at`), so the verdict is frozen once the
    case is gone instead of turning "breached" as time passes; otherwise now.
    The repository's ``sla_breached`` list filter is the SQL form of this
    rule; change both together.

    Args:
        case_record: The support case record to evaluate.

    Returns:
        True if the first response came after the deadline, or if the case
        is (or, for a cancelled case, was at cancellation) past its deadline
        without a first response.
    """
    reference_time = case_record.first_responded_at
    if reference_time is None and case_record.status is SupportCaseStatus.CANCELLED:
        reference_time = case_record.closed_at
    if reference_time is None:
        reference_time = utc_now()
    return reference_time > case_record.response_due_at


async def build_support_case_response(
    db_session: AsyncSession, case_record: SupportCaseModel
) -> SupportCaseResponse:
    """Build a support case response enriched with the driver's name.

    Args:
        db_session: Current database session.
        case_record: Support case ORM record already queried, created, or
            updated by the caller.

    Returns:
        Response data with `driver_name` populated via the drivers
        domain's public service (or `None` if no driver is attached), and
        `vehicle_vin`/`latitude`/`longitude` read directly from the case's
        own stored snapshot columns - no cross-domain lookup needed for
        those, since they were captured at case-creation time.

    Side Effects:
        May call the drivers domain's public service. Read-only; does not
        commit or rollback.
    """
    driver_name: str | None = None
    if case_record.driver_id is not None:
        driver_reference = await driver_service.resolve_driver_reference_by_id(
            db_session, case_record.driver_id
        )
        driver_name = driver_reference.full_name if driver_reference else None

    latitude, longitude = location_to_coordinates(case_record.location)

    return SupportCaseResponse(
        case_id=case_record.case_id,
        organization_id=case_record.organization_id,
        case_type=case_record.case_type,
        category=case_record.category,
        channel=case_record.channel,
        status=case_record.status,
        vehicle_id=case_record.vehicle_id,
        vehicle_vin=case_record.vin,
        driver_id=case_record.driver_id,
        driver_name=driver_name,
        error_code=case_record.error_code,
        latitude=latitude,
        longitude=longitude,
        subject=case_record.subject,
        description=case_record.description,
        sla_response_minutes=case_record.sla_response_minutes,
        response_due_at=case_record.response_due_at,
        first_responded_at=case_record.first_responded_at,
        resolved_at=case_record.resolved_at,
        closed_at=case_record.closed_at,
        is_sla_breached=calculate_is_sla_breached(case_record),
        created_at=case_record.created_at,
        updated_at=case_record.updated_at,
    )


async def _resolve_case_context(
    db_session: AsyncSession,
    *,
    vehicle_vin: str | None,
    driver_id: UUID | None,
    principal: Principal,
) -> tuple[UUID | None, str | None, UUID | None, UUID | None]:
    """Resolve an optional VIN/driver ID into internal IDs, validating both.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_vin: VIN supplied by the caller, if any.
        driver_id: Driver ID supplied by the caller, if any; without one, a
            caller who has a driver profile is recorded as the driver.
        principal: The caller (data scope).

    Returns:
        A tuple of `(vehicle_id, vin, driver_id, vehicle_organization_id)`.
        `vehicle_organization_id` is the resolved vehicle's owner now. `vehicle_id`/`vin` are
        both `None` if no VIN was supplied; `driver_id` is the one supplied
        (only its existence in the caller's reach is checked) or the
        caller's own profile.

    Raises:
        SupportVehicleNotFoundError: When a VIN was supplied but doesn't
            resolve to a vehicle.
        SupportDriverNotFoundError: When a driver ID was supplied but
            doesn't resolve to a driver.
    """
    resolved_vehicle_id: UUID | None = None
    resolved_vin: str | None = None
    vehicle_organization_id: UUID | None = None
    if vehicle_vin is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
            db_session, vehicle_vin
        )
        if vehicle_reference is None or not (
            principal.can_access_organization(vehicle_reference.organization_id)
            or await driver_service.is_membership_checked_in_to_vehicle(
                db_session, principal.membership_id, vehicle_reference.vehicle_id
            )
        ):
            raise SupportVehicleNotFoundError(
                f"Vehicle with VIN '{vehicle_vin}' not found"
            )
        resolved_vehicle_id = vehicle_reference.vehicle_id
        resolved_vin = vehicle_reference.vin
        vehicle_organization_id = vehicle_reference.organization_id

    if driver_id is not None:
        driver_reference = await driver_service.resolve_driver_reference_by_id(
            db_session, driver_id, organization_id=principal.data_scope
        )
        if driver_reference is None:
            raise SupportDriverNotFoundError(f"Driver with id '{driver_id}' not found")
    else:
        own_driver = await driver_service.resolve_own_driver_reference(
            db_session, principal.membership_id
        )
        driver_id = own_driver.driver_id if own_driver is not None else None

    return resolved_vehicle_id, resolved_vin, driver_id, vehicle_organization_id


async def _insert_case(
    db_session: AsyncSession,
    case_create_request: SupportTicketCreateRequest | SupportSosCreateRequest,
    *,
    case_type: SupportCaseType,
    channel: SupportCaseChannel,
    subject: str,
    sla_response_minutes: int,
    principal: Principal,
) -> SupportCaseResponse:
    """Validate a new case's context, insert it as OPEN and build its response.

    The shared body of `create_support_ticket` and `create_support_sos`:
    the two differ only in the values passed as keyword arguments.

    Args:
        db_session: Database session owned by the entry boundary.
        case_create_request: The validated ticket or SOS request; supplies
            the vehicle/driver context, category, description, error code
            and location.
        case_type: Whether the case is a ticket or an SOS.
        channel: Where the case originated.
        subject: Short subject line to store.
        sla_response_minutes: Response SLA for this case type; the deadline
            `response_due_at` is now plus this many minutes.
        principal: The caller; the case belongs to the named truck's owner,
            else to the caller's organization (internal staff may name one).

    Returns:
        Response for the newly created case.

    Raises:
        SupportVehicleNotFoundError: When a VIN was supplied but doesn't
            resolve to a vehicle.
        SupportDriverNotFoundError: When a driver ID was supplied but
            doesn't resolve to a driver.
        OrganizationNotFoundError: When internal staff name an organization
            that does not exist.

    Side Effects:
        Inserts one `support_cases` row (flushed, not committed).
    """
    vehicle_id, vin, driver_id, vehicle_organization_id = await _resolve_case_context(
        db_session,
        vehicle_vin=case_create_request.vehicle_vin,
        driver_id=case_create_request.driver_id,
        principal=principal,
    )
    # The vehicle's owner wins over a caller-supplied organization: the case
    # belongs to whoever owns the truck now. Without a truck it belongs to the
    # organization the caller acts for.
    organization_id = vehicle_organization_id or (
        await identity_service.resolve_organization_for_new_record(
            db_session, principal, case_create_request.organization_id
        )
    )

    created_at = utc_now()
    case_record = await support_repository.insert(
        db_session,
        {
            "organization_id": organization_id,
            "case_type": case_type,
            "category": case_create_request.category,
            "channel": channel,
            "status": SupportCaseStatus.OPEN,
            "vehicle_id": vehicle_id,
            "driver_id": driver_id,
            "vin": vin,
            "error_code": case_create_request.error_code,
            "location": coordinates_to_location(
                case_create_request.latitude,
                case_create_request.longitude,
            ),
            "subject": subject,
            "description": case_create_request.description,
            "sla_response_minutes": sla_response_minutes,
            "response_due_at": created_at + timedelta(minutes=sla_response_minutes),
        },
    )

    return await build_support_case_response(db_session, case_record)


async def create_support_ticket(
    db_session: AsyncSession,
    support_ticket_create_request: SupportTicketCreateRequest,
    *,
    principal: Principal,
) -> SupportCaseResponse:
    """Create a new support ticket (F-I1) with the ticket response SLA.

    Args:
        db_session: Database session owned by the entry boundary.
        support_ticket_create_request: Request data that has passed
            Pydantic validation.
        principal: The caller.

    Returns:
        Response for the newly created ticket.

    Raises:
        SupportVehicleNotFoundError: When a VIN was supplied but doesn't
            resolve to a vehicle.
        SupportDriverNotFoundError: When a driver ID was supplied but
            doesn't resolve to a driver.
    """
    return await _insert_case(
        db_session,
        support_ticket_create_request,
        case_type=SupportCaseType.TICKET,
        channel=support_ticket_create_request.channel,
        subject=support_ticket_create_request.subject,
        sla_response_minutes=settings.SUPPORT_TICKET_RESPONSE_SLA_MINUTES,
        principal=principal,
    )


def build_sos_alert_payload(
    support_case_response: SupportCaseResponse,
) -> dict[str, object]:
    """Build the ``SOS_ALERT`` notification payload for a new SOS case.

    Pure mapping only, no I/O. Every key is always present (``None`` when
    the case has no such value) so a consumer reads one fixed shape; values
    are JSON-ready (UUIDs and times as strings).

    Args:
        support_case_response: The SOS case just created.

    Returns:
        ``case_id``, ``vehicle_id``, ``vehicle_vin``, ``driver_id``,
        ``channel``, ``category``, ``latitude``, ``longitude``,
        ``error_code`` and ``response_due_at``.
    """
    return {
        "case_id": str(support_case_response.case_id),
        "vehicle_id": (
            str(support_case_response.vehicle_id)
            if support_case_response.vehicle_id is not None
            else None
        ),
        "vehicle_vin": support_case_response.vehicle_vin,
        "driver_id": (
            str(support_case_response.driver_id)
            if support_case_response.driver_id is not None
            else None
        ),
        "channel": support_case_response.channel.value,
        "category": support_case_response.category.value,
        "latitude": support_case_response.latitude,
        "longitude": support_case_response.longitude,
        "error_code": support_case_response.error_code,
        "response_due_at": support_case_response.response_due_at.isoformat(),
    }


async def create_support_sos(
    db_session: AsyncSession,
    support_sos_create_request: SupportSosCreateRequest,
    *,
    principal: Principal,
) -> SupportCaseResponse:
    """Create a new SOS report (F-I2) and raise its ``SOS_ALERT``.

    Args:
        db_session: Database session owned by the entry boundary.
        support_sos_create_request: Request data that has passed Pydantic
            validation (an ``IN_APP`` SOS always carries a location).
        principal: The caller.

    Returns:
        Response for the newly created SOS case.

    Raises:
        SupportVehicleNotFoundError: When a VIN was supplied but doesn't
            resolve to a vehicle.
        SupportDriverNotFoundError: When a driver ID was supplied but
            doesn't resolve to a driver.

    Side Effects:
        Inserts the case with the request's channel (D12: ``IN_APP`` by
        default, ``HOTLINE``/``ZALO`` when an operator logs a call) and the
        SOS response SLA, whatever the channel; the subject is auto-filled
        ("SOS - <category>") since an SOS has no free-text subject. Then
        writes one CRITICAL ``SOS_ALERT`` notification through the
        notifications domain's public service, in the same transaction, so
        the case and its alert are committed or rolled back together. The
        backend's own job ends there - the callback itself (F-I2's <=5
        minute SLA) is a human action taken after this call returns.
    """
    support_case_response = await _insert_case(
        db_session,
        support_sos_create_request,
        case_type=SupportCaseType.SOS,
        channel=support_sos_create_request.channel,
        subject=f"SOS - {support_sos_create_request.category.value}",
        sla_response_minutes=settings.SUPPORT_SOS_RESPONSE_SLA_MINUTES,
        principal=principal,
    )

    vehicle_label = support_case_response.vehicle_vin or "unknown vehicle"
    # _insert_case always resolves an organization, so it is set here.
    assert support_case_response.organization_id is not None
    await notifications_service.create_notification(
        db_session,
        organization_id=support_case_response.organization_id,
        notification_type=NotificationType.SOS_ALERT,
        severity=NotificationSeverity.CRITICAL,
        vehicle_id=support_case_response.vehicle_id,
        subject_type="SUPPORT_CASE",
        subject_id=support_case_response.case_id,
        title=f"SOS ({support_case_response.category.value}) - {vehicle_label}",
        body=(
            f"SOS received via {support_case_response.channel.value}; respond "
            f"within {support_case_response.sla_response_minutes} minutes."
        ),
        payload=build_sos_alert_payload(support_case_response),
    )
    return support_case_response


def _driver_scope(principal: Principal, own_driver_id: UUID | None) -> UUID | None:
    """Tell whose cases a caller may read inside their organization.

    Args:
        principal: The caller.
        own_driver_id: The caller's own driver profile, if they have one.

    Returns:
        `None` for staff roles (every case of the organization); the
        caller's own driver profile for a DRIVER-only caller (a nil UUID when
        they have none, so nothing matches).
    """
    if principal.has_any_role(*SUPPORT_STAFF_ROLES):
        return None
    return own_driver_id if own_driver_id is not None else UUID(int=0)


async def _own_driver_id(db_session: AsyncSession, principal: Principal) -> UUID | None:
    """Look up the caller's own live driver profile.

    Args:
        db_session: Current database session.
        principal: The caller.

    Returns:
        The profile ID, or `None` when the caller is not a driver (or the
        caller holds a staff role, which needs no lookup).
    """
    if principal.has_any_role(*SUPPORT_STAFF_ROLES):
        return None
    own_driver = await driver_service.resolve_own_driver_reference(
        db_session, principal.membership_id
    )
    return own_driver.driver_id if own_driver is not None else None


async def _get_case_in_reach(
    db_session: AsyncSession, case_id: UUID, principal: Principal
) -> SupportCaseModel:
    """Load a live case the caller may act on, or raise "not found".

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.
        principal: The caller (data scope).

    Returns:
        The case record.

    Raises:
        SupportCaseNotFoundError: The case does not exist, was deleted or is
            out of the caller's reach.
    """
    case_record = await support_repository.get_by_id(
        db_session,
        case_id,
        organization_id=principal.data_scope,
        driver_id=_driver_scope(principal, await _own_driver_id(db_session, principal)),
    )
    if not case_record:
        raise SupportCaseNotFoundError(f"Support case with id '{case_id}' not found")
    return case_record


async def get_support_case(
    db_session: AsyncSession,
    case_id: UUID,
    *,
    principal: Principal,
) -> SupportCaseResponse:
    """Get an active support case by ID inside the caller's data reach.

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.
        principal: The caller; a case of another organization (or, for a
            DRIVER-only caller, of another driver) is not found.

    Returns:
        Response for the support case.

    Raises:
        SupportCaseNotFoundError: When the case does not exist or has been
            soft-deleted.
    """
    case_record = await _get_case_in_reach(db_session, case_id, principal)
    return await build_support_case_response(db_session, case_record)


async def list_support_cases(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: SupportCaseStatus | None = None,
    case_type_filter: SupportCaseType | None = None,
    vehicle_id_filter: UUID | None = None,
    category_filter: SupportCaseCategory | None = None,
    channel_filter: SupportCaseChannel | None = None,
    driver_id_filter: UUID | None = None,
    awaiting_response_filter: bool | None = None,
    sla_breached_filter: bool | None = None,
) -> SupportCaseListResponse:
    """Get a paginated list of active support cases, newest first.

    Args:
        db_session: Current database session.
        principal: The caller; only cases of their organization are listed
            (internal staff: all), and a DRIVER-only caller sees just the
            cases their own profile raised.
        page: Page number, starting from 1.
        page_size: Maximum number of cases per page.
        status_filter: Status filter, if any.
        case_type_filter: Case type filter, if any.
        vehicle_id_filter: Vehicle ID filter, if any.
        category_filter: Category filter, if any.
        channel_filter: Channel filter, if any.
        driver_id_filter: Driver ID filter, if any.
        awaiting_response_filter: ``True`` keeps only cases with no first
            response that are not CLOSED/CANCELLED; ``False`` the others.
        sla_breached_filter: ``True`` keeps only cases whose
            ``is_sla_breached`` is true (no response past the deadline, or a
            late response); ``False`` the others.

    Returns:
        Paginated support case list response.
    """
    page_window = normalize_page_window(page, page_size)
    driver_scope = _driver_scope(principal, await _own_driver_id(db_session, principal))
    case_list_filter = SupportCaseListFilter(
        status=status_filter,
        case_type=case_type_filter,
        vehicle_id=vehicle_id_filter,
        category=category_filter,
        channel=channel_filter,
        driver_id=driver_scope or driver_id_filter,
        organization_id=principal.data_scope,
        is_awaiting_response=awaiting_response_filter,
        is_sla_breached=sla_breached_filter,
    )
    # One instant for the whole request, so the page and its total agree.
    evaluated_at = utc_now()

    case_records = await support_repository.list_all(
        db_session,
        case_list_filter,
        offset=page_window.offset,
        limit=page_window.page_size,
        evaluated_at=evaluated_at,
    )
    total = await support_repository.count(
        db_session, case_list_filter, evaluated_at=evaluated_at
    )

    return SupportCaseListResponse(
        items=[
            await build_support_case_response(db_session, case_record)
            for case_record in case_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


def _apply_status_timestamps(
    update_values: dict[str, object],
    case_record: SupportCaseModel,
    new_status: SupportCaseStatus,
) -> None:
    """Set the timestamp columns implied by a status transition, in place.

    `first_responded_at` and `resolved_at` record when a step *first*
    happened, so they are only stamped when the stored case doesn't have
    them yet - a later transition (e.g. ACKNOWLEDGED -> RESOLVED) never
    moves an earlier timestamp, which the SLA calculation depends on.

    Args:
        update_values: The field values about to be persisted; mutated
            in place to add whichever timestamps the new status implies.
        case_record: The case as currently stored, before this update.
        new_status: The status the case is transitioning to.

    Side Effects:
        A later status in the happy path (RESOLVED, CLOSED) also stamps
        any earlier timestamp the case hasn't recorded yet, since reaching
        that status implies every earlier step already happened even if
        this update skipped straight past ACKNOWLEDGED/RESOLVED. CANCELLED
        stamps only `closed_at`: cancelling is not a response.
    """
    transition_at = utc_now()
    is_response = new_status not in (
        SupportCaseStatus.OPEN,
        SupportCaseStatus.CANCELLED,
    )
    if is_response and case_record.first_responded_at is None:
        update_values.setdefault("first_responded_at", transition_at)
    if (
        new_status in (SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED)
        and case_record.resolved_at is None
    ):
        update_values.setdefault("resolved_at", transition_at)
    if is_terminal_status(new_status):
        update_values["closed_at"] = transition_at


async def update_support_case(
    db_session: AsyncSession,
    case_id: UUID,
    support_case_update_request: SupportCaseUpdateRequest,
    *,
    principal: Principal,
) -> SupportCaseResponse:
    """Partially update a support case.

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.
        support_case_update_request: Field data to update.
        principal: The caller (data scope).

    Returns:
        Response for the updated support case.

    Raises:
        SupportCaseNotFoundError: When the case does not exist or has been
            soft-deleted.
        SupportCaseStateError: When the case is already CLOSED or
            CANCELLED - a terminal case accepts no further update.
    """
    case_record = await _get_case_in_reach(db_session, case_id, principal)

    if is_terminal_status(case_record.status):
        raise SupportCaseStateError(
            f"Support case with id '{case_id}' is already "
            f"{case_record.status.value} and cannot be updated"
        )

    update_values: dict[str, object] = {
        field_name: value
        for field_name, value in support_case_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if not update_values:
        return await build_support_case_response(db_session, case_record)

    new_status = update_values.get("status")
    if isinstance(new_status, SupportCaseStatus):
        _apply_status_timestamps(update_values, case_record, new_status)

    updated_case_record = await support_repository.update_fields(
        db_session, case_id, update_values
    )
    if updated_case_record is None:
        raise SupportCaseNotFoundError(f"Support case with id '{case_id}' not found")

    return await build_support_case_response(db_session, updated_case_record)


async def soft_delete_support_case(
    db_session: AsyncSession,
    case_id: UUID,
    *,
    principal: Principal,
) -> dict[str, str]:
    """Soft-delete a support case.

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.
        principal: The caller (data scope).

    Returns:
        Success deletion message.

    Raises:
        SupportCaseNotFoundError: When the case does not exist or has been
            soft-deleted.
    """
    await _get_case_in_reach(db_session, case_id, principal)
    case_record = await support_repository.soft_delete(db_session, case_id)
    if not case_record:
        raise SupportCaseNotFoundError(f"Support case with id '{case_id}' not found")

    return {"message": "Support case deleted successfully"}
