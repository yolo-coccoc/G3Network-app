"""Smoke tests for the telematics device-health monitor (F-J1, F-J3)."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.notifications.service as notifications_service
import app.domains.telematics.monitoring.device_health_monitor as device_health_monitor
import app.domains.telematics.repository as telematics_repository
import app.domains.telemetry.service as telemetry_service
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.telematics.models import TelematicModel
from app.libs.common.config import settings
from tests.builders import build_telematic_record, fake_db_session


@pytest.mark.asyncio
async def test_check_devices_for_silence_raises_alert_for_newly_silent_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device silent past the threshold with no prior alert gets exactly one (F-J1/F-J3)."""
    vehicle_id = uuid4()
    device = build_telematic_record(vehicle_id)
    now = datetime.now(timezone.utc)
    last_seen_at = now - timedelta(
        minutes=settings.TELEMATICS_SILENT_THRESHOLD_MINUTES + 30
    )
    created_notifications: list[dict[str, object]] = []

    async def active_devices(db_session: AsyncSession) -> list[TelematicModel]:
        return [device]

    async def last_telemetry_at(db: AsyncSession, vid: UUID) -> datetime:
        assert vid == vehicle_id
        return last_seen_at

    async def last_notified_at(db: AsyncSession, **kwargs: object) -> None:
        return None

    async def record_notification(db: AsyncSession, **kwargs: object) -> None:
        created_notifications.append(kwargs)

    monkeypatch.setattr(
        telematics_repository, "list_active_with_vehicle", active_devices
    )
    monkeypatch.setattr(
        telemetry_service, "resolve_last_telemetry_at", last_telemetry_at
    )
    monkeypatch.setattr(
        notifications_service, "resolve_last_notified_at", last_notified_at
    )
    monkeypatch.setattr(
        notifications_service, "create_notification", record_notification
    )

    await device_health_monitor.check_devices_for_silence(fake_db_session())

    assert len(created_notifications) == 1
    call = created_notifications[0]
    assert call["notification_type"] is NotificationType.DEVICE_OFFLINE_ALERT
    assert call["severity"] is NotificationSeverity.WARNING
    assert call["vehicle_id"] == vehicle_id
    payload = cast(dict[str, object], call["payload"])
    assert payload["telematic_serial"] == device.telematic_serial
    assert cast(int, payload["silent_minutes"]) >= (
        settings.TELEMATICS_SILENT_THRESHOLD_MINUTES + 30
    )


@pytest.mark.asyncio
async def test_check_devices_for_silence_skips_device_within_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device that reported recently doesn't alert (F-J1/F-J3)."""
    vehicle_id = uuid4()
    device = build_telematic_record(vehicle_id)
    last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    async def active_devices(db_session: AsyncSession) -> list[TelematicModel]:
        return [device]

    async def last_telemetry_at(db: AsyncSession, vid: UUID) -> datetime:
        return last_seen_at

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("create_notification should not be called")

    monkeypatch.setattr(
        telematics_repository, "list_active_with_vehicle", active_devices
    )
    monkeypatch.setattr(
        telemetry_service, "resolve_last_telemetry_at", last_telemetry_at
    )
    monkeypatch.setattr(notifications_service, "create_notification", fail_if_called)

    await device_health_monitor.check_devices_for_silence(fake_db_session())


