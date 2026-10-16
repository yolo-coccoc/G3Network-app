"""Smoke tests for the telematics service and the F-J2 config-push publisher."""

from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from aiomqtt import MqttError
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.commands.mqtt_publisher as telematics_mqtt_publisher
import app.domains.telematics.config_push_service as telematic_config_push_service
import app.domains.telematics.health_service as telematic_health_service
import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.service as telematics_service
import app.domains.telemetry.service as telemetry_public_service
import app.domains.vehicles.service as vehicles_public_service
from app.api.main import domain_error_handler
from app.domains.identity.types import UserRole
from app.domains.telematics.exceptions import (
    TelematicCommandPublishError,
    TelematicConflictError,
    TelematicDateInvalidError,
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
from app.libs.common.clock import CLIENT_CLOCK_SKEW
from app.libs.common.errors import NotFoundError
from tests.builders import build_telematic_record, fake_db_session
from tests.principals import build_internal_principal, build_principal

UNKNOWN_VIN = "1HGBH41JXMN999999"

pytestmark = pytest.mark.usefixtures("own_organization_for_new_records")


@pytest.fixture(autouse=True)
def vehicle_never_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every mounted vehicle look like it never reported telemetry.

    ``build_telematic_response`` derives the F-J1 health fields through the
    telemetry public service; these tests are not about health, so the
    lookups return "no telemetry" instead of touching the fake session.
    """

    async def no_last_telemetry_at(db_session: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(
        telemetry_public_service, "resolve_last_telemetry_at", no_last_telemetry_at
    )


@pytest.mark.asyncio
async def test_telematic_service_resolves_vehicle_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The telematic service resolves the VIN via the vehicles public service."""
    vehicle_id = uuid4()
    record = build_telematic_record(vehicle_id)
    reference = VehicleReference(
        organization_id=uuid4(),
        vehicle_id=vehicle_id,
        vin="1HGBH41JXMN109186",
        battery_capacity_kwh=None,
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

    response = await telematics_service.create_telematic(
        fake_db_session(),
        TelematicCreateRequest(
            telematic_serial=record.telematic_serial,
            organization_id=record.organization_id,
            vehicle_vin=reference.vin,
            status=record.status,
        ),
        principal=build_internal_principal(),
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
        organization_id=uuid4(),
        vehicle_id=vehicle_id,
        vin="1HGBH41JXMN109186",
        battery_capacity_kwh=None,
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
                organization_id=uuid4(),
                vehicle_vin=reference.vin,
            ),
            principal=build_internal_principal(),
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
        organization_id=uuid4(),
        acquired_at=now,
        vehicle_id=None,
        status=TelematicStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )

    telematic_response = await telematics_service.build_telematic_response(
        fake_db_session(), telematic_record
    )

    assert telematic_response.vehicle_vin is None
    assert telematic_response.telemetry_interval_seconds is None
    # F-J1: an unmounted device has no health to report.
    assert telematic_response.last_seen_at is None
    assert telematic_response.is_online is False
    assert telematic_response.is_silent is False
    assert telematic_response.last_signal_strength_dbm is None


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
        **_scope: object,
    ) -> list[TelematicModel]:
        list_arguments.update(offset=offset, limit=limit, status_filter=status_filter)
        return []

    async def count(
        db_session: AsyncSession,
        status_filter: TelematicStatus | None = None,
        **_scope: object,
    ) -> int:
        return 0

    monkeypatch.setattr(telematics_repository, "list_all", list_all)
    monkeypatch.setattr(telematics_repository, "count", count)

    telematic_list_response = await telematics_service.list_telematics(
        fake_db_session(),
        page=0,
        page_size=0,
        status_filter=TelematicStatus.INACTIVE,
        principal=build_internal_principal(),
    )

    assert list_arguments == {
        "offset": 0,
        "limit": 1,
        "status_filter": TelematicStatus.INACTIVE,
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
    """A successful push publishes the command and writes nothing (TX-09)."""
    vehicle_id = uuid4()
    record = build_telematic_record(vehicle_id)
    published: dict[str, object] = {}

    async def get_by_id(
        db: AsyncSession, telematic_id: UUID, **_scope: object
    ) -> TelematicModel:
        return record

    async def publish(serial: str, payload: dict[str, object]) -> None:
        published["serial"] = serial
        published["payload"] = payload

    async def update_fields(
        db: AsyncSession,
        telematic_record: TelematicModel,
        values: dict[str, object],
        *,
        change_reason: str,
    ) -> TelematicModel:
        raise AssertionError("a config push must not write the device row")

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(telematics_mqtt_publisher, "publish_device_command", publish)
    monkeypatch.setattr(telematics_repository, "update_fields", update_fields)

    response = await telematic_config_push_service.push_telematic_config(
        fake_db_session(),
        record.telematic_id,
        TelematicConfigPushRequest(telemetry_interval_seconds=60),
        principal=build_internal_principal(),
    )

    assert published["serial"] == record.telematic_serial
    payload = cast(dict[str, object], published["payload"])
    assert payload["telemetry_interval_seconds"] == 60
    # The response and the wire message agree on the command timestamp.
    assert response.config_pushed_at.isoformat() == payload["timestamp"]
    assert response.telemetry_interval_seconds == 60
    assert response.command_topic == (
        f"g3network/telematics/{record.telematic_serial}/command"
    )


@pytest.mark.asyncio
async def test_push_telematic_config_rejects_unknown_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown or soft-deleted device 404s before any publish is attempted."""

    async def get_by_id(db: AsyncSession, telematic_id: UUID, **_scope: object) -> None:
        return None

    async def fail_if_called(serial: str, payload: dict[str, object]) -> None:
        raise AssertionError("publish must not be attempted for an unknown device")

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        telematics_mqtt_publisher, "publish_device_command", fail_if_called
    )

    with pytest.raises(TelematicNotFoundError):
        await telematic_config_push_service.push_telematic_config(
            fake_db_session(),
            uuid4(),
            TelematicConfigPushRequest(telemetry_interval_seconds=60),
            principal=build_internal_principal(),
        )


@pytest.mark.asyncio
async def test_push_telematic_config_rejects_inactive_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An INACTIVE device is refused before any publish is attempted."""
    record = build_telematic_record(uuid4())
    record.status = TelematicStatus.INACTIVE

    async def get_by_id(
        db: AsyncSession, telematic_id: UUID, **_scope: object
    ) -> TelematicModel:
        return record

    async def fail_if_called(serial: str, payload: dict[str, object]) -> None:
        raise AssertionError("publish must not be attempted for an INACTIVE device")

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        telematics_mqtt_publisher, "publish_device_command", fail_if_called
    )

    with pytest.raises(TelematicNotConfigurableError):
        await telematic_config_push_service.push_telematic_config(
            fake_db_session(),
            record.telematic_id,
            TelematicConfigPushRequest(telemetry_interval_seconds=60),
            principal=build_internal_principal(),
        )


