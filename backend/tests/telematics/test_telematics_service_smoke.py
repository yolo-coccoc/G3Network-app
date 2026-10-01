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
    TelematicNotConfigurableError,
    TelematicNotFoundError,
)
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import (
    TelematicConfigPushRequest,
    TelematicCreateRequest,
)
from app.domains.telematics.types import TelematicStatus
from app.domains.vehicles.types import (
    VehicleReference,
)
from tests.builders import build_telematic_record, fake_db_session


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

    async def no_existing_serial(db: AsyncSession, serial: str) -> None:
        return None

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return reference

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def insert_telematic(
        db: AsyncSession, values: dict[str, object]
    ) -> TelematicModel:
        return record

    async def no_assigned_vehicle(statement: object) -> None:
        return None

    class FakeDatabase:
        async def scalar(self, statement: object) -> None:
            return await no_assigned_vehicle(statement)

    async def mark_assigned(db_session: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(telematics_repository, "get_by_serial", no_existing_serial)
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
    # since FakeDatabase only implements .scalar(), and this test is about
    # VIN resolution, not activation.
    monkeypatch.setattr(vehicles_public_service, "mark_device_assigned", mark_assigned)

    response = await telematics_service.create_telematic(
        cast(AsyncSession, FakeDatabase()),
        TelematicCreateRequest(
            telematic_serial=record.telematic_serial,
            vehicle_vin=reference.vin,
            status=record.status,
            firmware_version=record.firmware_version,
        ),
    )

    assert response.vehicle_id == vehicle_id
    assert response.vehicle_vin == reference.vin


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
