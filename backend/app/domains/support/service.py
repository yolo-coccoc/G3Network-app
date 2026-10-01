"""Business service and public contract of the support domain.

This module holds the business rules for support case tickets (F-I1) and
SOS reports (F-I2). No other domain calls into it yet; one that does must go
through this service and must never receive the ORM model or HTTP response
schema of the support domain. This domain depends on the
`vehicles` and `drivers` domains' public services to resolve a VIN/driver ID
into an internal reference - both one-directional edges, the same shape as
the existing `telematics -> vehicles` edge.
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
import app.domains.support.repository as support_repository
import app.domains.vehicles.service as vehicle_service
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
    SupportCaseChannel,
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
) -> tuple[UUID | None, str | None, UUID | None]:
    """Resolve an optional VIN/driver ID into internal IDs, validating both.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_vin: VIN supplied by the caller, if any.
        driver_id: Driver ID supplied by the caller, if any.

    Returns:
        A tuple of `(vehicle_id, vin, driver_id)`. `vehicle_id`/`vin` are
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

    if driver_id is not None:
        driver_reference = await driver_service.resolve_driver_reference_by_id(
            db_session, driver_id
        )
        if driver_reference is None:
            raise SupportDriverNotFoundError(f"Driver with id '{driver_id}' not found")

    return resolved_vehicle_id, resolved_vin, driver_id


async def _insert_case(
    db_session: AsyncSession,
    case_create_request: SupportTicketCreateRequest | SupportSosCreateRequest,
    *,
    case_type: SupportCaseType,
    channel: SupportCaseChannel,
    subject: str,
    sla_response_minutes: int,
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

    Returns:
        Response for the newly created case.

    Raises:
        SupportVehicleNotFoundError: When a VIN was supplied but doesn't
            resolve to a vehicle.
        SupportDriverNotFoundError: When a driver ID was supplied but
            doesn't resolve to a driver.

    Side Effects:
        Inserts one `support_cases` row (flushed, not committed).
    """
    vehicle_id, vin, driver_id = await _resolve_case_context(
        db_session,
        vehicle_vin=case_create_request.vehicle_vin,
        driver_id=case_create_request.driver_id,
    )

    created_at = utc_now()
    case_record = await support_repository.insert(
        db_session,
        {
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


async def create_support_sos(
    db_session: AsyncSession,
    support_sos_create_request: SupportSosCreateRequest,
) -> SupportCaseResponse:
    """Create a new SOS report (F-I2) with the SOS response SLA.

    Args:
        db_session: Database session owned by the entry boundary.
        support_sos_create_request: Request data that has passed Pydantic
            validation.

    Returns:
        Response for the newly created SOS case.

    Raises:
        SupportVehicleNotFoundError: When a VIN was supplied but doesn't
            resolve to a vehicle.
        SupportDriverNotFoundError: When a driver ID was supplied but
            doesn't resolve to a driver.

    Side Effects:
        The channel is always IN_APP and the subject is auto-filled
        ("SOS - <category>") since an SOS is a button tap with no free-text
        subject. The backend's own job ends at recording the case - the
        callback itself (F-I2's <=5 minute SLA) is a human action taken
        after this call returns.
    """
    return await _insert_case(
        db_session,
        support_sos_create_request,
        case_type=SupportCaseType.SOS,
        channel=SupportCaseChannel.IN_APP,
        subject=f"SOS - {support_sos_create_request.category.value}",
        sla_response_minutes=settings.SUPPORT_SOS_RESPONSE_SLA_MINUTES,
    )


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
) -> SupportCaseListResponse:
    """Get a paginated list of active support cases, newest first.

    Args:
        db_session: Current database session.
        page: Page number, starting from 1.
        page_size: Maximum number of cases per page.
        status_filter: Status filter, if any.
        case_type_filter: Case type filter, if any.
        vehicle_id_filter: Vehicle ID filter, if any.

    Returns:
        Paginated support case list response.
    """
    page_window = normalize_page_window(page, page_size)

    case_records = await support_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        status_filter=status_filter,
        case_type_filter=case_type_filter,
        vehicle_id_filter=vehicle_id_filter,
    )
    total = await support_repository.count(
        db_session,
        status_filter=status_filter,
        case_type_filter=case_type_filter,
        vehicle_id_filter=vehicle_id_filter,
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
