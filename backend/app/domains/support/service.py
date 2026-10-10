"""Business service and public contract of the support domain.

This module holds the business rules for support case tickets (F-I1) and
SOS reports (F-I2). No other domain calls into it yet; one that does must go
through this service and must never receive the ORM model or HTTP response
schema of the support domain. This domain depends on the
`vehicles` and `drivers` domains' public services to resolve a VIN/driver ID
into an internal reference, and on the `notifications` domain's public
service to raise an `SOS_ALERT` for every new SOS (F-I2) - all
one-directional edges, the same shape as the existing `telematics ->
vehicles` and `telemetry -> notifications` edges.
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
import app.domains.notifications.service as notifications_service
import app.domains.support.repository as support_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.support.exceptions import (
    SupportCaseNotFoundError,
    SupportCaseStateError,
    SupportDriverNotFoundError,
    SupportOrganizationRequiredError,
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
) -> tuple[UUID | None, str | None, UUID | None, UUID | None]:
    """Resolve an optional VIN/driver ID into internal IDs, validating both.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_vin: VIN supplied by the caller, if any.
        driver_id: Driver ID supplied by the caller, if any.

    Returns:
        A tuple of `(vehicle_id, vin, driver_id, vehicle_organization_id)`.
        `vehicle_organization_id` is the resolved vehicle's owner now. `vehicle_id`/`vin` are
        both `None` if no VIN was supplied; `driver_id` is echoed back
        unchanged (only its existence is checked).

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
        if vehicle_reference is None:
            raise SupportVehicleNotFoundError(
                f"Vehicle with VIN '{vehicle_vin}' not found"
            )
        resolved_vehicle_id = vehicle_reference.vehicle_id
        resolved_vin = vehicle_reference.vin
        vehicle_organization_id = vehicle_reference.organization_id

    if driver_id is not None:
        driver_reference = await driver_service.resolve_driver_reference_by_id(
            db_session, driver_id
        )
        if driver_reference is None:
            raise SupportDriverNotFoundError(f"Driver with id '{driver_id}' not found")

    return resolved_vehicle_id, resolved_vin, driver_id, vehicle_organization_id


async def _insert_case(
    db_session: AsyncSession,
    case_create_request: SupportTicketCreateRequest | SupportSosCreateRequest,
    *,
    case_type: SupportCaseType,
    channel: SupportCaseChannel,
    subject: str,
    sla_response_minutes: int,
    is_organization_required: bool = False,
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
        is_organization_required: Whether the case must end up with an
            organization (an SOS, whose alert belongs to one).

    Returns:
        Response for the newly created case.

    Raises:
        SupportVehicleNotFoundError: When a VIN was supplied but doesn't
            resolve to a vehicle.
        SupportDriverNotFoundError: When a driver ID was supplied but
            doesn't resolve to a driver.
        SupportOrganizationRequiredError: When an organization is required
            and neither the request nor the vehicle gives one.

    Side Effects:
        Inserts one `support_cases` row (flushed, not committed).
    """
    vehicle_id, vin, driver_id, vehicle_organization_id = await _resolve_case_context(
        db_session,
        vehicle_vin=case_create_request.vehicle_vin,
        driver_id=case_create_request.driver_id,
    )
    # The vehicle's owner wins over a caller-supplied organization: the case
    # belongs to whoever owns the truck now.
    organization_id = vehicle_organization_id or case_create_request.organization_id
    if organization_id is None and is_organization_required:
        raise SupportOrganizationRequiredError(
            "An SOS needs a known vehicle or an organization_id"
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
) -> SupportCaseResponse:
    """Create a new support ticket (F-I1) with the ticket response SLA.

    Args:
        db_session: Database session owned by the entry boundary.
        support_ticket_create_request: Request data that has passed
            Pydantic validation.

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
) -> SupportCaseResponse:
    """Create a new SOS report (F-I2) and raise its ``SOS_ALERT``.

    Args:
        db_session: Database session owned by the entry boundary.
        support_sos_create_request: Request data that has passed Pydantic
            validation (an ``IN_APP`` SOS always carries a location).

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
        is_organization_required=True,
    )

    vehicle_label = support_case_response.vehicle_vin or "unknown vehicle"
    # _insert_case refused an SOS without an organization, so it is set here.
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


async def get_support_case(
    db_session: AsyncSession,
    case_id: UUID,
) -> SupportCaseResponse:
    """Get an active support case by ID.

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.

    Returns:
        Response for the support case.

    Raises:
        SupportCaseNotFoundError: When the case does not exist or has been
            soft-deleted.
    """
    case_record = await support_repository.get_by_id(db_session, case_id)
    if not case_record:
        raise SupportCaseNotFoundError(f"Support case with id '{case_id}' not found")

    return await build_support_case_response(db_session, case_record)


async def list_support_cases(
    db_session: AsyncSession,
    *,
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
    case_list_filter = SupportCaseListFilter(
        status=status_filter,
        case_type=case_type_filter,
        vehicle_id=vehicle_id_filter,
        category=category_filter,
        channel=channel_filter,
        driver_id=driver_id_filter,
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
) -> SupportCaseResponse:
    """Partially update a support case.

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.
        support_case_update_request: Field data to update.

    Returns:
        Response for the updated support case.

    Raises:
        SupportCaseNotFoundError: When the case does not exist or has been
            soft-deleted.
        SupportCaseStateError: When the case is already CLOSED or
            CANCELLED - a terminal case accepts no further update.
    """
    case_record = await support_repository.get_by_id(db_session, case_id)
    if not case_record:
        raise SupportCaseNotFoundError(f"Support case with id '{case_id}' not found")

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
) -> dict[str, str]:
    """Soft-delete a support case.

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.

    Returns:
        Success deletion message.

    Raises:
        SupportCaseNotFoundError: When the case does not exist or has been
            soft-deleted.
    """
    case_record = await support_repository.soft_delete(db_session, case_id)
    if not case_record:
        raise SupportCaseNotFoundError(f"Support case with id '{case_id}' not found")

    return {"message": "Support case deleted successfully"}