@pytest.mark.asyncio
async def test_check_devices_for_silence_skips_device_never_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device that has never sent telemetry is skipped, not treated as silent (F-J1/F-J3)."""
    vehicle_id = uuid4()
    device = build_telematic_record(vehicle_id)

    async def active_devices(db_session: AsyncSession) -> list[TelematicModel]:
        return [device]

    async def no_telemetry(db: AsyncSession, vid: UUID) -> None:
        return None

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("create_notification should not be called")

    monkeypatch.setattr(
        telematics_repository, "list_active_with_vehicle", active_devices
    )
    monkeypatch.setattr(telemetry_service, "resolve_last_telemetry_at", no_telemetry)
    monkeypatch.setattr(notifications_service, "create_notification", fail_if_called)

    await device_health_monitor.check_devices_for_silence(fake_db_session())


@pytest.mark.asyncio
async def test_check_devices_for_silence_suppresses_duplicate_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device already alerted for this exact silence episode doesn't alert again (F-J1/F-J3)."""
    vehicle_id = uuid4()
    device = build_telematic_record(vehicle_id)
    now = datetime.now(timezone.utc)
    last_seen_at = now - timedelta(
        minutes=settings.TELEMATICS_SILENT_THRESHOLD_MINUTES + 30
    )
    # The prior alert is newer than last_seen_at - the device hasn't
    # reported anything new since that alert was raised.
    prior_alert_at = last_seen_at + timedelta(minutes=1)

    async def active_devices(db_session: AsyncSession) -> list[TelematicModel]:
        return [device]

    async def last_telemetry_at(db: AsyncSession, vid: UUID) -> datetime:
        return last_seen_at

    async def last_notified_at(db: AsyncSession, **kwargs: object) -> datetime:
        return prior_alert_at

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("create_notification should not be called")

    monkeypatch.setattr(
        telematics_repository, "list_active_with_vehicle", active_devices
    )
    monkeypatch.setattr(
        telemetry_service, "resolve_last_telemetry_at", last_telemetry_at
    )
    monkeypatch.setattr(
        notifications_service, "resolve_last_notified_at", last_notified_at
    )
    monkeypatch.setattr(notifications_service, "create_notification", fail_if_called)

    await device_health_monitor.check_devices_for_silence(fake_db_session())


@pytest.mark.asyncio
async def test_check_devices_for_silence_realerts_after_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device that recovered then went silent again alerts a second time (F-J1/F-J3)."""
    vehicle_id = uuid4()
    device = build_telematic_record(vehicle_id)
    now = datetime.now(timezone.utc)
    last_seen_at = now - timedelta(
        minutes=settings.TELEMATICS_SILENT_THRESHOLD_MINUTES + 30
    )
    # The prior alert predates last_seen_at - the device reported again
    # (moving last_seen_at forward) after that alert, so a new silence
    # episode is eligible to alert.
    prior_alert_at = last_seen_at - timedelta(hours=1)
    created_notifications: list[dict[str, object]] = []

    async def active_devices(db_session: AsyncSession) -> list[TelematicModel]:
        return [device]

    async def last_telemetry_at(db: AsyncSession, vid: UUID) -> datetime:
        return last_seen_at

    async def last_notified_at(db: AsyncSession, **kwargs: object) -> datetime:
        return prior_alert_at

    async def record_notification(db: AsyncSession, **kwargs: object) -> None:
        created_notifications.append(kwargs)

    monkeypatch.setattr(
        telematics_repository, "list_active_with_vehicle", active_devices
    )
    monkeypatch.setattr(
        telemetry_service, "resolve_last_telemetry_at", last_telemetry_at
    )
    monkeypatch.setattr(
        notifications_service, "resolve_last_notified_at", last_notified_at
    )
    monkeypatch.setattr(
        notifications_service, "create_notification", record_notification
    )

    await device_health_monitor.check_devices_for_silence(fake_db_session())

    assert len(created_notifications) == 1


@pytest.mark.asyncio
async def test_run_monitor_exits_immediately_when_stop_event_already_set() -> None:
    """run_monitor() returns without running a tick if already told to stop (F-J1/F-J3)."""
    stop_event = asyncio.Event()
    stop_event.set()

    # No monkeypatching of check_devices_for_silence/list_active_with_vehicle -
    # if run_monitor tried to run a tick, it would hit the real (unmocked)
    # database and fail/hang, so a clean return proves no tick ran.
    await device_health_monitor.run_monitor(stop_event)
