"""Smoke tests for the computed vehicle activation (VEH-05, VH-06).

The other domains' public services and the first-sample query are
monkeypatched; nothing here touches a database.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_service
import app.domains.telemetry.activation as telemetry_activation
import app.domains.telemetry.repository as telemetry_repository
import app.domains.vehicles.service as vehicles_public_service
from app.domains.telematics.types import TelematicMountedDevice, TelematicStatus
from app.domains.telemetry.types import VehicleActivationStatus
from app.domains.vehicles.types import VehicleStatus, VehicleSummary
from tests.builders import fake_db_session

HANDOVER_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _summary() -> VehicleSummary:
    """Build a live truck summary."""
    return VehicleSummary(
        vehicle_id=uuid4(),
        vin="1HGBH41JXMN109186",
        license_plate="51C-123.45",
        status=VehicleStatus.ACTIVE,
    )


def _patch_lookups(
    monkeypatch: pytest.MonkeyPatch,
    *,
    device: TelematicMountedDevice | None,
    first_data_at: datetime | None,
) -> list[datetime]:
    """Stub the handover, the mounted device and the first sample.

    Returns:
        A list that receives the ``since`` value of every first-sample query.
    """
    since_values: list[datetime] = []

    async def handover(db_session: AsyncSession, vehicle_id: UUID) -> datetime:
        return HANDOVER_AT

    async def mounted(
        db: AsyncSession, vehicle_id: UUID
    ) -> TelematicMountedDevice | None:
        return device

    async def first_sample(
        db: AsyncSession, vehicle_id: UUID, telematic_id: UUID, since: datetime
    ) -> datetime | None:
        since_values.append(since)
        return first_data_at

    monkeypatch.setattr(vehicles_public_service, "resolve_first_handover_at", handover)
    monkeypatch.setattr(
        telematics_service, "resolve_mounted_device_by_vehicle_id", mounted
    )
    monkeypatch.setattr(telemetry_repository, "find_first_received_at", first_sample)
    return since_values


def _device(mounted_at: datetime) -> TelematicMountedDevice:
    """Build a mounted ACTIVE device."""
    return TelematicMountedDevice(
        telematic_id=uuid4(),
        telematic_serial="TBOX-1",
        status=TelematicStatus.ACTIVE,
        mounted_at=mounted_at,
    )


@pytest.mark.asyncio
async def test_activation_is_no_device_when_nothing_is_mounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truck whose T-Box was removed shows NO_DEVICE again."""
    _patch_lookups(monkeypatch, device=None, first_data_at=None)

    activation = await telemetry_activation.build_vehicle_activation(
        fake_db_session(), _summary()
    )

    assert activation.activation_status is VehicleActivationStatus.NO_DEVICE
    assert activation.handover_at == HANDOVER_AT
    assert activation.telematic_id is None


@pytest.mark.asyncio
async def test_activation_waits_for_data_then_counts_from_the_later_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mounted device with no sample is AWAITING_DATA; samples count from mount or handover."""
    mounted_at = HANDOVER_AT + timedelta(days=3)
    since_values = _patch_lookups(
        monkeypatch, device=_device(mounted_at), first_data_at=None
    )

    waiting = await telemetry_activation.build_vehicle_activation(
        fake_db_session(), _summary()
    )

    assert waiting.activation_status is VehicleActivationStatus.AWAITING_DATA
    assert since_values == [mounted_at]


@pytest.mark.asyncio
async def test_activation_is_activated_with_hours_since_handover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first sample after mounting activates the truck; hours run from the handover."""
    mounted_at = HANDOVER_AT - timedelta(days=1)
    _patch_lookups(
        monkeypatch,
        device=_device(mounted_at),
        first_data_at=HANDOVER_AT + timedelta(hours=5),
    )

    activation = await telemetry_activation.build_vehicle_activation(
        fake_db_session(), _summary()
    )

    assert activation.activation_status is VehicleActivationStatus.ACTIVATED
    assert activation.activation_hours == 5.0


@pytest.mark.asyncio
async def test_activation_summary_gives_the_success_rate_over_trucks_with_a_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rate = activated / (awaiting + activated); trucks without a device are not attempts."""
    mounted_at = HANDOVER_AT
    activations = []
    for device, first_data_at in (
        (None, None),
        (_device(mounted_at), None),
        (_device(mounted_at), HANDOVER_AT + timedelta(hours=2)),
        (_device(mounted_at), HANDOVER_AT + timedelta(hours=4)),
    ):
        _patch_lookups(monkeypatch, device=device, first_data_at=first_data_at)
        activations.append(
            await telemetry_activation.build_vehicle_activation(
                fake_db_session(), _summary()
            )
        )

    summary = telemetry_activation.summarize_activations(activations)

    assert summary.total_count == 4
    assert summary.no_device_count == 1
    assert summary.awaiting_data_count == 1
    assert summary.activated_count == 2
    assert summary.activation_rate_percent == 66.7
    assert summary.average_activation_hours == 3.0


def test_activation_hours_ignores_data_that_came_before_the_handover() -> None:
    """A test drive before the handover does not count as activation time."""
    assert (
        telemetry_activation.calculate_activation_hours(
            HANDOVER_AT, HANDOVER_AT - timedelta(hours=1)
        )
        is None
    )