@pytest.mark.asyncio
async def test_push_telematic_config_does_not_record_when_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed publish leaves the database untouched (fail-closed)."""
    record = build_telematic_record(uuid4())

    async def get_by_id(
        db: AsyncSession, telematic_id: UUID, **_scope: object
    ) -> TelematicModel:
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
        await telematic_config_push_service.push_telematic_config(
            fake_db_session(),
            record.telematic_id,
            TelematicConfigPushRequest(telemetry_interval_seconds=60),
            principal=build_internal_principal(),
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
                organization_id=uuid4(),
                vehicle_vin=UNKNOWN_VIN,
            ),
            principal=build_internal_principal(),
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

    async def get_by_id(
        db: AsyncSession, telematic_id: UUID, **_scope: object
    ) -> TelematicModel:
        return telematic_record

    async def update_fields(
        db: AsyncSession,
        record: TelematicModel,
        values: dict[str, object],
        *,
        change_reason: str,
        **_scope: object,
    ) -> TelematicModel:
        updated_values.update(values)
        return record

    async def no_vehicle_by_id(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(telematics_repository, "update_fields", update_fields)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", no_vehicle_by_id
    )


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
            principal=build_internal_principal(),
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
        principal=build_internal_principal(),
    )

    assert updated_values == {"vehicle_id": None, "installed_at": None}


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
        TelematicUpdateRequest.model_validate({"imei": "356938035643809"}),
        principal=build_internal_principal(),
    )

    assert updated_values == {"imei": "356938035643809"}


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
            organization_id=uuid4(),
            vehicle_id=target_vehicle_id,
            vin=vin,
            battery_capacity_kwh=None,
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
            principal=build_internal_principal(),
        )

    assert updated_values == {}


@pytest.mark.asyncio
async def test_resolve_mapping_by_serial_ignores_soft_deleted_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device mapped to a soft-deleted vehicle resolves to no mapping (D11)."""
    telematic_vehicle_mapping = TelematicVehicleMapping(
        telematic_id=uuid4(), vehicle_id=uuid4(), organization_id=uuid4()
    )

    async def find_mapping(
        db: AsyncSession, telematic_serial: str
    ) -> tuple[UUID, UUID]:
        return (
            telematic_vehicle_mapping.telematic_id,
            telematic_vehicle_mapping.vehicle_id,
        )

    async def deleted_vehicle(db: AsyncSession, vehicle_id: UUID) -> None:
        assert vehicle_id == telematic_vehicle_mapping.vehicle_id
        return None

    monkeypatch.setattr(
        telematics_repository, "find_mounted_vehicle_id_by_serial", find_mapping
    )
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
        telematic_id=uuid4(), vehicle_id=uuid4(), organization_id=uuid4()
    )

    async def find_mapping(
        db: AsyncSession, telematic_serial: str
    ) -> tuple[UUID, UUID]:
        return (
            telematic_vehicle_mapping.telematic_id,
            telematic_vehicle_mapping.vehicle_id,
        )

    async def live_vehicle(db: AsyncSession, vehicle_id: UUID) -> VehicleReference:
        return VehicleReference(
            organization_id=telematic_vehicle_mapping.organization_id,
            vehicle_id=vehicle_id,
            vin="1HGBH41JXMN109186",
            battery_capacity_kwh=None,
        )

    monkeypatch.setattr(
        telematics_repository, "find_mounted_vehicle_id_by_serial", find_mapping
    )
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", live_vehicle
    )

    assert (
        await telematics_service.resolve_mapping_by_serial(
            fake_db_session(), "TBOX-TEST-001"
        )
        == telematic_vehicle_mapping
    )


