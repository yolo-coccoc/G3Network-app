"""Business service for Telematic device CRUD, config push and public lookup.

Other domains, especially ``telemetry``, must only use the public functions in
this module to resolve device-vehicle mappings; they must not access
``telematics``' repository or model directly. The vehicle a device is
assigned to is resolved through the ``vehicles`` public service (by VIN on
input, by ID for the response). No function here commits or rolls back -
the caller's entry boundary owns the transaction.
"""

import logging
from uuid import UUID

from aiomqtt import MqttError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.repository as telematics_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.telematics.commands import mqtt_publisher
from app.domains.telematics.exceptions import (
    TelematicCommandPublishError,
    TelematicConflictError,
    TelematicNotConfigurableError,
    TelematicNotFoundError,
)
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import (
    TelematicConfigPushRequest,
    TelematicConfigResponse,
    TelematicCreateRequest,
    TelematicListResponse,
    TelematicResponse,
    TelematicUpdateRequest,
)
from app.domains.telematics.types import TelematicStatus, TelematicVehicleMapping
from app.libs.common.clock import utc_now
from app.libs.common.pagination import normalize_page_window

logger = logging.getLogger(__name__)


async def resolve_mapping_by_serial(
    db: AsyncSession,
    serial: str,
) -> TelematicVehicleMapping | None:
    """Resolve a telematic serial into a device ID and vehicle ID.

    Args:
        db: Database session owned by the entry boundary.
        serial: Physical serial received from a telemetry message.

    Returns:
        A ``TelematicVehicleMapping`` if the mapping is valid; ``None`` if
        the device does not exist, has been soft-deleted, or has not been
        assigned a vehicle.

    Side Effects:
        Performs a read-only query in the current session; does not commit or
        rollback.
    """
    return await telematics_repository.find_mapping_by_serial(db, serial)


async def build_telematic_response(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
) -> TelematicResponse:
    """Build a telematic response and add the assigned vehicle's current VIN.

    Adding the VIN requires calling the vehicles domain's public service,
    since this domain must not access the vehicles repository or ORM model
    directly.

    Args:
        db_session: Current database session.
        telematic_record: Device record, flushed or loaded in this session.

    Returns:
        The HTTP response for the device. ``vehicle_vin`` is ``None`` when
        no vehicle is assigned or the assigned vehicle is soft-deleted.

    Side Effects:
        One read-only query through the vehicles service when a vehicle is
        assigned.
    """
    vehicle_vin = None
    if telematic_record.vehicle_id:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session,
            telematic_record.vehicle_id,
        )
        vehicle_vin = vehicle_reference.vin if vehicle_reference else None
    return TelematicResponse(
        telematic_id=telematic_record.telematic_id,
        telematic_serial=telematic_record.telematic_serial,
        vehicle_id=telematic_record.vehicle_id,
        vehicle_vin=vehicle_vin,
        status=telematic_record.status,
        firmware_version=telematic_record.firmware_version,
        telemetry_interval_seconds=telematic_record.telemetry_interval_seconds,
        config_pushed_at=telematic_record.config_pushed_at,
        created_at=telematic_record.created_at,
        updated_at=telematic_record.updated_at,
    )


async def create_telematic(
    db_session: AsyncSession, telematic_create_request: TelematicCreateRequest
) -> TelematicResponse:
    """Create a device, assigning it to the vehicle named by VIN if that exists.

    Rule:
        A VIN that matches no live vehicle leaves the device unassigned
        rather than failing the request. A vehicle may carry at most one
        live device.

    Args:
        db_session: Current database session.
        telematic_create_request: Validated create request.

    Returns:
        The HTTP response for the new device.

    Raises:
        TelematicConflictError: The serial already exists, or the vehicle
            is already assigned to another device (including a constraint
            violation detected only at flush time).

    Side Effects:
        Inserts and flushes the device; when a vehicle is assigned, advances
        that vehicle's F-F2 activation status via
        ``vehicle_service.mark_device_assigned``. Does not commit.
    """
    if await telematics_repository.find_by_serial(
        db_session,
        telematic_create_request.telematic_serial,
    ):
        raise TelematicConflictError("Telematic serial already exists")
    vehicle_id = None
    if telematic_create_request.vehicle_vin:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
            db_session,
            telematic_create_request.vehicle_vin,
        )
        vehicle_id = vehicle_reference.vehicle_id if vehicle_reference else None
        if vehicle_id and await telematics_repository.find_by_vehicle_id(
            db_session, vehicle_id
        ):
            raise TelematicConflictError(
                "Vehicle is already assigned to another telematic"
            )
    try:
        telematic_record = await telematics_repository.insert(
            db_session,
            {
                "telematic_serial": telematic_create_request.telematic_serial,
                "vehicle_id": vehicle_id,
                "status": telematic_create_request.status,
                "firmware_version": telematic_create_request.firmware_version,
            },
        )
    except IntegrityError as error:
        raise TelematicConflictError(
            "Telematic serial or vehicle already exists"
        ) from error
    if vehicle_id is not None:
        # F-F2: advance the vehicle's activation state machine now that a
        # device is assigned. Best-effort side channel - never raises.
        await vehicle_service.mark_device_assigned(db_session, vehicle_id)
    return await build_telematic_response(db_session, telematic_record)


