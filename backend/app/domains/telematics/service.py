"""Business service for Telematic device CRUD, health, status reports, config push and lookup.

Other domains, especially ``telemetry``, must only use the public functions in
this module to resolve device-vehicle mappings; they must not access
``telematics``' repository or model directly. The vehicle a device is
assigned to is resolved through the ``vehicles`` public service (by VIN on
input, by ID for the response); a device's read-time health (F-J1) through
the ``telemetry`` public service; a fleet's vehicles for the fleet-wide
config push (F-J2) through the ``fleet`` public service. No function here
commits or rolls back - the caller's entry boundary owns the transaction.

Device health (DEV-04) is always computed when read: the silence flag from the
vehicle's newest telemetry, the rest from the device's newest status report
(TX-11). The status reports themselves are written by ``record_status_report``
(DEV-03, the status-report ingestion process).

Where the code lives (CV-20, CV-23): this module holds the device records
(create, read, list, update, soft-delete) and the lookups other domains call;
status reports and device health are in ``health_service.py``, the
configuration push in ``config_push_service.py``, the device's live view
(mount and health) in ``device_view.py`` and the reach loader in
``access.py``.
"""

import logging
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.service as identity_service
import app.domains.telematics.access as telematic_access
import app.domains.telematics.device_view as telematic_device_view
import app.domains.telematics.repository as telematics_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.identity.types import Principal
from app.domains.telematics.exceptions import (
    TelematicConflictError,
    TelematicDateInvalidError,
    TelematicVehicleNotFoundError,
)
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import (
    TelematicCreateRequest,
    TelematicListResponse,
    TelematicResponse,
    TelematicUpdateRequest,
)
from app.domains.telematics.types import (
    TelematicMountedDevice,
    TelematicStatus,
    TelematicVehicleMapping,
)
from app.libs.common.clock import CLIENT_CLOCK_SKEW, utc_now
from app.libs.common.pagination import normalize_page_window

logger = logging.getLogger(__name__)


# Fixed history reasons of routine actions; the acting user is the caller.
TELEMATIC_EDITED_REASON = "Device details edited"


TELEMATIC_DELETED_REASON = "Device deleted"


async def resolve_mapping_by_serial(
    db: AsyncSession,
    serial: str,
) -> TelematicVehicleMapping | None:
    """Resolve a telematic serial into a device ID and vehicle ID.

    Rule (D11 of the happy-path planner):
        A device whose assigned vehicle is soft-deleted is ignored, exactly
        like an unassigned one - the device row keeps its ``vehicle_id``
        after the vehicle is deleted (the vehicles domain can't unassign it
        without a ``vehicles -> telematics`` cycle), so liveness of the
        vehicle is checked here through the vehicles public service.

    Args:
        db: Database session owned by the entry boundary.
        serial: Physical serial received from a telemetry message.

    Returns:
        A ``TelematicVehicleMapping`` (with the vehicle's current owner) if the
        mapping is valid; ``None`` if
        the device does not exist, has been soft-deleted, has not been
        assigned a vehicle, or is assigned to a soft-deleted vehicle.

    Side Effects:
        Read-only: the mapping query, then one vehicle lookup through the
        vehicles service when a mapping exists. Does not commit or roll
        back.
    """
    mounted_ids = await telematics_repository.find_mounted_vehicle_id_by_serial(
        db, serial
    )
    if mounted_ids is None:
        return None
    telematic_id, vehicle_id = mounted_ids
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db, vehicle_id
    )
    if vehicle_reference is None:
        return None
    return TelematicVehicleMapping(
        telematic_id=telematic_id,
        vehicle_id=vehicle_id,
        organization_id=vehicle_reference.organization_id,
    )