@pytest.mark.asyncio
async def test_fleet_limited_caller_gets_not_found_for_a_device_off_their_trucks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detail, health, reports, config push and delete 404 off the limit (D5)."""
    visible_device = build_telematic_record(uuid4())
    other_truck_device = build_telematic_record(uuid4())
    unmounted_device = build_telematic_record(uuid4())
    unmounted_device.vehicle_id = None
    by_id = {
        device.telematic_id: device
        for device in (visible_device, other_truck_device, unmounted_device)
    }

    async def get_by_id(
        db: AsyncSession, telematic_id: UUID, **_scope: object
    ) -> TelematicModel:
        return by_id[telematic_id]

    async def resolve_id(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    visible_vehicle_ids = frozenset({cast(UUID, visible_device.vehicle_id)})
    principal = build_principal(
        roles=frozenset({UserRole.FLEET_MANAGER, UserRole.OPERATIONS})
    )

    detail = await telematics_service.get_telematic(
        fake_db_session(),
        visible_device.telematic_id,
        principal=principal,
        visible_vehicle_ids=visible_vehicle_ids,
    )
    assert detail.telematic_id == visible_device.telematic_id
    for hidden_device in (other_truck_device, unmounted_device):
        hidden_id = hidden_device.telematic_id
        with pytest.raises(TelematicNotFoundError):
            await telematics_service.get_telematic(
                fake_db_session(),
                hidden_id,
                principal=principal,
                visible_vehicle_ids=visible_vehicle_ids,
            )
        with pytest.raises(TelematicNotFoundError):
            await telematic_health_service.get_telematic_health(
                fake_db_session(),
                hidden_id,
                principal=principal,
                visible_vehicle_ids=visible_vehicle_ids,
            )
        with pytest.raises(TelematicNotFoundError):
            await telematic_health_service.list_telematic_status_reports(
                fake_db_session(),
                hidden_id,
                principal=principal,
                since=None,
                limit=10,
                visible_vehicle_ids=visible_vehicle_ids,
            )
        with pytest.raises(TelematicNotFoundError):
            await telematic_config_push_service.push_telematic_config(
                fake_db_session(),
                hidden_id,
                TelematicConfigPushRequest(telemetry_interval_seconds=60),
                principal=principal,
                visible_vehicle_ids=visible_vehicle_ids,
            )
        with pytest.raises(TelematicNotFoundError):
            await telematics_service.soft_delete_telematic(
                fake_db_session(),
                hidden_id,
                principal=principal,
                visible_vehicle_ids=visible_vehicle_ids,
            )


@pytest.mark.asyncio
async def test_device_list_and_health_dashboard_filter_on_the_fleet_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device page, its total and the whole-scope health read take the limit."""
    received_vehicle_ids: list[object] = []

    async def list_all(db: AsyncSession, **filters: object) -> list[TelematicModel]:
        received_vehicle_ids.append(filters["vehicle_ids"])
        return []

    async def count(
        db: AsyncSession,
        status_filter: TelematicStatus | None = None,
        **filters: object,
    ) -> int:
        received_vehicle_ids.append(filters["vehicle_ids"])
        return 0

    async def list_in_scope(
        db: AsyncSession, **filters: object
    ) -> list[TelematicModel]:
        received_vehicle_ids.append(filters["vehicle_ids"])
        return []

    monkeypatch.setattr(telematics_repository, "list_all", list_all)
    monkeypatch.setattr(telematics_repository, "count", count)
    monkeypatch.setattr(telematics_repository, "list_in_scope", list_in_scope)
    visible_vehicle_ids = frozenset({uuid4()})
    principal = build_principal(
        roles=frozenset({UserRole.FLEET_MANAGER, UserRole.OPERATIONS})
    )

    await telematics_service.list_telematics(
        fake_db_session(),
        principal=principal,
        page=1,
        page_size=10,
        visible_vehicle_ids=visible_vehicle_ids,
    )
    await telematic_health_service.get_device_health_summary(
        fake_db_session(), principal=principal, visible_vehicle_ids=visible_vehicle_ids
    )

    assert received_vehicle_ids == [visible_vehicle_ids] * 3


