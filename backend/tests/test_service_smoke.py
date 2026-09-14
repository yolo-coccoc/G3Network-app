"""Smoke test cho các service workflow chính của backend."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.service as telematics_service
import app.domains.telematics.service as telematics_public_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.repository as vehicle_repository
import app.domains.vehicles.service as vehicle_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.charging_sessions.models import ChargingSessionModel
from app.domains.charging_sessions.types import (
    MeterSampleInput,
    SessionEventType,
    SessionStatus,
)
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import TelematicCreateRequest
from app.domains.telematics.types import TelematicStatus, TelematicVehicleMapping
from app.domains.telemetry.schemas import TelemetryEnvelope, TelemetryMessage
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.schemas import VehicleCreateRequest
from app.domains.vehicles.types import VehicleReference, VehicleStatus


def _db() -> AsyncSession:
    """Tạo placeholder session cho unit test không truy cập database."""
    return cast(AsyncSession, object())


def _vehicle_record() -> VehicleModel:
    """Tạo ORM vehicle tối thiểu để service chuyển thành response."""
    now = datetime.now(timezone.utc)
    return VehicleModel(
        vehicle_id=uuid4(),
        license_plate="TEST-001",
        vin="1HGBH41JXMN109186",
        make="G3Network",
        model="E-Truck",
        year=2026,
        status=VehicleStatus.ACTIVE,
        fleet_id=None,
        created_at=now,
        updated_at=now,
    )


def _telematic_record(vehicle_id: UUID) -> TelematicModel:
    """Tạo ORM telematic tối thiểu đã gán vào một xe."""
    now = datetime.now(timezone.utc)
    return TelematicModel(
        telematic_id=uuid4(),
        telematic_serial="TBOX-TEST-001",
        vehicle_id=vehicle_id,
        status=TelematicStatus.ACTIVE,
        firmware_version="test",
        created_at=now,
        updated_at=now,
    )


def _telemetry_envelope() -> TelemetryEnvelope:
    """Tạo envelope telemetry hợp lệ cho process_message."""
    message = TelemetryMessage.model_validate(
        {
            "message_uuid": str(uuid4()),
            "telematic_serial": "TBOX-TEST-001",
            "recorded_at": "2026-08-26T10:00:00Z",
            "location": {"latitude": 10.8, "longitude": 106.7},
            "battery": {"soc": 80},
        }
    )
    return TelemetryEnvelope(message=message, raw_payload={"test": True})


def _charging_session() -> ChargingSessionModel:
    """Tạo aggregate session tối thiểu cho charging service test."""
    now = datetime.now(timezone.utc)
    return ChargingSessionModel(
        session_id=uuid4(),
        station_id=uuid4(),
        evse_id=uuid4(),
        connector_id=uuid4(),
        ocpp_transaction_id="TX-TEST-001",
        status=SessionStatus.ACTIVE,
        started_at=now,
        ended_at=None,
        meter_start_wh=None,
        meter_end_wh=None,
        energy_delivered_wh=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_vehicle_service_creates_vehicle_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vehicle service tạo response khi không có conflict unique."""
    record = _vehicle_record()

    async def no_existing_plate(db: AsyncSession, value: str) -> None:
        return None

    async def no_existing_vin(db: AsyncSession, value: str) -> None:
        return None

    async def insert_vehicle(db: AsyncSession, values: dict[str, Any]) -> VehicleModel:
        return record

    monkeypatch.setattr(vehicle_repository, "find_by_license_plate", no_existing_plate)
    monkeypatch.setattr(vehicle_repository, "find_by_vin", no_existing_vin)
    monkeypatch.setattr(vehicle_repository, "insert", insert_vehicle)

    response = await vehicle_service.create_vehicle(
        _db(),
        VehicleCreateRequest(
            license_plate=record.license_plate,
            vin=record.vin,
            make=record.make,
            model=record.model,
            year=record.year,
            status=record.status,
            fleet_id=None,
        ),
    )

    assert response.vehicle_id == record.vehicle_id
    assert response.vin == record.vin


@pytest.mark.asyncio
async def test_vehicle_service_soft_delete_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft delete service trả message khi repository xóa thành công."""
    record = _vehicle_record()

    async def soft_delete(db: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    monkeypatch.setattr(vehicle_repository, "soft_delete", soft_delete)

    result = await vehicle_service.soft_delete_vehicle(_db(), record.vehicle_id)

    assert result == {"message": "Vehicle deleted successfully"}


@pytest.mark.asyncio
async def test_telematic_service_resolves_vehicle_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telematic service resolve VIN qua public service vehicles."""
    vehicle_id = uuid4()
    record = _telematic_record(vehicle_id)
    reference = VehicleReference(vehicle_id=vehicle_id, vin="1HGBH41JXMN109186")

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