async def resolve_serial_by_id(
    db: AsyncSession,
    telematic_id: UUID,
) -> str | None:
    """Resolve a device's serial from its internal ID (for telemetry responses).

    Args:
        db: Database session owned by the entry boundary.
        telematic_id: Internal ID of the device.

    Returns:
        The serial printed on the device, or ``None`` if there is no such
        device. A soft-deleted device still resolves, since its past
        telemetry names it.

    Side Effects:
        One read-only query.
    """
    return await telematics_repository.find_serial_by_id(db, telematic_id)


async def _resolve_vehicle_id_by_vin(
    db_session: AsyncSession,
    vehicle_vin: str,
    principal: Principal,
    *,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> UUID:
    """Resolve the VIN sent on a device create/update into a live vehicle ID.

    Args:
        db_session: Current database session.
        vehicle_vin: VIN of the vehicle the device should be assigned to.
        principal: The caller; a vehicle of an organization out of the
            caller's reach does not exist for them.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a truck outside them does not exist for the caller. `None`
            means no limit.

    Returns:
        Internal ID of the live vehicle carrying that VIN.

    Raises:
        TelematicVehicleNotFoundError: No live (non-soft-deleted) vehicle
            has that VIN, it belongs to an organization out of reach
            (D10, deferred.md item 83), or it is outside the caller's fleet
            limit.

    Side Effects:
        One read-only query through the vehicles public service.
    """
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
        db_session, vehicle_vin
    )
    if (
        vehicle_reference is None
        or not principal.can_access_organization(vehicle_reference.organization_id)
        or (
            visible_vehicle_ids is not None
            and vehicle_reference.vehicle_id not in visible_vehicle_ids
        )
    ):
        raise TelematicVehicleNotFoundError(
            f"Vehicle with VIN '{vehicle_vin}' not found"
        )
    return vehicle_reference.vehicle_id


async def build_telematic_response(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
) -> TelematicResponse:
    """Build a telematic response with the vehicle's VIN and the device health.

    The VIN comes from the vehicles domain's public service and the health
    (F-J1) from the telemetry domain's, since this domain must not access
    either domain's repository or ORM model directly.

    Args:
        db_session: Current database session.
        telematic_record: Device record, flushed or loaded in this session.

    Returns:
        The HTTP response for the device. ``vehicle_vin`` is ``None`` and
        the health fields are null/``False`` when no vehicle is assigned
        or the assigned vehicle is soft-deleted. Firmware and the interval in
        use come from the newest status report (TX-11).

    Side Effects:
        Read-only, see ``_load_device_view``.
    """
    device_view = await telematic_device_view.load_device_view(
        db_session, telematic_record
    )
    device_health = device_view.device_health
    latest_status_report = device_view.latest_status_report
    return TelematicResponse(
        telematic_id=telematic_record.telematic_id,
        telematic_serial=telematic_record.telematic_serial,
        imei=telematic_record.imei,
        organization_id=telematic_record.organization_id,
        acquired_at=telematic_record.acquired_at,
        vehicle_id=telematic_record.vehicle_id,
        vehicle_vin=device_view.vehicle_vin,
        installed_at=telematic_record.installed_at,
        status=telematic_record.status,
        status_reason=telematic_record.status_reason,
        firmware_version=(
            latest_status_report.firmware_version if latest_status_report else None
        ),
        telemetry_interval_seconds=(
            latest_status_report.telemetry_interval_seconds
            if latest_status_report
            else None
        ),
        last_seen_at=device_health.last_seen_at,
        is_online=device_health.is_online,
        is_silent=device_health.is_silent,
        last_signal_strength_dbm=device_health.last_signal_strength_dbm,
        created_at=telematic_record.created_at,
        updated_at=telematic_record.updated_at,
    )