@pytest.mark.asyncio
async def test_update_telematic_refuses_to_mount_on_a_truck_off_the_fleet_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A limited caller cannot move a visible device onto a truck they cannot see."""
    telematic_record = build_telematic_record(uuid4())
    updated_values: dict[str, object] = {}
    _patch_update_dependencies(monkeypatch, telematic_record, updated_values)

    async def truck_off_the_limit(db: AsyncSession, vin: str) -> VehicleReference:
        return VehicleReference(
            organization_id=uuid4(),
            vehicle_id=uuid4(),
            vin=vin,
            battery_capacity_kwh=None,
        )

    monkeypatch.setattr(
        vehicles_public_service,
        "resolve_vehicle_reference_by_vin",
        truck_off_the_limit,
    )

    with pytest.raises(TelematicVehicleNotFoundError):
        await telematics_service.update_telematic(
            fake_db_session(),
            telematic_record.telematic_id,
            TelematicUpdateRequest.model_validate({"vehicle_vin": UNKNOWN_VIN}),
            principal=build_internal_principal(),
            visible_vehicle_ids=frozenset({cast(UUID, telematic_record.vehicle_id)}),
        )

    assert updated_values == {}


async def _assert_bad_request(error: Exception) -> None:
    """Assert that the shared HTTP handler turns ``error`` into a 400.

    Args:
        error: The domain exception the service raised.
    """
    request: Any = None
    error_response = await domain_error_handler(request, error)
    assert error_response.status_code == status.HTTP_400_BAD_REQUEST


def _forbid_device_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every repository call of a device create fail the test.

    Args:
        monkeypatch: Pytest fixture used to replace the repository functions.
    """

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("a refused time must stop before the repository")

    for function_name in ("find_by_serial", "find_by_imei", "insert"):
        monkeypatch.setattr(telematics_repository, function_name, fail_if_called)


