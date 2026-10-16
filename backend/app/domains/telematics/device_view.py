"""A device's live view: the truck it is mounted on and its health (DEV-04, TX-11).

Loads what a device response and the health endpoints both show: the
mounted truck, the newest telemetry and status report, and the health state
derived from them by ``health_rule.py`` and ``silence_rule.py`` (computed at
read time, never stored). Internal module: other domains use ``service.py``.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.silence_rule as silence_rule
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.domains.telematics.models import TelematicModel, TelematicStatusReportModel
from app.domains.telematics.types import (
    TelematicDeviceHealth,
    TelematicStatus,
)
from app.libs.common.clock import utc_now

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


@dataclass(frozen=True)
class _DeviceView:
    """What is read about a device besides its own row, in one place.

    Attributes:
        vehicle_vin: VIN of the live vehicle it is mounted on, else `None`.
        is_mounted: Whether the device is mounted on a live vehicle.
        device_health: Read-time health from the vehicle's telemetry.
        latest_status_report: The newest status report, or `None`.
    """

    vehicle_vin: str | None
    is_mounted: bool
    device_health: TelematicDeviceHealth
    latest_status_report: TelematicStatusReportModel | None


async def load_device_view(
    db_session: AsyncSession, telematic_record: TelematicModel
) -> _DeviceView:
    """Read the vehicle, health and newest status report of a device.

    A device assigned to a soft-deleted vehicle is treated as not mounted
    (D11, as in ``resolve_mapping_by_serial``): no VIN and no health.

    Args:
        db_session: Current database session.
        telematic_record: Device record, flushed or loaded in this session.

    Returns:
        The device's view.

    Side Effects:
        Read-only: one status-report lookup, one vehicle lookup when a
        vehicle is assigned, then the health lookups of
        ``_resolve_device_health`` when it is live.
    """
    latest_status_report = await telematics_repository.find_latest_status_report(
        db_session, telematic_record.telematic_id
    )
    vehicle_vin = None
    is_mounted = False
    device_health = _UNMOUNTED_DEVICE_HEALTH
    if telematic_record.vehicle_id:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session,
            telematic_record.vehicle_id,
        )
        if vehicle_reference is not None:
            vehicle_vin = vehicle_reference.vin
            is_mounted = True
            device_health = await _resolve_device_health(
                db_session, telematic_record, vehicle_reference.vehicle_id
            )
    return _DeviceView(
        vehicle_vin=vehicle_vin,
        is_mounted=is_mounted,
        device_health=device_health,
        latest_status_report=latest_status_report,
    )
