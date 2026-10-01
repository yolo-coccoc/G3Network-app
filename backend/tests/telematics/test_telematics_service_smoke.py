"""Smoke tests for the telematics service and the F-J2 config-push publisher."""

from datetime import datetime, timezone
from typing import cast
from uuid import UUID, uuid4

import pytest
from aiomqtt import MqttError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.commands.mqtt_publisher as telematics_mqtt_publisher
import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.service as telematics_service
import app.domains.vehicles.service as vehicles_public_service
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
    TelematicCreateRequest,
    TelematicUpdateRequest,
)
from app.domains.telematics.types import TelematicStatus, TelematicVehicleMapping
from app.domains.vehicles.types import (
    VehicleReference,
)
from app.libs.common.errors import NotFoundError
from tests.builders import build_telematic_record, fake_db_session

UNKNOWN_VIN = "1HGBH41JXMN999999"


@pytest.mark.asyncio
async def test_telematic_service_resolves_vehicle_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The telematic service resolves the VIN via the vehicles public service."""
    vehicle_id = uuid4()
    record = build_telematic_record(vehicle_id)
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )

    async def no_existing_serial(db: AsyncSession, telematic_serial: str) -> None:
        return None

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return reference

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def insert_telematic(
        db: AsyncSession, values: dict[str, object]
    ) -> TelematicModel:
        return record

    async def no_assigned_vehicle(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    async def mark_assigned(db_session: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(telematics_repository, "find_by_serial", no_existing_serial)
    monkeypatch.setattr(
        telematics_repository, "find_by_vehicle_id", no_assigned_vehicle
    )
    monkeypatch.setattr(telematics_repository, "insert", insert_telematic)
    monkeypatch.setattr(
        vehicles_public_service,
        "resolve_vehicle_reference_by_vin",
        resolve_vin,
    )
    monkeypatch.setattr(
        vehicles_public_service,
        "resolve_vehicle_reference_by_id",
        resolve_id,
    )
    # F-F2's activation hook fires since a vehicle_id resolves; mocked out
    # since this test is about VIN resolution, not activation.
    monkeypatch.setattr(vehicles_public_service, "mark_device_assigned", mark_assigned)

    response = await telematics_service.create_telematic(
        fake_db_session(),
        TelematicCreateRequest(
            telematic_serial=record.telematic_serial,
            vehicle_vin=reference.vin,
            status=record.status,
            firmware_version=record.firmware_version,
        ),
    )

    assert response.vehicle_id == vehicle_id
    assert response.vehicle_vin == reference.vin


@pytest.mark.asyncio
async def test_create_telematic_rejects_vehicle_already_assigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vehicle that already carries a live device can't get a second one."""
    vehicle_id = uuid4()
    reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )

    async def no_existing_serial(db: AsyncSession, telematic_serial: str) -> None:
        return None

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return reference

    async def assigned_device(db: AsyncSession, value: UUID) -> TelematicModel:
        assert value == vehicle_id
        return build_telematic_record(vehicle_id)

    async def fail_if_called(db: AsyncSession, values: dict[str, object]) -> None:
        raise AssertionError("insert must not run for an already-assigned vehicle")

    monkeypatch.setattr(telematics_repository, "find_by_serial", no_existing_serial)
    monkeypatch.setattr(telematics_repository, "find_by_vehicle_id", assigned_device)
    monkeypatch.setattr(telematics_repository, "insert", fail_if_called)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )

    with pytest.raises(TelematicConflictError):
        await telematics_service.create_telematic(
            fake_db_session(),
            TelematicCreateRequest(
                telematic_serial="TBOX-TEST-002",
                vehicle_vin=reference.vin,
                firmware_version=None,
            ),
        )


@pytest.mark.asyncio
async def test_build_telematic_response_handles_never_configured_device() -> None:
    """A device that never had a config pushed builds with null F-J2 fields.

    Regression for the old ``__dict__`` spread, which needed the create path
    to pre-set ``telemetry_interval_seconds``/``config_pushed_at`` to None.
    """
    now = datetime.now(timezone.utc)
    telematic_record = TelematicModel(
        telematic_id=uuid4(),
        telematic_serial="TBOX-TEST-003",
        vehicle_id=None,
        status=TelematicStatus.ACTIVE,
        firmware_version=None,
        created_at=now,
        updated_at=now,
    )

    telematic_response = await telematics_service.build_telematic_response(
        fake_db_session(), telematic_record
    )

    assert telematic_response.vehicle_vin is None
    assert telematic_response.telemetry_interval_seconds is None
    assert telematic_response.config_pushed_at is None


