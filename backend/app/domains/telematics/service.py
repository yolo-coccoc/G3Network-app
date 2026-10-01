"""Business service for Telematic device CRUD, health, config push and lookup.

Other domains, especially ``telemetry``, must only use the public functions in
this module to resolve device-vehicle mappings; they must not access
``telematics``' repository or model directly. The vehicle a device is
assigned to is resolved through the ``vehicles`` public service (by VIN on
input, by ID for the response); a device's read-time health (F-J1) through
the ``telemetry`` public service; a fleet's vehicles for the fleet-wide
config push (F-J2) through the ``fleet`` public service. No function here
commits or rolls back - the caller's entry boundary owns the transaction.
"""

import logging
from uuid import UUID

from aiomqtt import MqttError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.service as fleet_service
import app.domains.telematics.monitoring.silence_rule as silence_rule
import app.domains.telematics.repository as telematics_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.domains.telematics.commands import mqtt_publisher
from app.domains.telematics.exceptions import (
    TelematicCommandPublishError,
    TelematicConflictError,
    TelematicNotConfigurableError,
    TelematicNotFoundError,
    TelematicVehicleNotFoundError,
)
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import (
    TelematicConfigPushRequest,
    TelematicConfigResponse,
    TelematicCreateRequest,
    TelematicFleetConfigPushResponse,
    TelematicFleetConfigPushResult,
    TelematicListResponse,
    TelematicResponse,
    TelematicUpdateRequest,
)
from app.domains.telematics.types import (
    TelematicConfigPushOutcome,
    TelematicDeviceHealth,
    TelematicStatus,
    TelematicVehicleMapping,
)
from app.libs.common.clock import utc_now
from app.libs.common.pagination import normalize_page_window