async def get_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> TelematicResponse:
    """Get the details of a device.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.

    Returns:
        The HTTP response for the device.

    Raises:
        TelematicNotFoundError: The device does not exist or is soft-deleted.
    """
    telematic_record = await telematics_repository.get_by_id(db_session, telematic_id)
    if not telematic_record:
        raise TelematicNotFoundError("Telematic not found")
    return await build_telematic_response(db_session, telematic_record)


async def list_telematics(
    db_session: AsyncSession,
    *,
    page: int,
    page_size: int,
    status_filter: TelematicStatus | None = None,
) -> TelematicListResponse:
    """Get a paginated list of devices that are not soft-deleted.

    Args:
        db_session: Current database session.
        page: Page number, starting from 1; clamped by
            ``normalize_page_window``.
        page_size: Maximum number of devices per page; clamped to
            ``1..API_MAX_PAGE_SIZE``.
        status_filter: Only list devices in this status, if given.

    Returns:
        Paginated device list carrying the normalized page and page size.

    Side Effects:
        Read-only: the page query, one vehicle lookup per assigned device
        for its VIN (simple per-item version, per the no-preemptive-batching
        rule), then the total count.
    """
    page_window = normalize_page_window(page, page_size)
    telematic_records = await telematics_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        status_filter=status_filter,
    )
    return TelematicListResponse(
        items=[
            await build_telematic_response(db_session, telematic_record)
            for telematic_record in telematic_records
        ],
        total=await telematics_repository.count(db_session, status_filter),
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
    telematic_update_request: TelematicUpdateRequest,
) -> TelematicResponse:
    """Partially update a device, re-resolving its vehicle when a VIN is sent.

    Rule:
        A field that is not sent, or sent as ``null``, is left unchanged -
        except ``vehicle_vin``: sending it re-resolves the assignment, and
        ``null`` (or a VIN matching no live vehicle) unassigns the device.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.
        telematic_update_request: Validated partial update.

    Returns:
        The HTTP response for the updated device.

    Raises:
        TelematicNotFoundError: The device does not exist or is soft-deleted.
        TelematicConflictError: The new serial already exists, or the new
            vehicle is already assigned to another device (detected at
            flush time).

    Side Effects:
        Flushes the update; when a vehicle is (re)assigned, advances that
        vehicle's F-F2 activation status via
        ``vehicle_service.mark_device_assigned``. Does not commit.
    """
    telematic_record = await telematics_repository.get_by_id(db_session, telematic_id)
    if not telematic_record:
        raise TelematicNotFoundError("Telematic not found")
    requested_values = telematic_update_request.model_dump(exclude_unset=True)
    if (
        "telematic_serial" in requested_values
        and requested_values["telematic_serial"] != telematic_record.telematic_serial
        and await telematics_repository.find_by_serial(
            db_session, requested_values["telematic_serial"]
        )
    ):
        raise TelematicConflictError("Telematic serial already exists")
    if "vehicle_vin" in requested_values:
        vehicle_vin = requested_values.pop("vehicle_vin")
        vehicle_reference = (
            await vehicle_service.resolve_vehicle_reference_by_vin(
                db_session, vehicle_vin
            )
            if vehicle_vin
            else None
        )
        requested_values["vehicle_id"] = (
            vehicle_reference.vehicle_id if vehicle_reference else None
        )
    update_values = {
        field_name: value
        for field_name, value in requested_values.items()
        if value is not None or field_name == "vehicle_id"
    }
    try:
        telematic_record = await telematics_repository.update_fields(
            db_session,
            telematic_record,
            update_values,
        )
    except IntegrityError as error:
        raise TelematicConflictError(
            "Vehicle is already assigned to another telematic"
        ) from error
    assigned_vehicle_id = update_values.get("vehicle_id")
    if assigned_vehicle_id is not None:
        # F-F2: advance the vehicle's activation state machine now that a
        # device is (re)assigned. Best-effort side channel - never raises.
        await vehicle_service.mark_device_assigned(db_session, assigned_vehicle_id)
    return await build_telematic_response(db_session, telematic_record)