@pytest.mark.asyncio
async def test_telemetry_service_skips_unmapped_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry service skip message khi serial chưa có mapping."""

    async def no_mapping(db: AsyncSession, serial: str) -> None:
        return None

    monkeypatch.setattr(
        telematics_public_service, "resolve_mapping_by_serial", no_mapping
    )

    result = await telemetry_service.process_message(_db(), _telemetry_envelope())

    assert result == {"processed": 0, "skipped": 1, "errors": 0}


@pytest.mark.asyncio
async def test_telemetry_service_persists_mapped_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry service enrich và persist đúng một message hợp lệ."""
    mapping = TelematicVehicleMapping(telematic_id=uuid4(), vehicle_id=uuid4())

    async def resolve_mapping(db: AsyncSession, serial: str) -> TelematicVehicleMapping:
        return mapping

    async def insert_telemetry(db: AsyncSession, values: dict[str, object]) -> int:
        return 1

    monkeypatch.setattr(
        telematics_public_service,
        "resolve_mapping_by_serial",
        resolve_mapping,
    )
    monkeypatch.setattr(telemetry_repository, "insert_telemetry", insert_telemetry)

    async def skip_alert_evaluation(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(telemetry_service, "sync_battery_alerts", skip_alert_evaluation)

    result = await telemetry_service.process_message(_db(), _telemetry_envelope())

    assert result == {"processed": 1, "skipped": 0, "errors": 0}


@pytest.mark.asyncio
async def test_telemetry_service_pushes_battery_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service publish đúng topic và ngưỡng cố định tới thiết bị của xe."""
    vehicle_id = uuid4()
    mapping = TelematicVehicleMapping(
        telematic_id=uuid4(),
        vehicle_id=vehicle_id,
        telematic_serial="TBOX-TEST-001",
    )
    published: dict[str, object] = {}

    async def resolve_mapping(db: AsyncSession, value: UUID) -> TelematicVehicleMapping:
        return mapping

    async def publish_json(
        publisher: object, topic: str, payload: dict[str, object]
    ) -> None:
        published["topic"] = topic
        published["payload"] = payload

    monkeypatch.setattr(
        telematics_public_service,
        "resolve_mapping_by_vehicle_id",
        resolve_mapping,
    )
    monkeypatch.setattr(
        telemetry_service.telemetry_publisher.MQTTPublisher,
        "publish_json",
        publish_json,
    )

    response = await telemetry_service.push_battery_threshold_to_vehicle(
        _db(), vehicle_id
    )

    assert response.threshold_percent == 20
    assert published["topic"] == (
        "g3network/telematics/TBOX-TEST-001/config/battery-threshold"
    )
    payload = cast(dict[str, object], published["payload"])
    assert payload["threshold_percent"] == 20


@pytest.mark.asyncio
async def test_charging_service_runs_started_meter_ended_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Charging service chạy đúng lifecycle Started đến Ended."""
    session = _charging_session()
    now = datetime.now(timezone.utc)
    inserted_events: list[SessionEventType] = []
    inserted_meters: list[Decimal] = []

    async def create_session(
        db: AsyncSession, **kwargs: object
    ) -> ChargingSessionModel:
        session.meter_start_wh = cast(Decimal | None, kwargs["meter_start_wh"])
        session.started_at = cast(datetime, kwargs["started_at"])
        return session

    async def get_by_transaction(
        db: AsyncSession, station_id: UUID, transaction_id: str
    ) -> ChargingSessionModel:
        return session

    async def get_by_id(db: AsyncSession, session_id: UUID) -> ChargingSessionModel:
        return session

    async def insert_event(db: AsyncSession, **kwargs: object) -> None:
        inserted_events.append(cast(SessionEventType, kwargs["event_type"]))

    async def insert_meter(db: AsyncSession, **kwargs: object) -> None:
        inserted_meters.append(cast(Decimal, kwargs["value_wh"]))

    monkeypatch.setattr(charging_repository, "create_session", create_session)
    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )
    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_event", insert_event)
    monkeypatch.setattr(charging_repository, "insert_meter_value", insert_meter)
    monkeypatch.setattr(charging_repository, "utc_now", lambda: now)

    started = await charging_service.ingest_transaction_event(
        _db(),
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=session.ocpp_transaction_id,
        event_type=SessionEventType.STARTED,
        event_occurred_at=now,
        meter_start_wh=Decimal("1000"),
    )
    meter = await charging_service.ingest_meter_values(
        _db(),
        session_id=session.session_id,
        sample=MeterSampleInput(sampled_at=now, value_wh=Decimal("1500")),
    )
    ended = await charging_service.ingest_transaction_event(
        _db(),
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=session.ocpp_transaction_id,
        event_type=SessionEventType.ENDED,
        event_occurred_at=now,
        meter_end_wh=Decimal("1750"),
    )

    assert started.status is SessionStatus.ACTIVE
    assert meter.accepted_count == 1
    assert ended.status is SessionStatus.COMPLETED
    assert session.meter_end_wh == Decimal("1750")
    assert session.energy_delivered_wh == Decimal("750")
    assert inserted_events == [SessionEventType.STARTED, SessionEventType.ENDED]
    assert inserted_meters == [Decimal("1500")]