logger = logging.getLogger(__name__)


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
        A ``TelematicVehicleMapping`` if the mapping is valid; ``None`` if
        the device does not exist, has been soft-deleted, has not been
        assigned a vehicle, or is assigned to a soft-deleted vehicle.

    Side Effects:
        Read-only: the mapping query, then one vehicle lookup through the
        vehicles service when a mapping exists. Does not commit or roll
        back.
    """
    telematic_vehicle_mapping = await telematics_repository.find_mapping_by_serial(
        db, serial
    )
    if telematic_vehicle_mapping is None:
        return None
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db, telematic_vehicle_mapping.vehicle_id
    )
    return telematic_vehicle_mapping if vehicle_reference is not None else None


async def _resolve_vehicle_id_by_vin(
    db_session: AsyncSession, vehicle_vin: str
) -> UUID:
    """Resolve the VIN sent on a device create/update into a live vehicle ID.

    Args:
        db_session: Current database session.
        vehicle_vin: VIN of the vehicle the device should be assigned to.

    Returns:
        Internal ID of the live vehicle carrying that VIN.

    Raises:
        TelematicVehicleNotFoundError: No live (non-soft-deleted) vehicle
            has that VIN (D10, future.md item 83).

    Side Effects:
        One read-only query through the vehicles public service.
    """
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
        db_session, vehicle_vin
    )
    if vehicle_reference is None:
        raise TelematicVehicleNotFoundError(
            f"Vehicle with VIN '{vehicle_vin}' not found"
        )
    return vehicle_reference.vehicle_id


# Health of a device with no telemetry to judge: not mounted on a live
# vehicle, or mounted on one that never reported. A frozen dataclass, so one
# shared module-level instance is safe for every response.
_UNMOUNTED_DEVICE_HEALTH = TelematicDeviceHealth(
    last_seen_at=None,
    is_online=False,
    is_silent=False,
    last_signal_strength_dbm=None,
)


async def _resolve_device_health(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
    vehicle_id: UUID,
) -> TelematicDeviceHealth:
    """Derive a mounted device's health from its vehicle's telemetry (F-J1).

    Rule:
        ``last_seen_at`` is the vehicle's newest telemetry ``received_at``,
        the exact anchor the device-health monitor measures silence from;
        ``is_online`` and the signal strength come from telemetry's live
        status (D2: newest arrival within
        ``TELEMETRY_ONLINE_THRESHOLD_SECONDS``). ``is_silent`` applies the
        monitor's rule (``silence_rule``) with the monitor's eligibility:
        only an ``ACTIVE`` device can be silent. A vehicle that never
        reported yields ``_UNMOUNTED_DEVICE_HEALTH`` (not silent: a
        provisioning gap, as in the monitor).

    Args:
        db_session: Current database session.
        telematic_record: The device; only its ``status`` is read here.
        vehicle_id: Its mounted, live (not soft-deleted) vehicle.

    Returns:
        The device's read-time health.

    Side Effects:
        Read-only: one last-seen lookup, plus one live-status lookup when
        the vehicle has reported, both through the telemetry public
        service. Called once per device - a list page pays this per item,
        per the no-preemptive-batching rule.
    """
    last_seen_at = await telemetry_service.resolve_last_telemetry_at(
        db_session, vehicle_id
    )
    if last_seen_at is None:
        return _UNMOUNTED_DEVICE_HEALTH
    vehicle_live_status = await telemetry_service.resolve_vehicle_live_status(
        db_session, vehicle_id
    )
    is_silent = (
        telematic_record.status is TelematicStatus.ACTIVE
        and silence_rule.calculate_is_device_silent(last_seen_at, now=utc_now())
    )
    return TelematicDeviceHealth(
        last_seen_at=last_seen_at,
        is_online=(
            vehicle_live_status.is_online if vehicle_live_status is not None else False
        ),
        is_silent=is_silent,
        last_signal_strength_dbm=(
            vehicle_live_status.signal_strength_dbm
            if vehicle_live_status is not None
            else None
        ),
    )


async def build_telematic_response(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
) -> TelematicResponse:
    """Build a telematic response with the vehicle's VIN and the device health.

    The VIN comes from the vehicles domain's public service and the health
    (F-J1) from the telemetry domain's, since this domain must not access
    either domain's repository or ORM model directly. A device assigned to
    a soft-deleted vehicle is treated as not mounted (D11, as in
    ``resolve_mapping_by_serial``): no VIN, and no health.

    Args:
        db_session: Current database session.
        telematic_record: Device record, flushed or loaded in this session.

    Returns:
        The HTTP response for the device. ``vehicle_vin`` is ``None`` and
        the health fields are null/``False`` when no vehicle is assigned
        or the assigned vehicle is soft-deleted.

    Side Effects:
        Read-only: one vehicle lookup when a vehicle is assigned, then the
        health lookups of ``_resolve_device_health`` when it is live.
    """
    vehicle_vin = None
    device_health = _UNMOUNTED_DEVICE_HEALTH
    if telematic_record.vehicle_id:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session,
            telematic_record.vehicle_id,
        )
        if vehicle_reference is not None:
            vehicle_vin = vehicle_reference.vin
            device_health = await _resolve_device_health(
                db_session, telematic_record, vehicle_reference.vehicle_id
            )
    return TelematicResponse(
        telematic_id=telematic_record.telematic_id,
        telematic_serial=telematic_record.telematic_serial,
        vehicle_id=telematic_record.vehicle_id,
        vehicle_vin=vehicle_vin,
        status=telematic_record.status,
        firmware_version=telematic_record.firmware_version,
        telemetry_interval_seconds=telematic_record.telemetry_interval_seconds,
        config_pushed_at=telematic_record.config_pushed_at,
        last_seen_at=device_health.last_seen_at,
        is_online=device_health.is_online,
        is_silent=device_health.is_silent,
        last_signal_strength_dbm=device_health.last_signal_strength_dbm,
        created_at=telematic_record.created_at,
        updated_at=telematic_record.updated_at,
    )


async def create_telematic(
    db_session: AsyncSession, telematic_create_request: TelematicCreateRequest
) -> TelematicResponse:
    """Create a device, optionally assigning it to the vehicle named by VIN.

    Rule:
        A VIN that matches no live vehicle fails the request (D10). A
        vehicle may carry at most one live device; a soft-deleted device
        that still records the vehicle doesn't count, so a replacement can
        be mounted (future.md item 82).

    Args:
        db_session: Current database session.
        telematic_create_request: Validated create request.

    Returns:
        The HTTP response for the new device.

    Raises:
        TelematicVehicleNotFoundError: ``vehicle_vin`` matches no live
            vehicle.
        TelematicConflictError: The serial already exists (including on a
            soft-deleted device, detected only at flush time), or the
            vehicle is already assigned to another live device.

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
    if telematic_create_request.vehicle_vin is not None:
        vehicle_id = await _resolve_vehicle_id_by_vin(
            db_session, telematic_create_request.vehicle_vin
        )
        if await telematics_repository.find_by_vehicle_id(db_session, vehicle_id):
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
        except ``vehicle_vin``: sending a VIN re-resolves the assignment
        (a VIN matching no live vehicle fails the request, D10), and an
        explicit ``null`` unassigns the device. A vehicle may carry at most
        one live device.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.
        telematic_update_request: Validated partial update.

    Returns:
        The HTTP response for the updated device.

    Raises:
        TelematicNotFoundError: The device does not exist or is soft-deleted.
        TelematicVehicleNotFoundError: ``vehicle_vin`` matches no live
            vehicle.
        TelematicConflictError: The new serial already exists (on a
            soft-deleted device it is only detected at flush time), or the
            new vehicle is already assigned to another live device.

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
        # exclude_unset keeps an explicit null, so "vehicle_vin" present
        # with None means "unassign"; absent means "leave unchanged".
        vehicle_vin = requested_values.pop("vehicle_vin")
        vehicle_id = (
            await _resolve_vehicle_id_by_vin(db_session, vehicle_vin)
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
            "Telematic serial or vehicle already exists"
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
    return await _publish_and_record_config(
        db_session,
        telematic_record,
        telematic_config_push_request.telemetry_interval_seconds,
    )


async def _publish_and_record_config(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
    telemetry_interval_seconds: int,
) -> TelematicConfigResponse:
    """Publish a set-interval command to one device, then record it (F-J2).

    The one publish path shared by the single-device and the fleet-wide
    push. Fail-closed, publish-then-record: see ``push_telematic_config``.
    The caller has already checked that the device may be configured.

    Args:
        db_session: Session whose transaction is owned by the caller.
        telematic_record: The device to configure, loaded in this session.
        telemetry_interval_seconds: Interval to publish and record.

    Returns:
        The pushed configuration and its command topic.

    Raises:
        TelematicCommandPublishError: The MQTT broker was unreachable or
            the publish otherwise failed; nothing is recorded.

    Side Effects:
        Publishes one MQTT message, then writes the device's
        ``telemetry_interval_seconds``/``config_pushed_at`` (the same
        instant stamped on the command) and flushes. Does not commit.
    """
    telematic_id = telematic_record.telematic_id
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


async def push_fleet_config(
    db_session: AsyncSession,
    fleet_id: UUID,
    telematic_config_push_request: TelematicConfigPushRequest,
) -> TelematicFleetConfigPushResponse:
    """Push one telemetry publish-interval config to a whole fleet (F-J2, D9).

    Rule (planner D9):
        One result per active fleet member, in membership order (oldest
        member first). A member is ``skipped`` - nothing attempted - when
        its vehicle is soft-deleted (D11: its device is treated as not
        mounted), it has no live device, or its device is not ``ACTIVE``
        (stricter than the single push, which also accepts
        ``MAINTENANCE``: a bulk push must not retune a device that is being
        serviced; push to it individually). Otherwise the device goes
        through the single push's publish path and is ``published`` or,
        when the publish raises, ``failed`` with the reason. Partial
        failure is reported, never raised: one unreachable publish does not
        stop the loop.

    Transaction / partial-failure behaviour:
        Everything runs in the caller's one transaction. Each device is
        publish-then-record, so a ``failed`` device records nothing and a
        ``published`` one records its new interval and push time; a later
        failure does not undo an earlier success - that device already
        received the command, and the database must say so. If an
        unexpected error (e.g. a database error) aborts the whole request,
        the transaction rolls back while the devices already published keep
        the new interval: the same accepted under-claiming residual as the
        single push, reconverged by the next push.

    Args:
        db_session: Session whose transaction is owned by the caller.
        fleet_id: Internal ID of the fleet.
        telematic_config_push_request: Desired telemetry publish interval.

    Returns:
        The fleet ID, the count per outcome and one result per member.

    Raises:
        FleetNotFoundError: The fleet does not exist or is soft-deleted
            (raised by the fleet public service; 404).

    Side Effects:
        Sequential, one member at a time (no batching): per member one
        vehicle lookup and one device lookup, then for an eligible device
        one MQTT publish (its own short-lived connection, so a broker
        outage costs up to ``MQTT_COMMAND_TIMEOUT_SECONDS`` per device)
        and one flush of the device row. Does not commit.
    """
    vehicle_ids = await fleet_service.list_active_member_vehicle_ids(
        db_session, fleet_id
    )
    fleet_config_push_results = [
        await _push_config_to_fleet_vehicle(
            db_session,
            vehicle_id,
            telematic_config_push_request.telemetry_interval_seconds,
        )
        for vehicle_id in vehicle_ids
    ]
    outcomes = [
        fleet_config_push_result.outcome
        for fleet_config_push_result in fleet_config_push_results
    ]
    fleet_config_push_response = TelematicFleetConfigPushResponse(
        fleet_id=fleet_id,
        published_count=outcomes.count(TelematicConfigPushOutcome.PUBLISHED),
        skipped_count=outcomes.count(TelematicConfigPushOutcome.SKIPPED),
        failed_count=outcomes.count(TelematicConfigPushOutcome.FAILED),
        results=fleet_config_push_results,
    )
    logger.info(
        "Fleet config push completed",
        extra={
            "fleet_id": str(fleet_id),
            "published": fleet_config_push_response.published_count,
            "skipped": fleet_config_push_response.skipped_count,
            "failed": fleet_config_push_response.failed_count,
        },
    )
    return fleet_config_push_response


async def _push_config_to_fleet_vehicle(
    db_session: AsyncSession,
    vehicle_id: UUID,
    telemetry_interval_seconds: int,
) -> TelematicFleetConfigPushResult:
    """Push the config to one fleet vehicle's device, reporting the outcome.

    Applies ``push_fleet_config``'s skip rules, then the shared publish
    path; a publish failure becomes a ``failed`` result instead of an
    exception.

    Args:
        db_session: Session whose transaction is owned by the caller.
        vehicle_id: Internal ID of the fleet member.
        telemetry_interval_seconds: Interval to publish and record.

    Returns:
        This vehicle's result: ``published``, ``skipped`` or ``failed``.

    Side Effects:
        One vehicle lookup through the vehicles public service and one
        device lookup; for an eligible device, one MQTT publish and, on
        success, one flush of the device row. Does not commit.
    """
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db_session, vehicle_id
    )
    if vehicle_reference is None:
        return TelematicFleetConfigPushResult(
            vehicle_id=vehicle_id,
            telematic_id=None,
            telematic_serial=None,
            outcome=TelematicConfigPushOutcome.SKIPPED,
            reason="vehicle is soft-deleted",
        )
    telematic_record = await telematics_repository.find_by_vehicle_id(
        db_session, vehicle_id
    )
    if telematic_record is None:
        return TelematicFleetConfigPushResult(
            vehicle_id=vehicle_id,
            telematic_id=None,
            telematic_serial=None,
            outcome=TelematicConfigPushOutcome.SKIPPED,
            reason="no device",
        )
    if telematic_record.status is not TelematicStatus.ACTIVE:
        return TelematicFleetConfigPushResult(
            vehicle_id=vehicle_id,
            telematic_id=telematic_record.telematic_id,
            telematic_serial=telematic_record.telematic_serial,
            outcome=TelematicConfigPushOutcome.SKIPPED,
            reason=(
                f"device is {telematic_record.status.value}; only ACTIVE "
                "devices receive a fleet-wide push"
            ),
        )
    try:
        await _publish_and_record_config(
            db_session, telematic_record, telemetry_interval_seconds
        )
    except TelematicCommandPublishError as error:
        # Reported, not raised (D9): the other members are still pushed.
        # _publish_and_record_config already logged the traceback.
        return TelematicFleetConfigPushResult(
            vehicle_id=vehicle_id,
            telematic_id=telematic_record.telematic_id,
            telematic_serial=telematic_record.telematic_serial,
            outcome=TelematicConfigPushOutcome.FAILED,
            reason=str(error),
        )
    return TelematicFleetConfigPushResult(
        vehicle_id=vehicle_id,
        telematic_id=telematic_record.telematic_id,
        telematic_serial=telematic_record.telematic_serial,
        outcome=TelematicConfigPushOutcome.PUBLISHED,
        reason=None,
    )