async def soft_delete_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> None:
    """Soft-delete a device.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.

    Raises:
        TelematicNotFoundError: The device does not exist or is already
            soft-deleted.

    Side Effects:
        Sets ``deleted_at`` and flushes; does not commit.
    """
    telematic_record = await telematics_repository.get_by_id(db_session, telematic_id)
    if not telematic_record:
        raise TelematicNotFoundError("Telematic not found")
    await telematics_repository.soft_delete(db_session, telematic_record)


async def push_telematic_config(
    db_session: AsyncSession,
    telematic_id: UUID,
    telematic_config_push_request: TelematicConfigPushRequest,
) -> TelematicConfigResponse:
    """Push a telemetry publish-interval config to a device over MQTT (F-J2).

    Rule:
        Fail-closed, publish-then-record: the MQTT command is published
        before anything is written to the database, and the database is
        only updated when the publish succeeds. If the publish fails,
        nothing is persisted, so ``telemetry_interval_seconds`` never
        claims a push that didn't happen - the only honest meaning left
        available given that no MQTT ack topic exists for this command
        (mqtt-spec.md 2.3).

    Args:
        db_session: Session whose transaction is owned by the caller.
        telematic_id: Internal ID of the device to configure.
        telematic_config_push_request: Desired telemetry publish interval.

    Returns:
        The pushed configuration, echoing the exact command topic so an
        operator can verify delivery with ``mosquitto_sub`` against it.

    Raises:
        TelematicNotFoundError: Device does not exist or is soft-deleted.
        TelematicNotConfigurableError: Device status is ``INACTIVE`` - a
            device deliberately taken out of service should not silently
            accept a new operating config. ``MAINTENANCE`` is allowed:
            retuning a device that's being serviced is a plausible use.
        TelematicCommandPublishError: The MQTT broker was unreachable or
            the publish otherwise failed.

    Side Effects:
        Publishes one MQTT message, then writes the device's config-push
        columns and flushes (commit is the caller's responsibility). A
        crash between a successful publish and the caller's commit is a
        known, accepted residual: the device may be on the new interval
        while the database still shows the old one - the safer direction,
        since the database only ever under-claims and the next push
        reconverges it.
    """
    telematic_record = await telematics_repository.get_by_id(db_session, telematic_id)
    if telematic_record is None:
        raise TelematicNotFoundError("Telematic not found")
    if telematic_record.status is TelematicStatus.INACTIVE:
        raise TelematicNotConfigurableError(
            "Telematic is INACTIVE and cannot be configured"
        )

    telemetry_interval_seconds = (
        telematic_config_push_request.telemetry_interval_seconds
    )
    issued_at = utc_now()
    command_topic = mqtt_publisher.build_command_topic(
        telematic_record.telematic_serial
    )
    command_payload = mqtt_publisher.build_set_telemetry_interval_payload(
        telemetry_interval_seconds, issued_at=issued_at
    )
    try:
        await mqtt_publisher.publish_device_command(
            telematic_record.telematic_serial, command_payload
        )
    except (MqttError, TimeoutError) as error:
        logger.exception(
            "Failed to publish device command",
            extra={
                "telematic_id": str(telematic_id),
                "telematic_serial": telematic_record.telematic_serial,
                "topic": command_topic,
            },
        )
        raise TelematicCommandPublishError(
            f"Failed to publish config to '{telematic_record.telematic_serial}'"
        ) from error

    await telematics_repository.update_fields(
        db_session,
        telematic_record,
        {
            "telemetry_interval_seconds": telemetry_interval_seconds,
            "config_pushed_at": issued_at,
        },
    )
    logger.info(
        "Device config pushed",
        extra={
            "telematic_id": str(telematic_id),
            "telematic_serial": telematic_record.telematic_serial,
            "telemetry_interval_seconds": telemetry_interval_seconds,
            "topic": command_topic,
        },
    )
    return TelematicConfigResponse(
        telematic_id=telematic_id,
        telematic_serial=telematic_record.telematic_serial,
        telemetry_interval_seconds=telemetry_interval_seconds,
        config_pushed_at=issued_at,
        command_topic=command_topic,
    )
