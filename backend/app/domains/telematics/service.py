"""Business service for Telematic device CRUD and public lookup.

Other domains, especially ``telemetry``, must only use the public functions in
this module to resolve device-vehicle mappings; they must not access
``telematics``' repository or model directly.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from aiomqtt import MqttError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telematics import repository
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
from app.domains.vehicles import service as vehicle_service
from app.libs.common.config import settings

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
        Tuple ``(telematic_id, vehicle_id)`` if the mapping is valid; ``None``
        if the device does not exist, has been soft-deleted, or has not been
        assigned a vehicle.

    Side Effects:
        Performs a read-only query in the current session; does not commit or
        rollback.
    """
    return await repository.find_mapping_by_serial(db, serial)


async def resolve_mappings_by_serial(
    db: AsyncSession,
    serials: Sequence[str],
) -> dict[str, TelematicVehicleMapping]:
    """Resolve a batch of telematic serials into device-vehicle mappings.

    Args:
        db: Database session owned by the entry boundary.
        serials: Physical serials to look up.

    Returns:
        Dict mapping serial to ``(telematic_id, vehicle_id)``; invalid
        mappings do not appear in the result.

    Side Effects:
        Performs a single read-only query in the current session; does not
        commit or rollback.
    """
    return await repository.find_mappings_by_serial(db, serials)


async def build_telematic_response(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
) -> TelematicResponse:
    """Build a telematic response and add the vehicle's current VIN.

    Adding the VIN requires calling the vehicles domain's public service,
    since this domain must not access the vehicles repository or ORM model
    directly.
    """
    vin = None
    if telematic_record.vehicle_id:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session,
            telematic_record.vehicle_id,
        )
        vin = vehicle_reference.vin if vehicle_reference else None
    return TelematicResponse.model_validate(
        {**telematic_record.__dict__, "vehicle_vin": vin}
    )


async def create_telematic(
    db_session: AsyncSession, telematic_create_request: TelematicCreateRequest
) -> TelematicResponse:
    """Create a device, resolving the VIN if the vehicle exists."""
    if await repository.get_by_serial(
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
        if vehicle_id and await db_session.scalar(
            select(TelematicModel.telematic_id).where(
                TelematicModel.vehicle_id == vehicle_id,
                TelematicModel.deleted_at.is_(None),
            )
        ):
            raise TelematicConflictError(
                "Vehicle is already assigned to another telematic"
            )
    try:
        telematic_record = await repository.insert(
            db_session,
            {
                "telematic_serial": telematic_create_request.telematic_serial,
                "vehicle_id": vehicle_id,
                "status": telematic_create_request.status,
                "firmware_version": telematic_create_request.firmware_version,
                # F-J2: explicitly set so build_telematic_response's
                # __dict__ spread has the key before any config is ever
                # pushed - a column never assigned during construction
                # is simply absent from a transient ORM instance's
                # __dict__, not present-with-None.
                "telemetry_interval_seconds": None,
                "config_pushed_at": None,
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
    """Get device details."""
    telematic_record = await repository.get_by_id(db_session, telematic_id)
    if not telematic_record:
        raise TelematicNotFoundError("Telematic not found")
    return await build_telematic_response(db_session, telematic_record)


async def list_telematics(
    db_session: AsyncSession,
    page: int,
    page_size: int,
    status: TelematicStatus | None,
) -> TelematicListResponse:
    """Get the paginated list of devices."""
    page_size = min(max(page_size, 1), settings.API_MAX_PAGE_SIZE)
    page = max(page, settings.API_DEFAULT_PAGE)
    telematic_records = await repository.list_all(
        db_session,
        (page - 1) * page_size,
        page_size,
        status,
    )
    return TelematicListResponse(
        items=[
            await build_telematic_response(db_session, telematic_record)
            for telematic_record in telematic_records
        ],
        total=await repository.count(db_session, status),
        page=page,
        page_size=page_size,
    )


async def update_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
    telematic_update_request: TelematicUpdateRequest,
) -> TelematicResponse:
    """Update a device and re-resolve the VIN when the field is sent."""
    telematic_record = await repository.get_by_id(db_session, telematic_id)
    if not telematic_record:
        raise TelematicNotFoundError("Telematic not found")
    values = telematic_update_request.model_dump(exclude_unset=True)
    if (
        "telematic_serial" in values
        and values["telematic_serial"] != telematic_record.telematic_serial
        and await repository.get_by_serial(db_session, values["telematic_serial"])
    ):
        raise TelematicConflictError("Telematic serial already exists")
    if "vehicle_vin" in values:
        vin = values.pop("vehicle_vin")
        vehicle_reference = (
            await vehicle_service.resolve_vehicle_reference_by_vin(db_session, vin)
            if vin
            else None
        )
        values["vehicle_id"] = (
            vehicle_reference.vehicle_id if vehicle_reference else None
        )
    values = {
        key: value
        for key, value in values.items()
        if value is not None or key == "vehicle_id"
    }
    try:
        telematic_record = await repository.update_fields(
            db_session,
            telematic_record,
            values,
        )
    except IntegrityError as error:
        raise TelematicConflictError(
            "Vehicle is already assigned to another telematic"
        ) from error
    if "vehicle_id" in values and values["vehicle_id"] is not None:
        # F-F2: advance the vehicle's activation state machine now that a
        # device is (re)assigned. Best-effort side channel - never raises.
        await vehicle_service.mark_device_assigned(db_session, values["vehicle_id"])
    return await build_telematic_response(db_session, telematic_record)


async def soft_delete_telematic(
    db_session: AsyncSession,
    telematic_id: UUID,
) -> None:
    """Soft delete a device."""
    telematic_record = await repository.get_by_id(db_session, telematic_id)
    if not telematic_record:
        raise TelematicNotFoundError("Telematic not found")
    await repository.soft_delete(db_session, telematic_record)


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
    telematic_record = await repository.get_by_id(db_session, telematic_id)
    if telematic_record is None:
        raise TelematicNotFoundError("Telematic not found")
    if telematic_record.status is TelematicStatus.INACTIVE:
        raise TelematicNotConfigurableError(
            "Telematic is INACTIVE and cannot be configured"
        )

    telemetry_interval_seconds = (
        telematic_config_push_request.telemetry_interval_seconds
    )
    issued_at = datetime.now(timezone.utc)
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

    await repository.update_fields(
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
