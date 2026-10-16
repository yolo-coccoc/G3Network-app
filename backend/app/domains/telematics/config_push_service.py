"""Configuration push to T-Boxes over MQTT (F-J2, DEV-07, TX-08).

Publishes a telemetry-interval configuration to one device, or to every
device mounted on a fleet's trucks (the fleet limit applies through
``fleet``); only a mounted, ACTIVE device is configurable. Internal module:
the domain's router calls it.
"""

import logging
from uuid import UUID

from aiomqtt import MqttError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.service as fleet_service
import app.domains.telematics.access as telematic_access
import app.domains.telematics.repository as telematics_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.identity.types import Principal
from app.domains.telematics.commands import mqtt_publisher
from app.domains.telematics.exceptions import (
    TelematicCommandPublishError,
    TelematicNotConfigurableError,
)
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import (
    TelematicConfigPushRequest,
    TelematicConfigResponse,
    TelematicFleetConfigPushResponse,
    TelematicFleetConfigPushResult,
)
from app.domains.telematics.types import (
    TelematicConfigPushOutcome,
    TelematicStatus,
)
from app.libs.common.clock import utc_now

logger = logging.getLogger(__name__)


async def push_telematic_config(
    db_session: AsyncSession,
    telematic_id: UUID,
    telematic_config_push_request: TelematicConfigPushRequest,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicConfigResponse:
    """Push a telemetry publish-interval config to a device over MQTT (F-J2).

    Rule:
        Fail-closed: a publish failure is raised, never swallowed. Nothing
        is written to the database: the device confirms the interval it
        uses in its next status report (TX-09, TX-11), which is the only
        honest record given that no MQTT ack topic exists for this command
        (mqtt-spec.md 2.3).

    Args:
        db_session: Session whose transaction is owned by the caller.
        telematic_id: Internal ID of the device to configure.
        telematic_config_push_request: Desired telemetry publish interval.
        principal: The caller; a device of another organization is not found
            unless the caller is internal.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a device not mounted on one of them is not found. `None` means
            no limit.

    Returns:
        The pushed configuration, echoing the exact command topic so an
        operator can verify delivery with ``mosquitto_sub`` against it.

    Raises:
        TelematicNotFoundError: Device does not exist, is soft-deleted or is
            out of the caller's reach.
        TelematicNotConfigurableError: The device is not mounted on a vehicle
            or is ``INACTIVE`` (TX-08: only a mounted, ACTIVE device receives
            configuration) - a device in stock or deliberately taken out of
            service should not silently accept a new operating config.
        TelematicCommandPublishError: The MQTT broker was unreachable or
            the publish otherwise failed.

    Side Effects:
        Publishes one MQTT message. Writes nothing.
    """
    telematic_record = await telematic_access.get_telematic_record(
        db_session, telematic_id, principal, visible_vehicle_ids=visible_vehicle_ids
    )
    _require_configurable(telematic_record)
    return await _publish_config(
        telematic_record,
        telematic_config_push_request.telemetry_interval_seconds,
    )


def _require_configurable(telematic_record: TelematicModel) -> None:
    """Check TX-08: only a mounted, ACTIVE device receives configuration.

    Args:
        telematic_record: The device to configure, loaded in this session.

    Raises:
        TelematicNotConfigurableError: The device is not mounted on a vehicle,
            or is not ``ACTIVE``.
    """
    if telematic_record.vehicle_id is None:
        raise TelematicNotConfigurableError(
            "Telematic is not mounted on a vehicle and cannot be configured"
        )
    if telematic_record.status is not TelematicStatus.ACTIVE:
        raise TelematicNotConfigurableError(
            f"Telematic is {telematic_record.status.value} and cannot be configured"
        )


async def _publish_config(
    telematic_record: TelematicModel,
    telemetry_interval_seconds: int,
) -> TelematicConfigResponse:
    """Publish a set-interval command to one device (F-J2).

    The one publish path shared by the single-device and the fleet-wide
    push. The caller has already checked that the device may be configured.

    Args:
        telematic_record: The device to configure, loaded in this session.
        telemetry_interval_seconds: Interval to publish.

    Returns:
        The pushed configuration and its command topic.

    Raises:
        TelematicCommandPublishError: The MQTT broker was unreachable or
            the publish otherwise failed.

    Side Effects:
        Publishes one MQTT message; writes nothing to the database.
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
    *,
    principal: Principal,
) -> TelematicFleetConfigPushResponse:
    """Push one telemetry publish-interval config to a whole fleet (F-J2, D9).

    Rule (planner D9):
        One result per active fleet member, in membership order (oldest
        member first). A member is ``skipped`` - nothing attempted - when
        its vehicle is soft-deleted (D11: its device is treated as not
        mounted), it has no live device, or its device is not ``ACTIVE``.
        Otherwise the device goes through the single push's publish path
        and is ``published`` or, when the publish raises, ``failed`` with
        the reason. Partial failure is reported, never raised: one
        unreachable publish does not stop the loop.

    Transaction / partial-failure behaviour:
        Nothing is written to the database (the device confirms the interval
        it uses in its next status report, TX-09), so a later failure cannot
        leave the database disagreeing with a command already published.

    Args:
        db_session: Session whose transaction is owned by the caller.
        fleet_id: Internal ID of the fleet.
        telematic_config_push_request: Desired telemetry publish interval.
        principal: The caller; a fleet of another organization is not found
            unless the caller is internal.

    Returns:
        The fleet ID, the count per outcome and one result per member.

    Raises:
        FleetNotFoundError: The fleet does not exist or is soft-deleted
            (raised by the fleet public service; 404).

    Side Effects:
        Sequential, one member at a time (no batching): per member one
        vehicle lookup and one device lookup, then for an eligible device
        one MQTT publish (its own short-lived connection, so a broker
        outage costs up to ``MQTT_COMMAND_TIMEOUT_SECONDS`` per device).
        Writes nothing.
    """
    vehicle_ids = await fleet_service.list_active_member_vehicle_ids(
        db_session, fleet_id, principal=principal
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
        telemetry_interval_seconds: Interval to publish.

    Returns:
        This vehicle's result: ``published``, ``skipped`` or ``failed``.

    Side Effects:
        One vehicle lookup through the vehicles public service and one
        device lookup; for an eligible device, one MQTT publish. Writes
        nothing.
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
        await _publish_config(telematic_record, telemetry_interval_seconds)
    except TelematicCommandPublishError as error:
        # Reported, not raised (D9): the other members are still pushed.
        # _publish_config already logged the traceback.
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
