"""Smoke tests for the F-J2 fleet-wide config push (planner D9).

The fleet and vehicles public services, the telematics repository and the
MQTT publisher are monkeypatched; nothing here touches a database or broker.
"""

from uuid import UUID, uuid4

import pytest
from aiomqtt import MqttError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.service as fleet_public_service
import app.domains.telematics.commands.mqtt_publisher as telematics_mqtt_publisher
import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.service as telematics_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.fleet.exceptions import FleetNotFoundError
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import TelematicConfigPushRequest
from app.domains.telematics.types import TelematicConfigPushOutcome, TelematicStatus
from app.domains.vehicles.types import VehicleReference
from app.libs.common.errors import NotFoundError
from tests.builders import build_telematic_record, fake_db_session
from tests.principals import build_internal_principal


@pytest.mark.asyncio
async def test_push_fleet_config_reports_published_skipped_and_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each member gets one result in fleet order; one failure doesn't stop the rest.

    Members: a soft-deleted vehicle, a vehicle without a device, a
    INACTIVE device, an unreachable device, then a healthy device (D9).
    """
    fleet_id = uuid4()
    member_vehicle_ids = [uuid4() for _ in range(5)]
    deleted_vehicle_id = member_vehicle_ids[0]
    inactive_device = build_telematic_record(member_vehicle_ids[2])
    inactive_device.status = TelematicStatus.INACTIVE
    unreachable_device = build_telematic_record(member_vehicle_ids[3])
    unreachable_device.telematic_serial = "TBOX-UNREACHABLE"
    healthy_device = build_telematic_record(member_vehicle_ids[4])
    healthy_device.telematic_serial = "TBOX-HEALTHY"
    # member_vehicle_ids[1] carries no device at all.
    devices_by_vehicle_id = {
        member_vehicle_ids[2]: inactive_device,
        member_vehicle_ids[3]: unreachable_device,
        member_vehicle_ids[4]: healthy_device,
    }
    published_serials: list[str] = []

    async def member_vehicle_ids_of(
        db_session: AsyncSession, value: UUID, **_scope: object
    ) -> list[UUID]:
        assert value == fleet_id
        return member_vehicle_ids

    async def resolve_vehicle(
        db_session: AsyncSession, vehicle_id: UUID
    ) -> VehicleReference | None:
        if vehicle_id == deleted_vehicle_id:
            return None
        return VehicleReference(
            organization_id=uuid4(),
            vehicle_id=vehicle_id,
            vin="1HGBH41JXMN109186",
            battery_capacity_kwh=None,
        )

    async def find_device(
        db_session: AsyncSession, vehicle_id: UUID
    ) -> TelematicModel | None:
        return devices_by_vehicle_id.get(vehicle_id)

    async def publish(serial: str, payload: dict[str, object]) -> None:
        if serial == unreachable_device.telematic_serial:
            raise MqttError("broker unreachable")
        published_serials.append(serial)

    monkeypatch.setattr(
        fleet_public_service, "list_active_member_vehicle_ids", member_vehicle_ids_of
    )
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_vehicle
    )
    monkeypatch.setattr(telematics_repository, "find_by_vehicle_id", find_device)
    monkeypatch.setattr(telematics_mqtt_publisher, "publish_device_command", publish)

    fleet_config_push_response = await telematics_service.push_fleet_config(
        fake_db_session(),
        fleet_id,
        TelematicConfigPushRequest(telemetry_interval_seconds=60),
        principal=build_internal_principal(),
    )

    assert fleet_config_push_response.fleet_id == fleet_id
    assert fleet_config_push_response.published_count == 1
    assert fleet_config_push_response.skipped_count == 3
    assert fleet_config_push_response.failed_count == 1
    results = fleet_config_push_response.results
    assert [result.vehicle_id for result in results] == member_vehicle_ids
    assert [result.outcome for result in results] == [
        TelematicConfigPushOutcome.SKIPPED,
        TelematicConfigPushOutcome.SKIPPED,
        TelematicConfigPushOutcome.SKIPPED,
        TelematicConfigPushOutcome.FAILED,
        TelematicConfigPushOutcome.PUBLISHED,
    ]
    assert results[0].reason == "vehicle is soft-deleted"
    assert results[0].telematic_id is None
    assert results[1].reason == "no device"
    assert results[1].telematic_serial is None
    assert results[2].telematic_id == inactive_device.telematic_id
    assert results[2].reason is not None and "INACTIVE" in results[2].reason
    assert results[3].telematic_serial == unreachable_device.telematic_serial
    assert results[3].reason is not None and "TBOX-UNREACHABLE" in results[3].reason
    assert results[4].telematic_id == healthy_device.telematic_id
    assert results[4].reason is None
    # Only the eligible, reachable device is published to; nothing is stored.
    assert published_serials == [healthy_device.telematic_serial]


@pytest.mark.asyncio
async def test_push_fleet_config_with_no_members_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty fleet answers zero counts and no results."""

    async def no_members(
        db_session: AsyncSession, fleet_id: UUID, **_scope: object
    ) -> list[UUID]:
        return []

    monkeypatch.setattr(
        fleet_public_service, "list_active_member_vehicle_ids", no_members
    )

    fleet_config_push_response = await telematics_service.push_fleet_config(
        fake_db_session(),
        uuid4(),
        TelematicConfigPushRequest(telemetry_interval_seconds=60),
        principal=build_internal_principal(),
    )

    assert fleet_config_push_response.published_count == 0
    assert fleet_config_push_response.skipped_count == 0
    assert fleet_config_push_response.failed_count == 0
    assert fleet_config_push_response.results == []


@pytest.mark.asyncio
async def test_push_fleet_config_unknown_fleet_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown fleet propagates the fleet service's 404-class error."""

    async def unknown_fleet(
        db_session: AsyncSession, fleet_id: UUID, **_scope: object
    ) -> list[UUID]:
        raise FleetNotFoundError("Fleet not found")

    async def fail_if_called(serial: str, payload: dict[str, object]) -> None:
        raise AssertionError("nothing is published for an unknown fleet")

    monkeypatch.setattr(
        fleet_public_service, "list_active_member_vehicle_ids", unknown_fleet
    )
    monkeypatch.setattr(
        telematics_mqtt_publisher, "publish_device_command", fail_if_called
    )

    with pytest.raises(FleetNotFoundError) as error_info:
        await telematics_service.push_fleet_config(
            fake_db_session(),
            uuid4(),
            TelematicConfigPushRequest(telemetry_interval_seconds=60),
            principal=build_internal_principal(),
        )

    assert isinstance(error_info.value, NotFoundError)
