"""Periodic check for telematic devices that have gone silent (F-J1, F-J3 partial).

This is the first periodic (as opposed to event-driven) background process
in this backend - every other worker reacts to an incoming MQTT message or
WebSocket frame; "has this device stopped sending anything?" has no message
to react to, so it needs a timer instead.

Scope (DEV-05): a silent-device notification, once per silence episode,
written for the organization that owns the truck; the notifications domain
routes it to the administrators and fleet managers (NTF-06, its routing table). The device-health dashboard (DEV-04) is
read-time and lives in ``telematics.service``. Not delivered (DEV-06): telling
a sudden power loss from an ordinary signal loss - the device contract
(mqtt-spec.md 2.2) has no power-loss or tamper signal, so there is nothing
to tell them apart with (see docs/decisions/deferred.md 50-51).
"""

import asyncio
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.notifications.service as notifications_service
import app.domains.telematics.monitoring.silence_rule as silence_rule
import app.domains.telematics.repository as telematics_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.telematics.models import TelematicModel
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.db.session import async_session_factory

logger = logging.getLogger(__name__)


async def run_monitor(stop_event: asyncio.Event) -> None:
    """Run device-health ticks on an interval until told to stop.

    Args:
        stop_event: Event set by the entrypoint on SIGINT/SIGTERM.

    Side Effects:
        Sleeps via a cancellable wait (so a shutdown signal during the wait
        is honored immediately) and runs one tick per elapsed interval. A
        signal arriving mid-tick is only honored once that tick finishes -
        acceptable given each tick is a bounded, one-query-per-device sweep.
        Any exception raised by a tick propagates out of this function
        (fail-fast, matching telemetry ingestion's policy) rather than being
        swallowed and retried.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.TELEMATICS_HEALTH_CHECK_INTERVAL_SECONDS,
            )
            return  # stop_event was set - exit without another tick
        except TimeoutError:
            pass  # interval elapsed - time to check
        await run_health_check_tick()


async def run_health_check_tick() -> None:
    """Open one transaction for a sweep and run it.

    Side Effects:
        Opens one atomic transaction for the whole sweep
        (``async_session_factory.begin()``), matching this repo's default
        of one business operation per transaction, then delegates to
        ``check_devices_for_silence`` for the actual logic. This split
        mirrors ``process_message(db, ...)``/the OCPP handlers elsewhere in
        this repo: the transaction-opening wrapper isn't unit tested
        directly, the session-accepting function it calls is.
    """
    async with async_session_factory.begin() as db_session:
        await check_devices_for_silence(db_session)


async def check_devices_for_silence(db_session: AsyncSession) -> None:
    """Run one sweep of active devices, alerting on newly-silent ones.

    Args:
        db_session: Session whose transaction is owned by the caller
            (``run_health_check_tick`` in production; a test's fake session
            when unit tested directly).

    Rule (D11 of the happy-path planner):
        A device whose assigned vehicle is soft-deleted is skipped - the
        vehicle is gone, so its device going quiet is expected, not an
        incident. The device row keeps its ``vehicle_id`` after the vehicle
        is deleted, so the vehicle's liveness is checked through the
        vehicles public service.

    Metrics (logged once per tick):
        ``checked`` - devices returned by ``list_active_with_vehicle``;
        ``alerted`` - notifications written this tick; ``skipped`` -
        devices not evaluated for silence, either because their vehicle is
        soft-deleted or because they have never reported telemetry.

    Side Effects:
        Loops devices one at a time (one query per device for the vehicle
        check, one for the last-seen check, one for the dedup check)
        rather than a
        batched/grouped query - this repo's convention is the simple
        per-item version first, batched only once a benchmark shows a real
        need; that applies to a periodic sweep the same as it does to a
        request path. May write one notification row per newly-silent
        device; does not commit.
    """
    devices = await telematics_repository.list_active_with_vehicle(db_session)
    now = utc_now()
    checked_count = 0
    alerted_count = 0
    skipped_count = 0

    for device in devices:
        checked_count += 1
        # list_active_with_vehicle() already filters vehicle_id IS NOT
        # NULL; the assertion documents that invariant for mypy.
        vehicle_id = device.vehicle_id
        assert vehicle_id is not None, "query filters out unassigned devices"

        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, vehicle_id
        )
        if vehicle_reference is None:
            skipped_count += 1
            continue

        last_seen_at = await telemetry_service.resolve_last_telemetry_at(
            db_session, vehicle_id
        )
        if last_seen_at is None:
            # Never reported - a provisioning gap (F-F2), not silence;
            # there's no anchor to measure silence duration against.
            skipped_count += 1
            continue

        # The same rule as TelematicResponse.is_silent (one shared helper).
        if not silence_rule.calculate_is_device_silent(last_seen_at, now=now):
            continue

        last_notified_at = await notifications_service.resolve_last_notified_at(
            db_session,
            vehicle_id=vehicle_id,
            notification_type=NotificationType.DEVICE_OFFLINE_ALERT,
        )
        if last_notified_at is not None and last_notified_at > last_seen_at:
            # Already alerted for this exact silence episode - the device
            # hasn't reported anything new since that alert.
            continue

        await _raise_device_offline_alert(
            db_session,
            device=device,
            organization_id=vehicle_reference.organization_id,
            vehicle_id=vehicle_id,
            last_seen_at=last_seen_at,
            now=now,
        )
        alerted_count += 1

    logger.info(
        "device health check tick completed",
        extra={
            "checked": checked_count,
            "alerted": alerted_count,
            "skipped": skipped_count,
        },
    )


async def _raise_device_offline_alert(
    db_session: AsyncSession,
    *,
    device: TelematicModel,
    organization_id: UUID,
    vehicle_id: UUID,
    last_seen_at: datetime,
    now: datetime,
) -> None:
    """Raise a device-offline notification (F-J1, F-J3 partial).

    Args:
        db_session: Session whose transaction is owned by the monitor tick.
        device: The telematic device that's gone silent.
        organization_id: The assigned vehicle's owner now (written on the
            alert once, DM-24 case C).
        vehicle_id: The device's assigned vehicle.
        last_seen_at: The device's last telemetry receive time.
        now: The tick's current time, for computing silence duration.

    Side Effects:
        Writes one notification row into the session; the notifications
        service adds its recipients (the organization's ORG_ADMIN and
        FLEET_MANAGER members who may see the truck) and sends the push and
        e-mail; does not commit.
    """
    silent_minutes = int((now - last_seen_at).total_seconds() // 60)
    payload: dict[str, object] = {
        "telematic_id": str(device.telematic_id),
        "telematic_serial": device.telematic_serial,
        "last_seen_at": last_seen_at.isoformat(),
        "silent_minutes": silent_minutes,
        "threshold_minutes": settings.TELEMATICS_SILENT_THRESHOLD_MINUTES,
    }
    notification_reference = await notifications_service.create_notification(
        db_session,
        organization_id=organization_id,
        notification_type=NotificationType.DEVICE_OFFLINE_ALERT,
        severity=NotificationSeverity.WARNING,
        vehicle_id=vehicle_id,
        title=f"Device {device.telematic_serial} offline",
        body=(
            f"No telemetry received from {device.telematic_serial} for "
            f"{silent_minutes} minutes (threshold "
            f"{settings.TELEMATICS_SILENT_THRESHOLD_MINUTES})."
        ),
        payload=payload,
        subject_type="TELEMATIC",
        subject_id=device.telematic_id,
    )
    logger.info(
        "device offline alert raised",
        extra={
            "vehicle_id": str(vehicle_id),
            "telematic_id": str(device.telematic_id),
            "silent_minutes": silent_minutes,
            "notification_id": notification_reference.notification_id,
        },
    )