@pytest.mark.asyncio
async def test_list_telematics_normalizes_page_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_telematics() clamps page/page_size and passes the offset as a keyword."""
    list_arguments: dict[str, object] = {}

    async def list_all(
        db_session: AsyncSession,
        *,
        offset: int,
        limit: int,
        status_filter: TelematicStatus | None = None,
    ) -> list[TelematicModel]:
        list_arguments.update(offset=offset, limit=limit, status_filter=status_filter)
        return []

    async def count(
        db_session: AsyncSession, status_filter: TelematicStatus | None = None
    ) -> int:
        return 0

    monkeypatch.setattr(telematics_repository, "list_all", list_all)
    monkeypatch.setattr(telematics_repository, "count", count)

    telematic_list_response = await telematics_service.list_telematics(
        fake_db_session(),
        page=0,
        page_size=0,
        status_filter=TelematicStatus.MAINTENANCE,
    )

    assert list_arguments == {
        "offset": 0,
        "limit": 1,
        "status_filter": TelematicStatus.MAINTENANCE,
    }
    assert telematic_list_response.page == 1
    assert telematic_list_response.page_size == 1


def test_build_command_topic_uses_device_serial() -> None:
    """The command topic matches mqtt-spec.md 2.3's template verbatim."""
    topic = telematics_mqtt_publisher.build_command_topic("TBOX-VN-000123")

    assert topic == "g3network/telematics/TBOX-VN-000123/command"


def test_set_telemetry_interval_payload_matches_mqtt_contract() -> None:
    """The payload has exactly the keys mqtt-spec.md 2.3 documents."""
    issued_at = datetime(2026, 9, 17, 10, 30, tzinfo=timezone.utc)

    payload = telematics_mqtt_publisher.build_set_telemetry_interval_payload(
        60, issued_at=issued_at
    )

    assert payload == {
        "command": "set_telemetry_interval",
        "telemetry_interval_seconds": 60,
        "timestamp": issued_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_push_telematic_config_publishes_and_records_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful push publishes the command and persists the new interval."""
    vehicle_id = uuid4()
    record = build_telematic_record(vehicle_id)
    published: dict[str, object] = {}
    updated: dict[str, object] = {}

    async def get_by_id(db: AsyncSession, telematic_id: UUID) -> TelematicModel:
        return record

    async def publish(serial: str, payload: dict[str, object]) -> None:
        published["serial"] = serial
        published["payload"] = payload

    async def update_fields(
        db: AsyncSession, telematic_record: TelematicModel, values: dict[str, object]
    ) -> TelematicModel:
        updated.update(values)
        return telematic_record

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(telematics_mqtt_publisher, "publish_device_command", publish)
    monkeypatch.setattr(telematics_repository, "update_fields", update_fields)

    response = await telematics_service.push_telematic_config(
        fake_db_session(),
        record.telematic_id,
        TelematicConfigPushRequest(telemetry_interval_seconds=60),
    )

    assert published["serial"] == record.telematic_serial
    payload = cast(dict[str, object], published["payload"])
    assert payload["telemetry_interval_seconds"] == 60
    assert updated["telemetry_interval_seconds"] == 60
    # The DB row and the wire message must agree on the push timestamp.
    config_pushed_at = cast(datetime, updated["config_pushed_at"])
    assert config_pushed_at.isoformat() == payload["timestamp"]
    assert response.telemetry_interval_seconds == 60
    assert response.command_topic == (
        f"g3network/telematics/{record.telematic_serial}/command"
    )


@pytest.mark.asyncio
async def test_push_telematic_config_rejects_unknown_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown or soft-deleted device 404s before any publish is attempted."""

    async def get_by_id(db: AsyncSession, telematic_id: UUID) -> None:
        return None

    async def fail_if_called(serial: str, payload: dict[str, object]) -> None:
        raise AssertionError("publish must not be attempted for an unknown device")

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        telematics_mqtt_publisher, "publish_device_command", fail_if_called
    )

    with pytest.raises(TelematicNotFoundError):
        await telematics_service.push_telematic_config(
            fake_db_session(),
            uuid4(),
            TelematicConfigPushRequest(telemetry_interval_seconds=60),
        )