@pytest.mark.asyncio
async def test_create_telematic_rejects_acquired_at_without_time_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A naive ``acquired_at`` is a 400, never stored as host local time."""
    _forbid_device_writes(monkeypatch)

    with pytest.raises(TelematicDateInvalidError) as error_info:
        await telematics_service.create_telematic(
            fake_db_session(),
            TelematicCreateRequest(
                telematic_serial="TBOX-NAIVE-001",
                acquired_at=datetime(2026, 10, 1, 8, 0),
            ),
            principal=build_internal_principal(),
        )
    await _assert_bad_request(error_info.value)


@pytest.mark.asyncio
async def test_create_telematic_rejects_acquired_at_in_the_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``acquired_at`` beyond the clock skew is a 400 (RV-AS4)."""
    _forbid_device_writes(monkeypatch)
    future_acquired_at = (
        datetime.now(timezone.utc) + CLIENT_CLOCK_SKEW + timedelta(minutes=1)
    )

    with pytest.raises(TelematicDateInvalidError) as error_info:
        await telematics_service.create_telematic(
            fake_db_session(),
            TelematicCreateRequest(
                telematic_serial="TBOX-FUTURE-001",
                acquired_at=future_acquired_at,
            ),
            principal=build_internal_principal(),
        )
    await _assert_bad_request(error_info.value)


@pytest.mark.asyncio
async def test_create_telematic_stores_aware_acquired_at_in_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A past ``acquired_at`` with an offset is accepted and stored as UTC."""
    record = build_telematic_record(uuid4())
    record.vehicle_id = None
    inserted_values: dict[str, object] = {}

    async def no_existing_serial(db: AsyncSession, telematic_serial: str) -> None:
        return None

    async def insert_telematic(
        db: AsyncSession, values: dict[str, object]
    ) -> TelematicModel:
        inserted_values.update(values)
        return record

    monkeypatch.setattr(telematics_repository, "find_by_serial", no_existing_serial)
    monkeypatch.setattr(telematics_repository, "insert", insert_telematic)

    await telematics_service.create_telematic(
        fake_db_session(),
        TelematicCreateRequest(
            telematic_serial=record.telematic_serial,
            organization_id=record.organization_id,
            acquired_at=datetime(
                2026, 10, 1, 15, 0, tzinfo=timezone(timedelta(hours=7))
            ),
        ),
        principal=build_internal_principal(),
    )

    assert inserted_values["acquired_at"] == datetime(
        2026, 10, 1, 8, 0, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_list_telematic_status_reports_rejects_since_without_time_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A naive ``since`` is a 400, not a naive/aware comparison in the query."""

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("a refused time must stop before the repository")

    monkeypatch.setattr(telematics_repository, "get_by_id", fail_if_called)
    monkeypatch.setattr(telematics_repository, "list_status_reports", fail_if_called)

    with pytest.raises(TelematicDateInvalidError) as error_info:
        await telematic_health_service.list_telematic_status_reports(
            fake_db_session(),
            uuid4(),
            principal=build_internal_principal(),
            since=datetime(2026, 10, 1, 8, 0),
            limit=10,
        )
    await _assert_bad_request(error_info.value)