async def create_telematic(
    db_session: AsyncSession,
    telematic_create_request: TelematicCreateRequest,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicResponse:
    """Create a device, optionally assigning it to the vehicle named by VIN.

    Rule:
        A VIN that matches no live vehicle fails the request (D10). A
        vehicle may carry at most one live device; a soft-deleted device
        that still records the vehicle doesn't count, so a replacement can
        be mounted (deferred.md item 82).

    Args:
        db_session: Current database session.
        telematic_create_request: Validated create request.
        principal: The caller; the device is owned by the caller's
            organization unless internal staff name another one.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            the device cannot be mounted on another truck. `None` means no
            limit.

    Returns:
        The HTTP response for the new device.

    Raises:
        TelematicDateInvalidError: ``acquired_at`` has no time zone or lies
            more than ``CLIENT_CLOCK_SKEW`` in the future.
        TelematicVehicleNotFoundError: ``vehicle_vin`` matches no live
            vehicle in the caller's reach or fleet limit.
        OrganizationNotFoundError: ``organization_id`` matches no
            organization in the caller's reach.
        TelematicConflictError: The serial or IMEI is already used by a
            device still in the system, or the vehicle is already assigned
            to another live device.

    Side Effects:
        Inserts and flushes the device; a device created mounted gets
        ``installed_at`` = now, and ``acquired_at`` defaults to now. Does not
        commit.
    """
    acquired_at = telematic_create_request.acquired_at
    if acquired_at is not None:
        acquired_at = telematic_access.normalize_utc(acquired_at, "acquired_at")
        if acquired_at > utc_now() + CLIENT_CLOCK_SKEW:
            raise TelematicDateInvalidError("acquired_at cannot be in the future")
    if await telematics_repository.find_by_serial(
        db_session,
        telematic_create_request.telematic_serial,
    ):
        raise TelematicConflictError("Telematic serial already exists")
    if (
        telematic_create_request.imei is not None
        and await telematics_repository.find_by_imei(
            db_session, telematic_create_request.imei
        )
    ):
        raise TelematicConflictError("Telematic IMEI already exists")
    vehicle_id = None
    if telematic_create_request.vehicle_vin is not None:
        vehicle_id = await _resolve_vehicle_id_by_vin(
            db_session,
            telematic_create_request.vehicle_vin,
            principal,
            visible_vehicle_ids=visible_vehicle_ids,
        )
        if await telematics_repository.find_by_vehicle_id(db_session, vehicle_id):
            raise TelematicConflictError(
                "Vehicle is already assigned to another telematic"
            )
    organization_id = await identity_service.resolve_organization_for_new_record(
        db_session, principal, telematic_create_request.organization_id
    )
    now = utc_now()
    try:
        telematic_record = await telematics_repository.insert(
            db_session,
            {
                "telematic_serial": telematic_create_request.telematic_serial,
                "imei": telematic_create_request.imei,
                "organization_id": organization_id,
                "acquired_at": acquired_at or now,
                "vehicle_id": vehicle_id,
                "installed_at": now if vehicle_id is not None else None,
                "status": telematic_create_request.status,
                "status_reason": telematic_create_request.status_reason,
            },
        )
    except IntegrityError as error:
        raise TelematicConflictError(
            "Telematic serial, IMEI or vehicle already exists"
        ) from error
    return await build_telematic_response(db_session, telematic_record)


async def get_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicResponse:
    """Get the details of a device inside the caller's data reach.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.
        principal: The caller; a device of another organization is not found
            unless the caller is internal.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a device not mounted on one of them is not found. `None` means
            no limit.

    Returns:
        The HTTP response for the device.

    Raises:
        TelematicNotFoundError: The device does not exist, is soft-deleted or
            is out of the caller's reach.
    """
    telematic_record = await telematic_access.get_telematic_record(
        db_session, telematic_id, principal, visible_vehicle_ids=visible_vehicle_ids
    )
    return await build_telematic_response(db_session, telematic_record)


async def list_telematics(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int,
    page_size: int,
    status_filter: TelematicStatus | None = None,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicListResponse:
    """Get a paginated list of devices that are not soft-deleted.

    Args:
        db_session: Current database session.
        principal: The caller; only devices of the caller's organization are
            listed unless the caller is internal.
        page: Page number, starting from 1; clamped by
            ``normalize_page_window``.
        page_size: Maximum number of devices per page; clamped to
            ``1..API_MAX_PAGE_SIZE``.
        status_filter: Only list devices in this status, if given.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10):
            only devices mounted on them are listed, unmounted ones are
            hidden (D5). `None` means no limit.

    Returns:
        Paginated device list carrying the normalized page and page size.

    Side Effects:
        Read-only: the page query, then per assigned device one vehicle
        lookup for its VIN and the F-J1 health lookups (simple per-item
        version, per the no-preemptive-batching rule), then the total
        count.
    """
    page_window = normalize_page_window(page, page_size)
    telematic_records = await telematics_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        status_filter=status_filter,
        organization_id=principal.data_scope,
        vehicle_ids=visible_vehicle_ids,
    )
    return TelematicListResponse(
        items=[
            await build_telematic_response(db_session, telematic_record)
            for telematic_record in telematic_records
        ],
        total=await telematics_repository.count(
            db_session,
            status_filter,
            organization_id=principal.data_scope,
            vehicle_ids=visible_vehicle_ids,
        ),
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
    telematic_update_request: TelematicUpdateRequest,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicResponse:
    """Partially update a device, re-resolving its vehicle when a VIN is sent.

    Rule:
        A field that is not sent, or sent as ``null``, is left unchanged -
        except ``vehicle_vin``: sending a VIN re-resolves the assignment
        (a VIN matching no live vehicle fails the request, D10), and an
        explicit ``null`` unassigns the device. A vehicle may carry at most
        one live device.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.
        telematic_update_request: Validated partial update.
        principal: The caller; a device of another organization is not found
            unless the caller is internal.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a device not mounted on one of them is not found, and it cannot
            be moved to a truck outside them. `None` means no limit.

    Returns:
        The HTTP response for the updated device.

    Raises:
        TelematicNotFoundError: The device does not exist, is soft-deleted or
            is out of the caller's reach.
        TelematicVehicleNotFoundError: ``vehicle_vin`` matches no live
            vehicle in the caller's reach.
        TelematicConflictError: The new serial or IMEI is already used by a
            device still in the system, or the new vehicle is already
            assigned to another live device.

    Side Effects:
        Flushes the update and records it in the device's history with the
        caller as actor and the request's ``status_reason`` or else the
        fixed reason ``TELEMATIC_EDITED_REASON``. ``installed_at`` is set to
        now when the device is mounted on a different vehicle and cleared
        when it is unmounted. Does not commit.
    """
    telematic_record = await telematic_access.get_telematic_record(
        db_session, telematic_id, principal, visible_vehicle_ids=visible_vehicle_ids
    )
    requested_values = telematic_update_request.model_dump(exclude_unset=True)
    if (
        "telematic_serial" in requested_values
        and requested_values["telematic_serial"] != telematic_record.telematic_serial
        and await telematics_repository.find_by_serial(
            db_session, requested_values["telematic_serial"]
        )
    ):
        raise TelematicConflictError("Telematic serial already exists")
    if (
        requested_values.get("imei") is not None
        and requested_values["imei"] != telematic_record.imei
        and await telematics_repository.find_by_imei(
            db_session, requested_values["imei"]
        )
    ):
        raise TelematicConflictError("Telematic IMEI already exists")
    if "vehicle_vin" in requested_values:
        # exclude_unset keeps an explicit null, so "vehicle_vin" present
        # with None means "unassign"; absent means "leave unchanged".
        vehicle_vin = requested_values.pop("vehicle_vin")
        vehicle_id = (
            await _resolve_vehicle_id_by_vin(
                db_session,
                vehicle_vin,
                principal,
                visible_vehicle_ids=visible_vehicle_ids,
            )
            if vehicle_vin is not None
            else None
        )
        if vehicle_id is not None:
            assigned_telematic_record = await telematics_repository.find_by_vehicle_id(
                db_session, vehicle_id
            )
            if (
                assigned_telematic_record is not None
                and assigned_telematic_record.telematic_id
                != telematic_record.telematic_id
            ):
                raise TelematicConflictError(
                    "Vehicle is already assigned to another telematic"
                )
        requested_values["vehicle_id"] = vehicle_id
        if vehicle_id != telematic_record.vehicle_id:
            requested_values["installed_at"] = (
                utc_now() if vehicle_id is not None else None
            )
    update_values = {
        field_name: value
        for field_name, value in requested_values.items()
        if value is not None or field_name in ("vehicle_id", "installed_at")
    }
    try:
        telematic_record = await telematics_repository.update_fields(
            db_session,
            telematic_record,
            update_values,
            change_reason=update_values.get("status_reason") or TELEMATIC_EDITED_REASON,
            changed_by=principal.user_id,
        )
    except IntegrityError as error:
        raise TelematicConflictError(
            "Telematic serial, IMEI or vehicle already exists"
        ) from error
    return await build_telematic_response(db_session, telematic_record)


async def soft_delete_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
    *,
    principal: Principal,
    reason: str | None = None,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> None:
    """Soft-delete a device: it leaves the system (DM-25).

    Rule:
        The device is also set INACTIVE and unmounted, with a reason (the
        table's check constraint), so a deleted row never claims to be
        usable or mounted.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.
        principal: The caller; a device of another organization is not found
            unless the caller is internal.
        reason: Why the device leaves; defaults to
            ``TELEMATIC_DELETED_REASON``.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a device not mounted on one of them is not found. `None` means
            no limit.

    Raises:
        TelematicNotFoundError: The device does not exist, is already
            soft-deleted or is out of the caller's reach.

    Side Effects:
        Sets ``deleted_at``, ``status``, ``status_reason``, clears the vehicle
        and flushes (history reason ``TELEMATIC_DELETED_REASON``); does not
        commit.
    """
    telematic_record = await telematic_access.get_telematic_record(
        db_session, telematic_id, principal, visible_vehicle_ids=visible_vehicle_ids
    )
    await telematics_repository.soft_delete(
        db_session,
        telematic_record,
        change_reason=reason or TELEMATIC_DELETED_REASON,
        changed_by=principal.user_id,
    )


async def resolve_telematic_owner_organization_id(
    db: AsyncSession, telematic_id: UUID
) -> UUID | None:
    """Tell which organization owns a device now (public, for warranties).

    A warranty has no organization of its own (DM-24): it follows its object,
    so the warranties domain asks the object's domain who owns it.

    Args:
        db: Database session owned by the entry boundary.
        telematic_id: Internal ID of the device.

    Returns:
        The device's own owner (``telematics.organization_id``, TX-07), or
        `None` when the device does not exist or was soft-deleted.

    Side Effects:
        One read-only query.
    """
    telematic_record = await telematics_repository.get_by_id(db, telematic_id)
    return telematic_record.organization_id if telematic_record else None


async def resolve_mounted_device_by_vehicle_id(
    db: AsyncSession, vehicle_id: UUID
) -> TelematicMountedDevice | None:
    """Find the live device mounted on a truck now (public, for VEH-05 activation).

    Args:
        db: Database session owned by the entry boundary.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The mounted device, or `None` when the truck has none (a soft-deleted
        device is not mounted).

    Side Effects:
        One read-only query.
    """
    telematic_record = await telematics_repository.find_by_vehicle_id(db, vehicle_id)
    if telematic_record is None:
        return None
    return TelematicMountedDevice(
        telematic_id=telematic_record.telematic_id,
        telematic_serial=telematic_record.telematic_serial,
        status=telematic_record.status,
        mounted_at=telematic_record.installed_at or telematic_record.acquired_at,
    )