@pytest.mark.asyncio
async def test_push_telematic_config_rejects_inactive_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An INACTIVE device is refused before any publish is attempted."""
    record = build_telematic_record(uuid4())
    record.status = TelematicStatus.INACTIVE

    async def get_by_id(db: AsyncSession, telematic_id: UUID) -> TelematicModel:
        return record

    async def fail_if_called(serial: str, payload: dict[str, object]) -> None:
        raise AssertionError("publish must not be attempted for an INACTIVE device")

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        telematics_mqtt_publisher, "publish_device_command", fail_if_called
    )

    with pytest.raises(TelematicNotConfigurableError):
        await telematics_service.push_telematic_config(
            fake_db_session(),
            record.telematic_id,
            TelematicConfigPushRequest(telemetry_interval_seconds=60),
        )


@pytest.mark.asyncio
async def test_push_telematic_config_does_not_record_when_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed publish leaves the database untouched (fail-closed)."""
    record = build_telematic_record(uuid4())

    async def get_by_id(db: AsyncSession, telematic_id: UUID) -> TelematicModel:
        return record

    async def raise_mqtt_error(serial: str, payload: dict[str, object]) -> None:
        raise MqttError("broker unreachable")

    async def fail_if_called(
        db: AsyncSession, telematic_record: TelematicModel, values: dict[str, object]
    ) -> None:
        raise AssertionError("update_fields must not run when the publish failed")

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        telematics_mqtt_publisher, "publish_device_command", raise_mqtt_error
    )
    monkeypatch.setattr(telematics_repository, "update_fields", fail_if_called)

    with pytest.raises(TelematicCommandPublishError):
        await telematics_service.push_telematic_config(
            fake_db_session(),
            record.telematic_id,
            TelematicConfigPushRequest(telemetry_interval_seconds=60),
        )


@pytest.mark.asyncio
async def test_create_telematic_rejects_unknown_vehicle_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A VIN matching no live vehicle fails the create with a 404-class error (D10)."""

    async def no_existing_serial(db: AsyncSession, telematic_serial: str) -> None:
        return None

    async def no_vehicle(db: AsyncSession, vin: str) -> None:
        return None

    async def fail_if_called(db: AsyncSession, values: dict[str, object]) -> None:
        raise AssertionError("insert must not run for an unknown VIN")

    monkeypatch.setattr(telematics_repository, "find_by_serial", no_existing_serial)
    monkeypatch.setattr(telematics_repository, "insert", fail_if_called)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", no_vehicle
    )

    with pytest.raises(TelematicVehicleNotFoundError) as error_info:
        await telematics_service.create_telematic(
            fake_db_session(),
            TelematicCreateRequest(
                telematic_serial="TBOX-TEST-004",
                vehicle_vin=UNKNOWN_VIN,
                firmware_version=None,
            ),
        )

    assert isinstance(error_info.value, NotFoundError)


def _patch_update_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    telematic_record: TelematicModel,
    updated_values: dict[str, object],
) -> None:
    """Stub the repository reads/writes an update_telematic call goes through.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        telematic_record: Device returned by ``get_by_id``.
        updated_values: Collects the values passed to ``update_fields``.
    """

    async def get_by_id(db: AsyncSession, telematic_id: UUID) -> TelematicModel:
        return telematic_record

    async def update_fields(
        db: AsyncSession, record: TelematicModel, values: dict[str, object]
    ) -> TelematicModel:
        updated_values.update(values)
        return record

    async def no_vehicle_by_id(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    async def mark_assigned(db_session: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(telematics_repository, "update_fields", update_fields)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", no_vehicle_by_id
    )
    monkeypatch.setattr(vehicles_public_service, "mark_device_assigned", mark_assigned)


@pytest.mark.asyncio
async def test_update_telematic_rejects_unknown_vehicle_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-assigning to a VIN matching no live vehicle fails instead of unassigning (D10)."""
    telematic_record = build_telematic_record(uuid4())
    updated_values: dict[str, object] = {}
    _patch_update_dependencies(monkeypatch, telematic_record, updated_values)

    async def no_vehicle(db: AsyncSession, vin: str) -> None:
        return None

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", no_vehicle
    )

    with pytest.raises(TelematicVehicleNotFoundError):
        await telematics_service.update_telematic(
            fake_db_session(),
            telematic_record.telematic_id,
            TelematicUpdateRequest.model_validate({"vehicle_vin": UNKNOWN_VIN}),
        )

    assert updated_values == {}


