"""Smoke tests for the F-J1 device-health fields on ``TelematicResponse``.

The telemetry and vehicles public services are monkeypatched; nothing here
touches a database.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.monitoring.silence_rule as silence_rule
import app.domains.telematics.service as telematics_service
import app.domains.telemetry.service as telemetry_public_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.telematics.types import TelematicStatus
from app.domains.telemetry.types import VehicleLiveStatusReference
from app.domains.vehicles.types import VehicleReference
from app.libs.common.config import settings
from tests.builders import build_telematic_record, fake_db_session

SILENT_THRESHOLD = timedelta(minutes=settings.TELEMATICS_SILENT_THRESHOLD_MINUTES)


def _patch_live_vehicle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every assigned vehicle resolve as live (not soft-deleted).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    async def live_vehicle(
        db_session: AsyncSession, vehicle_id: UUID
    ) -> VehicleReference:
        return VehicleReference(
            vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
        )

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", live_vehicle
    )


def _patch_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    *,
    last_seen_at: datetime,
    is_online: bool,
    signal_strength_dbm: int | None,
) -> None:
    """Stub the vehicle's last-seen time and live status.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        last_seen_at: Newest telemetry ``received_at`` to report.
        is_online: Online flag the live status carries.
        signal_strength_dbm: Signal strength the live status carries.
    """

    async def last_telemetry_at(db_session: AsyncSession, vehicle_id: UUID) -> datetime:
        return last_seen_at

    async def live_status(
        db_session: AsyncSession, vehicle_id: UUID
    ) -> VehicleLiveStatusReference:
        return VehicleLiveStatusReference(
            vehicle_id=vehicle_id,
            latitude=10.8,
            longitude=106.7,
            recorded_at=last_seen_at,
            received_at=last_seen_at,
            is_online=is_online,
            signal_strength_dbm=signal_strength_dbm,
        )

    monkeypatch.setattr(
        telemetry_public_service, "resolve_last_telemetry_at", last_telemetry_at
    )
    monkeypatch.setattr(
        telemetry_public_service, "resolve_vehicle_live_status", live_status
    )


def _fail_on_telemetry_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any telemetry lookup fail the test.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    async def fail_if_called(db_session: AsyncSession, vehicle_id: UUID) -> None:
        raise AssertionError("telemetry must not be looked up for this device")

    monkeypatch.setattr(
        telemetry_public_service, "resolve_last_telemetry_at", fail_if_called
    )
    monkeypatch.setattr(
        telemetry_public_service, "resolve_vehicle_live_status", fail_if_called
    )


@pytest.mark.asyncio
async def test_build_response_reports_online_device_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mounted device that reported a minute ago is online, not silent (F-J1)."""
    telematic_record = build_telematic_record(uuid4())
    last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    _patch_live_vehicle(monkeypatch)
    _patch_telemetry(
        monkeypatch, last_seen_at=last_seen_at, is_online=True, signal_strength_dbm=-71
    )

    telematic_response = await telematics_service.build_telematic_response(
        fake_db_session(), telematic_record
    )

    assert telematic_response.last_seen_at == last_seen_at
    assert telematic_response.is_online is True
    assert telematic_response.is_silent is False
    assert telematic_response.last_signal_strength_dbm == -71


@pytest.mark.asyncio
async def test_build_response_flags_active_device_silent_past_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ACTIVE device quiet past the monitor's threshold is silent (F-J1)."""
    telematic_record = build_telematic_record(uuid4())
    last_seen_at = datetime.now(timezone.utc) - SILENT_THRESHOLD - timedelta(minutes=10)
    _patch_live_vehicle(monkeypatch)
    _patch_telemetry(
        monkeypatch,
        last_seen_at=last_seen_at,
        is_online=False,
        signal_strength_dbm=None,
    )

    telematic_response = await telematics_service.build_telematic_response(
        fake_db_session(), telematic_record
    )

    assert telematic_response.last_seen_at == last_seen_at
    assert telematic_response.is_online is False
    assert telematic_response.is_silent is True
    assert telematic_response.last_signal_strength_dbm is None


@pytest.mark.asyncio
async def test_build_response_never_flags_maintenance_device_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device out of service is not silent, as in the monitor; last-seen still shows."""
    telematic_record = build_telematic_record(uuid4())
    telematic_record.status = TelematicStatus.MAINTENANCE
    last_seen_at = datetime.now(timezone.utc) - SILENT_THRESHOLD - timedelta(minutes=10)
    _patch_live_vehicle(monkeypatch)
    _patch_telemetry(
        monkeypatch, last_seen_at=last_seen_at, is_online=False, signal_strength_dbm=-90
    )

    telematic_response = await telematics_service.build_telematic_response(
        fake_db_session(), telematic_record
    )

    assert telematic_response.last_seen_at == last_seen_at
    assert telematic_response.is_silent is False
    assert telematic_response.last_signal_strength_dbm == -90


@pytest.mark.asyncio
async def test_build_response_vehicle_never_reported_is_not_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mounted vehicle with no telemetry is a provisioning gap, not silence."""
    telematic_record = build_telematic_record(uuid4())
    _patch_live_vehicle(monkeypatch)

    async def never_reported(db_session: AsyncSession, vehicle_id: UUID) -> None:
        return None

    async def fail_if_called(db_session: AsyncSession, vehicle_id: UUID) -> None:
        raise AssertionError("no live-status lookup for a vehicle that never reported")

    monkeypatch.setattr(
        telemetry_public_service, "resolve_last_telemetry_at", never_reported
    )
    monkeypatch.setattr(
        telemetry_public_service, "resolve_vehicle_live_status", fail_if_called
    )

    telematic_response = await telematics_service.build_telematic_response(
        fake_db_session(), telematic_record
    )

    assert telematic_response.last_seen_at is None
    assert telematic_response.is_online is False
    assert telematic_response.is_silent is False
    assert telematic_response.last_signal_strength_dbm is None


@pytest.mark.asyncio
async def test_build_response_device_of_deleted_vehicle_has_no_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device on a soft-deleted vehicle counts as unmounted (D11): no lookups."""
    telematic_record = build_telematic_record(uuid4())

    async def deleted_vehicle(db_session: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", deleted_vehicle
    )
    _fail_on_telemetry_lookup(monkeypatch)

    telematic_response = await telematics_service.build_telematic_response(
        fake_db_session(), telematic_record
    )

    assert telematic_response.vehicle_vin is None
    assert telematic_response.last_seen_at is None
    assert telematic_response.is_online is False
    assert telematic_response.is_silent is False


@pytest.mark.asyncio
async def test_build_response_unmounted_device_skips_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unassigned device reports null/False health without any lookup."""
    telematic_record = build_telematic_record(uuid4())
    telematic_record.vehicle_id = None
    _fail_on_telemetry_lookup(monkeypatch)

    telematic_response = await telematics_service.build_telematic_response(
        fake_db_session(), telematic_record
    )

    assert telematic_response.last_seen_at is None
    assert telematic_response.is_online is False
    assert telematic_response.is_silent is False
    assert telematic_response.last_signal_strength_dbm is None


def test_silence_rule_boundaries() -> None:
    """Silent from exactly the threshold on; never silent without a report."""
    now = datetime.now(timezone.utc)

    assert silence_rule.calculate_is_device_silent(None, now=now) is False
    assert (
        silence_rule.calculate_is_device_silent(now - SILENT_THRESHOLD, now=now) is True
    )
    assert (
        silence_rule.calculate_is_device_silent(
            now - SILENT_THRESHOLD + timedelta(seconds=1), now=now
        )
        is False
    )