@pytest.mark.asyncio
async def test_update_telematic_explicit_null_vin_unassigns_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``vehicle_vin: null`` still unassigns the device (D10)."""
    telematic_record = build_telematic_record(uuid4())
    updated_values: dict[str, object] = {}
    _patch_update_dependencies(monkeypatch, telematic_record, updated_values)

    await telematics_service.update_telematic(
        fake_db_session(),
        telematic_record.telematic_id,
        TelematicUpdateRequest.model_validate({"vehicle_vin": None}),
    )

    assert updated_values == {"vehicle_id": None}


@pytest.mark.asyncio
async def test_update_telematic_without_vin_keeps_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting ``vehicle_vin`` leaves the current assignment untouched."""
    telematic_record = build_telematic_record(uuid4())
    updated_values: dict[str, object] = {}
    _patch_update_dependencies(monkeypatch, telematic_record, updated_values)

    await telematics_service.update_telematic(
        fake_db_session(),
        telematic_record.telematic_id,
        TelematicUpdateRequest.model_validate({"firmware_version": "2.0.0"}),
    )

    assert updated_values == {"firmware_version": "2.0.0"}


@pytest.mark.asyncio
async def test_update_telematic_rejects_vehicle_with_another_live_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-assigning to a vehicle that already carries a live device is a conflict."""
    telematic_record = build_telematic_record(uuid4())
    target_vehicle_id = uuid4()
    updated_values: dict[str, object] = {}
    _patch_update_dependencies(monkeypatch, telematic_record, updated_values)

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return VehicleReference(
            vehicle_id=target_vehicle_id, vin=vin, battery_capacity_kwh=None
        )

    async def other_device(db: AsyncSession, vehicle_id: UUID) -> TelematicModel:
        assert vehicle_id == target_vehicle_id
        return build_telematic_record(target_vehicle_id)

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(telematics_repository, "find_by_vehicle_id", other_device)

    with pytest.raises(TelematicConflictError):
        await telematics_service.update_telematic(
            fake_db_session(),
            telematic_record.telematic_id,
            TelematicUpdateRequest.model_validate({"vehicle_vin": UNKNOWN_VIN}),
        )

    assert updated_values == {}


@pytest.mark.asyncio
async def test_resolve_mapping_by_serial_ignores_soft_deleted_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device mapped to a soft-deleted vehicle resolves to no mapping (D11)."""
    telematic_vehicle_mapping = TelematicVehicleMapping(
        telematic_id=uuid4(), vehicle_id=uuid4()
    )

    async def find_mapping(
        db: AsyncSession, telematic_serial: str
    ) -> TelematicVehicleMapping:
        return telematic_vehicle_mapping

    async def deleted_vehicle(db: AsyncSession, vehicle_id: UUID) -> None:
        assert vehicle_id == telematic_vehicle_mapping.vehicle_id
        return None

    monkeypatch.setattr(telematics_repository, "find_mapping_by_serial", find_mapping)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", deleted_vehicle
    )

    assert (
        await telematics_service.resolve_mapping_by_serial(
            fake_db_session(), "TBOX-TEST-001"
        )
        is None
    )


@pytest.mark.asyncio
async def test_resolve_mapping_by_serial_returns_mapping_for_live_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device mapped to a live vehicle resolves to its mapping unchanged."""
    telematic_vehicle_mapping = TelematicVehicleMapping(
        telematic_id=uuid4(), vehicle_id=uuid4()
    )

    async def find_mapping(
        db: AsyncSession, telematic_serial: str
    ) -> TelematicVehicleMapping:
        return telematic_vehicle_mapping

    async def live_vehicle(db: AsyncSession, vehicle_id: UUID) -> VehicleReference:
        return VehicleReference(
            vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
        )

    monkeypatch.setattr(telematics_repository, "find_mapping_by_serial", find_mapping)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", live_vehicle
    )

    assert (
        await telematics_service.resolve_mapping_by_serial(
            fake_db_session(), "TBOX-TEST-001"
        )
        == telematic_vehicle_mapping
    )
