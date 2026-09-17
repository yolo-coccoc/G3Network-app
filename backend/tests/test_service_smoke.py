"""Smoke test for the backend's main service workflows."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
import app.domains.notifications.service as notifications_service
import app.domains.telematics.monitoring.device_health_monitor as device_health_monitor
import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.service as telematics_service
import app.domains.telematics.service as telematics_public_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.repository as vehicle_repository
import app.domains.vehicles.service as vehicle_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.charging_sessions.exceptions import ChargingSessionInputError
from app.domains.charging_sessions.models import ChargingSessionModel
from app.domains.charging_sessions.types import (
    MeterSampleInput,
    SessionEventType,
    SessionStatus,
)
from app.domains.charging_stations.exceptions import ChargingConnectorNotFoundError
from app.domains.charging_stations.models import ChargingStationModel
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingStationMaintenanceStatus,
    NearestChargingStation,
)
from app.domains.notifications.models import NotificationModel
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import TelematicCreateRequest
from app.domains.telematics.types import TelematicStatus, TelematicVehicleMapping
from app.domains.telemetry.exceptions import (
    TelemetryInvalidRangeError,
    TelemetryNotFoundError,
)
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.schemas import TelemetryEnvelope, TelemetryMessage
from app.domains.telemetry.types import BatteryAlertLevel, VehicleAnomalyType
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.schemas import VehicleCreateRequest
from app.domains.vehicles.types import (
    VehicleActivationStatus,
    VehicleReference,
    VehicleStatus,
)
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location, location_to_coordinates


def _db() -> AsyncSession:
    """Create a placeholder session for a unit test that doesn't touch the database."""
    return cast(AsyncSession, object())


def _vehicle_record(
    *, activation_status: VehicleActivationStatus = VehicleActivationStatus.PENDING
) -> VehicleModel:
    """Create a minimal ORM vehicle for the service to convert into a response."""
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
        activation_status=activation_status,
        created_at=now,
        updated_at=now,
    )


def _telematic_record(vehicle_id: UUID) -> TelematicModel:
    """Create a minimal ORM telematic already assigned to a vehicle."""
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


def _telemetry_envelope(
    soc: float = 80.0,
    *,
    battery_temperature: float | None = None,
    battery_voltage: float | None = None,
    soh_percent: float | None = None,
    cycle_count: int | None = None,
    errors: list[str] | None = None,
) -> TelemetryEnvelope:
    """Create a valid telemetry envelope for process_message."""
    message = TelemetryMessage.model_validate(
        {
            "message_uuid": str(uuid4()),
            "telematic_serial": "TBOX-TEST-001",
            "recorded_at": "2026-08-26T10:00:00Z",
            "location": {"latitude": 10.8, "longitude": 106.7},
            "battery": {
                "soc": soc,
                "temperature": battery_temperature,
                "voltage": battery_voltage,
                "soh_percent": soh_percent,
                "cycle_count": cycle_count,
            },
            "errors": errors,
        }
    )
    return TelemetryEnvelope(message=message, raw_payload={"test": True})


def _telemetry_record(
    *,
    vehicle_id: UUID,
    recorded_at: datetime,
    message_id: int = 1,
    soh_percent: float | None = None,
    cycle_count: int | None = None,
) -> VehicleTelemetryModel:
    """Create a minimal ORM telemetry record for a history/latest test."""
    return VehicleTelemetryModel(
        message_id=message_id,
        message_uuid=uuid4(),
        telematic_id=uuid4(),
        telematic_serial="TBOX-TEST-001",
        vehicle_id=vehicle_id,
        recorded_at=recorded_at,
        received_at=recorded_at,
        location=coordinates_to_location(10.762622, 106.660172),
        speed=None,
        heading=None,
        soc=80.0,
        battery_voltage=None,
        battery_current=None,
        battery_temperature=None,
        soh_percent=soh_percent,
        cycle_count=cycle_count,
        motor_temperature=None,
        odometer=None,
        signal_strength=None,
        error_codes=None,
        raw_payload={},
        schema_version=1,
    )


def _charging_session() -> ChargingSessionModel:
    """Create a minimal aggregate session for the charging service test."""
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


def _charging_station_record(*, station_id: UUID | None = None) -> ChargingStationModel:
    """Create a minimal ORM station for a nearby-search mapper test."""
    now = datetime.now(timezone.utc)
    return ChargingStationModel(
        station_id=station_id or uuid4(),
        ocpp_identity="OCPP-TEST-001",
        display_name="Test Station",
        location=coordinates_to_location(10.762622, 106.660172),
        power_rating_kw=Decimal("120.00"),
        connector_standard="CCS2",
        operating_hours="24/7",
        maintenance_status=ChargingStationMaintenanceStatus.OPERATIONAL,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


@pytest.mark.asyncio
async def test_vehicle_service_creates_vehicle_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vehicle service creates a response when there's no unique conflict."""
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
    """The soft-delete service returns a message when the repository succeeds."""
    record = _vehicle_record()

    async def soft_delete(db: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    monkeypatch.setattr(vehicle_repository, "soft_delete", soft_delete)

    result = await vehicle_service.soft_delete_vehicle(_db(), record.vehicle_id)

    assert result == {"message": "Vehicle deleted successfully"}


@pytest.mark.asyncio
async def test_mark_device_assigned_advances_pending_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_device_assigned() moves a PENDING vehicle to DEVICE_ASSIGNED (F-F2)."""
    record = _vehicle_record(activation_status=VehicleActivationStatus.PENDING)
    updated_values: dict[str, object] = {}

    async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    async def update_fields(
        db_session: AsyncSession, vehicle_id: UUID, values: dict[str, object]
    ) -> VehicleModel:
        updated_values.update(values)
        return record

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)

    await vehicle_service.mark_device_assigned(_db(), record.vehicle_id)

    assert updated_values == {
        "activation_status": VehicleActivationStatus.DEVICE_ASSIGNED
    }


@pytest.mark.asyncio
async def test_mark_device_assigned_is_noop_past_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_device_assigned() doesn't regress a vehicle already past PENDING (F-F2)."""
    record = _vehicle_record(activation_status=VehicleActivationStatus.ACTIVATED)

    async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    async def fail_if_called(
        db_session: AsyncSession, vehicle_id: UUID, values: dict[str, object]
    ) -> VehicleModel:
        raise AssertionError("update_fields should not be called")

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", fail_if_called)

    await vehicle_service.mark_device_assigned(_db(), record.vehicle_id)


@pytest.mark.asyncio
async def test_mark_vehicle_activated_advances_device_assigned_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_vehicle_activated() moves a vehicle to ACTIVATED (F-F2)."""
    record = _vehicle_record(activation_status=VehicleActivationStatus.DEVICE_ASSIGNED)
    updated_values: dict[str, object] = {}

    async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    async def update_fields(
        db_session: AsyncSession, vehicle_id: UUID, values: dict[str, object]
    ) -> VehicleModel:
        updated_values.update(values)
        return record

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)

    await vehicle_service.mark_vehicle_activated(_db(), record.vehicle_id)

    assert updated_values == {"activation_status": VehicleActivationStatus.ACTIVATED}


@pytest.mark.asyncio
async def test_mark_vehicle_activated_is_noop_when_already_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_vehicle_activated() is idempotent once a vehicle is ACTIVATED (F-F2)."""
    record = _vehicle_record(activation_status=VehicleActivationStatus.ACTIVATED)

    async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel:
        return record

    async def fail_if_called(
        db_session: AsyncSession, vehicle_id: UUID, values: dict[str, object]
    ) -> VehicleModel:
        raise AssertionError("update_fields should not be called")

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", fail_if_called)

    await vehicle_service.mark_vehicle_activated(_db(), record.vehicle_id)


@pytest.mark.asyncio
async def test_get_vehicle_activation_summary_computes_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_vehicle_activation_summary() computes the success rate from two counts (F-F2)."""

    async def count_by_status(
        db_session: AsyncSession, activation_status: VehicleActivationStatus
    ) -> int:
        return {
            VehicleActivationStatus.DEVICE_ASSIGNED: 3,
            VehicleActivationStatus.ACTIVATED: 7,
        }[activation_status]

    monkeypatch.setattr(
        vehicle_repository, "count_by_activation_status", count_by_status
    )

    summary = await vehicle_service.get_vehicle_activation_summary(_db())

    assert summary.attempted_count == 10
    assert summary.activated_count == 7
    assert summary.activation_rate_percent == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_get_vehicle_activation_summary_handles_zero_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fleet with no provisioning attempts yet reports None, not a division by zero (F-F2)."""

    async def count_by_status(
        db_session: AsyncSession, activation_status: VehicleActivationStatus
    ) -> int:
        return 0

    monkeypatch.setattr(
        vehicle_repository, "count_by_activation_status", count_by_status
    )

    summary = await vehicle_service.get_vehicle_activation_summary(_db())

    assert summary.attempted_count == 0
    assert summary.activated_count == 0
    assert summary.activation_rate_percent is None


@pytest.mark.asyncio
async def test_telematic_service_resolves_vehicle_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The telematic service resolves the VIN via the vehicles public service."""
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


@pytest.mark.asyncio
async def test_telemetry_service_skips_unmapped_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The telemetry service skips a message when the serial has no mapping yet."""

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
    """The telemetry service enriches and persists exactly one valid message."""
    mapping = TelematicVehicleMapping(telematic_id=uuid4(), vehicle_id=uuid4())

    async def resolve_mapping(db: AsyncSession, serial: str) -> TelematicVehicleMapping:
        return mapping

    async def insert_telemetry(db: AsyncSession, values: dict[str, object]) -> int:
        return 1

    async def no_previous_telemetry(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleTelemetryModel | None:
        return None

    async def mark_activated(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    monkeypatch.setattr(
        telematics_public_service,
        "resolve_mapping_by_serial",
        resolve_mapping,
    )
    monkeypatch.setattr(telemetry_repository, "insert_telemetry", insert_telemetry)
    monkeypatch.setattr(
        telemetry_repository, "get_latest_vehicle_telemetry", no_previous_telemetry
    )
    # previous_telemetry is None here (first-ever message), which also
    # triggers F-F2's activation hook - mocked out since this test is about
    # the ingestion counters, not activation.
    monkeypatch.setattr(vehicle_service, "mark_vehicle_activated", mark_activated)

    result = await telemetry_service.process_message(_db(), _telemetry_envelope())

    assert result == {"processed": 1, "skipped": 0, "errors": 0}


@pytest.mark.asyncio
async def test_telemetry_service_raises_battery_alert_on_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_message raises exactly one notification when SOC crosses a threshold (F-A2)."""
    mapping = TelematicVehicleMapping(telematic_id=uuid4(), vehicle_id=uuid4())
    # battery_temperature/battery_voltage/error_codes/soh_percent are None
    # so F-A3's and F-A4's detectors (also run by process_message) find
    # nothing to report here.
    previous_telemetry = SimpleNamespace(
        soc=25.0,
        battery_temperature=None,
        battery_voltage=None,
        error_codes=None,
        soh_percent=None,
    )
    created_notifications: list[dict[str, object]] = []

    async def resolve_mapping(db: AsyncSession, serial: str) -> TelematicVehicleMapping:
        return mapping

    async def insert_telemetry(db: AsyncSession, values: dict[str, object]) -> int:
        return 1

    async def previous_reading(db: AsyncSession, vehicle_id: UUID) -> SimpleNamespace:
        return previous_telemetry

    async def no_nearest_station(
        db: AsyncSession, *, latitude: float, longitude: float
    ) -> NearestChargingStation | None:
        return None

    async def record_notification(db: AsyncSession, **kwargs: object) -> None:
        created_notifications.append(kwargs)

    monkeypatch.setattr(
        telematics_public_service, "resolve_mapping_by_serial", resolve_mapping
    )
    monkeypatch.setattr(telemetry_repository, "insert_telemetry", insert_telemetry)
    monkeypatch.setattr(
        telemetry_repository, "get_latest_vehicle_telemetry", previous_reading
    )
    monkeypatch.setattr(
        charging_stations_service,
        "find_nearest_operational_station",
        no_nearest_station,
    )
    monkeypatch.setattr(
        notifications_service, "create_notification", record_notification
    )

    result = await telemetry_service.process_message(
        _db(), _telemetry_envelope(soc=18.0)
    )

    assert result == {"processed": 1, "skipped": 0, "errors": 0}
    assert len(created_notifications) == 1
    call = created_notifications[0]
    assert call["notification_type"] is NotificationType.BATTERY_ALERT
    assert call["severity"] is NotificationSeverity.WARNING
    assert call["vehicle_id"] == mapping.vehicle_id
    payload = cast(dict[str, object], call["payload"])
    assert payload["threshold_percent"] == 20.0
    assert payload["soc"] == 18.0
    assert payload["station_id"] is None
    assert payload["distance_km"] is None


@pytest.mark.parametrize(
    ("previous_soc", "current_soc", "expected_level"),
    [
        (None, 15.0, None),
        (35.0, 31.0, None),
        (31.0, 30.0, BatteryAlertLevel.EARLY),
        (29.0, 25.0, None),
        (21.0, 20.0, BatteryAlertLevel.MAIN),
        (20.0, 19.8, None),
        (20.0, 20.0, None),
        (11.0, 10.0, BatteryAlertLevel.CRITICAL),
        (35.0, 8.0, BatteryAlertLevel.CRITICAL),
        (15.0, 20.0, None),
    ],
)
def test_detect_battery_alert_level(
    previous_soc: float | None,
    current_soc: float,
    expected_level: BatteryAlertLevel | None,
) -> None:
    """detect_battery_alert_level() only fires on a strict-above/inclusive-below crossing.

    Covers: no alert on the very first message (``previous_soc is None``);
    no alert while already above every threshold; a boundary touch fires
    exactly once; no re-alert while resting below (or exactly on) a
    threshold already crossed; a multi-threshold single-message drop
    resolves to the most severe level; rising SOC never alerts.
    """
    assert (
        telemetry_service.detect_battery_alert_level(previous_soc, current_soc)
        == expected_level
    )


@pytest.mark.parametrize(
    ("previous_soh", "current_soh", "expected"),
    [
        (None, 65.0, False),  # first reading: no alert, matches F-A2
        (None, 80.0, False),  # first reading, above threshold: no alert
        (75.0, None, False),  # device didn't report SOH this message
        (75.0, 71.0, False),  # dropping but still above threshold
        (75.0, 70.0, True),  # entry, inclusive boundary
        (65.0, 60.0, False),  # already below threshold: no repeat
        (60.0, 75.0, False),  # SOH recovering never alerts
        (85.0, 55.0, True),  # a bigger single-message drop still alerts once
    ],
)
def test_detect_soh_alert(
    previous_soh: float | None, current_soh: float | None, expected: bool
) -> None:
    """detect_soh_alert() only fires on a strict-above/inclusive-below crossing (F-A3).

    Same crossing shape as detect_battery_alert_level, but unlike F-A4's
    fire-safety detectors, a missing previous reading means no alert here -
    gradual SOH degradation isn't a condition where skipping the very first
    reading carries real risk.
    """
    assert telemetry_service.detect_soh_alert(previous_soh, current_soh) is expected


@pytest.mark.asyncio
async def test_process_message_raises_soh_alert_on_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_message raises exactly one SOH_ALERT notification on a crossing (F-A3)."""
    mapping = TelematicVehicleMapping(telematic_id=uuid4(), vehicle_id=uuid4())
    previous_telemetry = SimpleNamespace(
        soc=80.0,
        battery_temperature=None,
        battery_voltage=None,
        error_codes=None,
        soh_percent=75.0,
    )
    created_notifications: list[dict[str, object]] = []

    async def resolve_mapping(db: AsyncSession, serial: str) -> TelematicVehicleMapping:
        return mapping

    async def insert_telemetry(db: AsyncSession, values: dict[str, object]) -> int:
        return 1

    async def previous_reading(db: AsyncSession, vehicle_id: UUID) -> SimpleNamespace:
        return previous_telemetry

    async def record_notification(db: AsyncSession, **kwargs: object) -> None:
        created_notifications.append(kwargs)

    monkeypatch.setattr(
        telematics_public_service, "resolve_mapping_by_serial", resolve_mapping
    )
    monkeypatch.setattr(telemetry_repository, "insert_telemetry", insert_telemetry)
    monkeypatch.setattr(
        telemetry_repository, "get_latest_vehicle_telemetry", previous_reading
    )
    monkeypatch.setattr(
        notifications_service, "create_notification", record_notification
    )

    result = await telemetry_service.process_message(
        _db(), _telemetry_envelope(soc=80.0, soh_percent=68.0, cycle_count=142)
    )

    assert result == {"processed": 1, "skipped": 0, "errors": 0}
    assert len(created_notifications) == 1
    call = created_notifications[0]
    assert call["notification_type"] is NotificationType.SOH_ALERT
    assert call["severity"] is NotificationSeverity.WARNING
    assert call["vehicle_id"] == mapping.vehicle_id
    payload = cast(dict[str, object], call["payload"])
    assert payload["soh_percent"] == 68.0
    assert payload["cycle_count"] == 142


@pytest.mark.parametrize(
    ("previous_celsius", "current_celsius", "expects_anomaly"),
    [
        (None, 45.0, False),  # first reading, below threshold: no anomaly
        (None, 65.0, True),  # first reading, at/above threshold: DOES alert
        (55.0, 59.9, False),  # rising but still below threshold
        (55.0, 60.0, True),  # entry, inclusive boundary
        (61.0, 67.0, False),  # already above threshold: no repeat
        (67.0, 45.0, False),  # recovers below threshold: silent
        (45.0, 62.0, True),  # re-entry after recovery: alerts again
    ],
)
def test_detect_high_battery_temperature(
    previous_celsius: float | None,
    current_celsius: float | None,
    expects_anomaly: bool,
) -> None:
    """detect_high_battery_temperature() fires once on entry, not on every message above it.

    Unlike detect_battery_alert_level, a missing previous reading is treated
    as "below threshold" (not "no alert") - a fire-safety anomaly must not
    be silently skipped on a vehicle's very first message.
    """
    anomaly = telemetry_service.detect_high_battery_temperature(
        previous_celsius, current_celsius
    )
    if expects_anomaly:
        assert anomaly is not None
        assert anomaly.anomaly_type is VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE
        assert anomaly.evidence["observed_celsius"] == current_celsius
    else:
        assert anomaly is None


def test_detect_high_battery_temperature_skips_missing_reading() -> None:
    """No anomaly when the device didn't report a temperature at all."""
    assert telemetry_service.detect_high_battery_temperature(55.0, None) is None


@pytest.mark.parametrize(
    ("previous_volts", "current_volts", "expects_anomaly"),
    [
        (None, 600.0, False),  # first reading: undefined, stays silent
        (650.0, None, False),  # device didn't report voltage this time
        (650.0, 648.2, False),  # small drop, below threshold
        (650.0, 600.0, True),  # exactly at threshold (inclusive)
        (650.0, 591.0, True),  # well above threshold
        (600.0, 650.0, False),  # voltage rising, never an anomaly
    ],
)
def test_detect_sudden_voltage_drop(
    previous_volts: float | None,
    current_volts: float | None,
    expects_anomaly: bool,
) -> None:
    """detect_sudden_voltage_drop() only fires on an absolute drop >= the threshold."""
    anomaly = telemetry_service.detect_sudden_voltage_drop(
        previous_volts, current_volts
    )
    if expects_anomaly:
        assert anomaly is not None
        assert anomaly.anomaly_type is VehicleAnomalyType.SUDDEN_VOLTAGE_DROP
        assert anomaly.evidence["drop_volts"] == pytest.approx(
            previous_volts - current_volts  # type: ignore[operator]
        )
    else:
        assert anomaly is None


@pytest.mark.parametrize(
    ("previous_codes", "current_codes", "expected_new_codes"),
    [
        (None, None, None),
        (None, [], None),
        (None, ["E001"], ["E001"]),
        (["E001"], ["E001"], None),  # unchanged: still faulted, not a new fault
        (["E001"], [], None),  # cleared: not an anomaly
        (["E001"], ["E001", "E042"], ["E042"]),  # one new code alongside an old one
    ],
)
def test_detect_new_error_codes(
    previous_codes: list[str] | None,
    current_codes: list[str] | None,
    expected_new_codes: list[str] | None,
) -> None:
    """detect_new_error_codes() only fires on a code absent from the previous reading."""
    anomaly = telemetry_service.detect_new_error_codes(previous_codes, current_codes)
    if expected_new_codes is None:
        assert anomaly is None
    else:
        assert anomaly is not None
        assert anomaly.anomaly_type is VehicleAnomalyType.DEVICE_FAULT
        assert anomaly.evidence["new_codes"] == expected_new_codes


def test_detect_vehicle_anomalies_returns_every_detector_that_fires() -> None:
    """A single reading tripping two conditions at once returns both anomalies."""
    previous_telemetry = SimpleNamespace(
        battery_temperature=45.0, battery_voltage=650.0, error_codes=None
    )
    message = _telemetry_envelope(
        battery_temperature=65.0, battery_voltage=650.0, errors=["E042"]
    ).message

    anomalies = telemetry_service.detect_vehicle_anomalies(
        cast(VehicleTelemetryModel, previous_telemetry), message
    )

    anomaly_types = {anomaly.anomaly_type for anomaly in anomalies}
    assert anomaly_types == {
        VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE,
        VehicleAnomalyType.DEVICE_FAULT,
    }


def test_detect_vehicle_anomalies_reads_stored_error_codes_shape() -> None:
    """The previous reading's error_codes is the stored {"codes": [...]} JSONB shape."""
    previous_telemetry = SimpleNamespace(
        battery_temperature=None,
        battery_voltage=None,
        error_codes={"codes": ["E001"]},
    )
    message = _telemetry_envelope(errors=["E001", "E042"]).message

    anomalies = telemetry_service.detect_vehicle_anomalies(
        cast(VehicleTelemetryModel, previous_telemetry), message
    )

    assert len(anomalies) == 1
    assert anomalies[0].evidence["new_codes"] == ["E042"]


def test_detect_vehicle_anomalies_returns_empty_for_no_previous_reading() -> None:
    """A vehicle's very first message with unremarkable readings raises nothing."""
    message = _telemetry_envelope().message

    assert telemetry_service.detect_vehicle_anomalies(None, message) == []


def test_to_telemetry_snapshot_is_json_serializable() -> None:
    """to_telemetry_snapshot() only contains values the JSONB payload column can store."""
    message = _telemetry_envelope(
        battery_temperature=65.0, battery_voltage=600.0, errors=["E042"]
    ).message

    snapshot = telemetry_service.to_telemetry_snapshot(message)

    serialized = json.dumps(snapshot)  # raises TypeError on a non-JSON-safe value
    assert json.loads(serialized)["battery_temperature"] == 65.0
    assert snapshot["message_uuid"] == str(message.message_uuid)
    assert snapshot["recorded_at"] == message.recorded_at.isoformat()


@pytest.mark.asyncio
async def test_process_message_raises_one_notification_per_tripped_anomaly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_message raises one ANOMALY_ALERT notification per detector that fires (F-A4)."""
    mapping = TelematicVehicleMapping(telematic_id=uuid4(), vehicle_id=uuid4())
    previous_telemetry = SimpleNamespace(
        soc=80.0,
        battery_temperature=45.0,
        battery_voltage=650.0,
        error_codes=None,
        soh_percent=None,
    )
    created_notifications: list[dict[str, object]] = []

    async def resolve_mapping(db: AsyncSession, serial: str) -> TelematicVehicleMapping:
        return mapping

    async def insert_telemetry(db: AsyncSession, values: dict[str, object]) -> int:
        return 1

    async def previous_reading(db: AsyncSession, vehicle_id: UUID) -> SimpleNamespace:
        return previous_telemetry

    async def record_notification(db: AsyncSession, **kwargs: object) -> None:
        created_notifications.append(kwargs)

    monkeypatch.setattr(
        telematics_public_service, "resolve_mapping_by_serial", resolve_mapping
    )
    monkeypatch.setattr(telemetry_repository, "insert_telemetry", insert_telemetry)
    monkeypatch.setattr(
        telemetry_repository, "get_latest_vehicle_telemetry", previous_reading
    )
    monkeypatch.setattr(
        notifications_service, "create_notification", record_notification
    )

    result = await telemetry_service.process_message(
        _db(),
        _telemetry_envelope(soc=80.0, battery_temperature=65.0, battery_voltage=650.0),
    )

    assert result == {"processed": 1, "skipped": 0, "errors": 0}
    assert len(created_notifications) == 1
    call = created_notifications[0]
    assert call["notification_type"] is NotificationType.ANOMALY_ALERT
    assert call["severity"] is NotificationSeverity.CRITICAL
    assert call["vehicle_id"] == mapping.vehicle_id
    payload = cast(dict[str, object], call["payload"])
    assert payload["anomaly_type"] == VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE.value
    snapshot = cast(dict[str, object], payload["snapshot"])
    assert snapshot["battery_temperature"] == 65.0


def test_notification_service_builds_response_from_model() -> None:
    """to_notification_response() maps the ORM model into the response schema."""
    now = datetime.now(timezone.utc)
    vehicle_id = uuid4()
    notification = NotificationModel(
        notification_id=42,
        notification_type=NotificationType.BATTERY_ALERT,
        severity=NotificationSeverity.WARNING,
        vehicle_id=vehicle_id,
        title="Battery at 18%",
        body="Vehicle battery dropped to 18.0%, crossing the 20% threshold.",
        payload={"threshold_percent": 20.0, "soc": 18.0},
        created_at=now,
        read_at=None,
    )

    response = notifications_service.to_notification_response(notification)

    assert response.notification_id == 42
    assert response.notification_type is NotificationType.BATTERY_ALERT
    assert response.severity is NotificationSeverity.WARNING
    assert response.vehicle_id == vehicle_id
    assert response.payload["soc"] == 18.0
    assert response.read_at is None


@pytest.mark.asyncio
async def test_charging_service_runs_started_meter_ended_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The charging service runs the correct lifecycle from Started to Ended."""
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


@pytest.mark.asyncio
async def test_get_station_energy_summary_converts_wh_to_kwh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The energy summary converts the repository's Wh total to kWh (F-C5)."""
    station_id = uuid4()
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)

    async def energy_summary(db: AsyncSession, **kwargs: object) -> tuple[Decimal, int]:
        assert kwargs["station_id"] == station_id
        assert kwargs["start_time"] == start
        assert kwargs["end_time"] == end
        return Decimal("12500.000"), 3

    monkeypatch.setattr(
        charging_repository, "get_station_energy_summary", energy_summary
    )

    summary = await charging_service.get_station_energy_summary(
        _db(), station_id=station_id, start_time=start, end_time=end
    )

    assert summary.station_id == station_id
    assert summary.total_energy_kwh == pytest.approx(12.5)
    assert summary.session_count == 3


@pytest.mark.asyncio
async def test_get_station_energy_summary_rejects_naive_timestamp() -> None:
    """A start_time/end_time with no timezone is rejected before any query runs."""
    with pytest.raises(ChargingSessionInputError, match="start_time"):
        await charging_service.get_station_energy_summary(
            _db(),
            station_id=uuid4(),
            start_time=datetime(2026, 9, 1),
            end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_get_station_energy_summary_rejects_non_positive_range() -> None:
    """end_time at or before start_time is rejected."""
    same_instant = datetime(2026, 9, 1, tzinfo=timezone.utc)

    with pytest.raises(ChargingSessionInputError):
        await charging_service.get_station_energy_summary(
            _db(),
            station_id=uuid4(),
            start_time=same_instant,
            end_time=same_instant,
        )


def test_to_nearby_charging_station_response_decodes_location_and_distance() -> None:
    """to_nearby_charging_station_response() decodes lat/lon and carries distance_km (F-D1)."""
    station = _charging_station_record()

    response = charging_stations_service.to_nearby_charging_station_response(
        station, connector_count=4, distance_km=2.5
    )

    assert response.latitude == pytest.approx(10.762622)
    assert response.longitude == pytest.approx(106.660172)
    assert response.connector_count == 4
    assert response.distance_km == pytest.approx(2.5)
    assert response.power_rating_kw == pytest.approx(120.0)


@pytest.mark.asyncio
async def test_find_nearby_charging_stations_clamps_radius_and_paginates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nearby-search service clamps radius/page/page_size before querying."""
    station = _charging_station_record()
    captured: dict[str, object] = {}

    async def list_nearby(
        db: AsyncSession, **kwargs: object
    ) -> list[tuple[ChargingStationModel, float]]:
        captured.update(kwargs)
        return [(station, 1500.0)]

    async def count_nearby(db: AsyncSession, **kwargs: object) -> int:
        return 1

    async def connector_count(db: AsyncSession, station_id: UUID) -> int:
        return 2

    monkeypatch.setattr(
        charging_stations_repository, "list_nearby_stations", list_nearby
    )
    monkeypatch.setattr(
        charging_stations_repository, "count_nearby_stations", count_nearby
    )
    monkeypatch.setattr(
        charging_stations_repository, "count_connectors_by_station_id", connector_count
    )

    response = await charging_stations_service.find_nearby_charging_stations(
        _db(),
        latitude=10.762622,
        longitude=106.660172,
        radius_km=settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM + 100,
        page=0,
        page_size=0,
    )

    assert captured["radius_meters"] == pytest.approx(
        settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM * 1000
    )
    assert response.page == settings.API_DEFAULT_PAGE
    assert response.page_size == settings.API_DEFAULT_PAGE_SIZE
    assert len(response.items) == 1
    assert response.items[0].distance_km == pytest.approx(1.5)
    assert response.items[0].connector_count == 2


@pytest.mark.asyncio
async def test_update_connector_status_raises_not_found_for_inactive_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """update_connector_status() raises when the repository finds no active connector."""

    async def no_update(db: AsyncSession, connector_id: UUID, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        charging_stations_repository, "update_connector_status", no_update
    )

    with pytest.raises(ChargingConnectorNotFoundError):
        await charging_stations_service.update_connector_status(
            _db(),
            connector_id=uuid4(),
            status=ChargingConnectorStatus.OCCUPIED,
            status_updated_at=datetime.now(timezone.utc),
        )


def test_geo_location_round_trips_through_postgis_conversion() -> None:
    """Latitude/longitude survive the PostGIS geography conversion round trip.

    Regression guard for the x/y (longitude/latitude) ordering Shapely and
    PostGIS both expect - a swapped pair would still "work" (no exception)
    but silently store the wrong location. Shared by charging_stations and
    telemetry, so this test covers both domains' storage.
    """
    location = coordinates_to_location(10.762622, 106.660172)

    latitude, longitude = location_to_coordinates(location)

    assert latitude == pytest.approx(10.762622)
    assert longitude == pytest.approx(106.660172)


def test_geo_location_conversion_handles_missing_coordinates() -> None:
    """No location, or a partially-missing pair, converts to/from ``None``."""
    assert coordinates_to_location(None, None) is None
    assert coordinates_to_location(10.762622, None) is None
    assert location_to_coordinates(None) == (None, None)


def test_telemetry_latest_response_decodes_location_to_lat_lon() -> None:
    """to_vehicle_telemetry_latest_response() exposes lat/lon from the stored geography.

    Regression guard for the vehicle_telemetry storage unification
    (future.md item 9): the response contract (plain latitude/longitude)
    stays the same even though the ORM model now stores a single
    ``location`` point instead.
    """
    now = datetime.now(timezone.utc)
    record = VehicleTelemetryModel(
        message_id=1,
        message_uuid=uuid4(),
        telematic_id=uuid4(),
        telematic_serial="TBOX-TEST-001",
        vehicle_id=uuid4(),
        recorded_at=now,
        received_at=now,
        location=coordinates_to_location(10.762622, 106.660172),
        speed=None,
        heading=None,
        soc=80.0,
        battery_voltage=None,
        battery_current=None,
        battery_temperature=None,
        motor_temperature=None,
        odometer=None,
        signal_strength=None,
        error_codes=None,
        raw_payload={},
        schema_version=3,
    )

    response = telemetry_service.to_vehicle_telemetry_latest_response(record)

    assert response.latitude == pytest.approx(10.762622)
    assert response.longitude == pytest.approx(106.660172)
    assert response.vehicle_id == record.vehicle_id
    assert response.soc == 80.0
    assert response.schema_version == 3


def test_telemetry_history_point_decodes_location_to_lat_lon() -> None:
    """to_vehicle_telemetry_history_point() exposes lat/lon and drops per-row vehicle fields."""
    now = datetime.now(timezone.utc)
    record = _telemetry_record(vehicle_id=uuid4(), recorded_at=now)

    point = telemetry_service.to_vehicle_telemetry_history_point(record)

    assert point.latitude == pytest.approx(10.762622)
    assert point.longitude == pytest.approx(106.660172)
    assert point.recorded_at == now
    assert point.soc == 80.0
    assert not hasattr(point, "vehicle_id")


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_returns_ordered_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The history service returns every point the repository provides, count included."""
    vehicle_id = uuid4()
    reference = VehicleReference(vehicle_id=vehicle_id, vin="1HGBH41JXMN109186")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)
    records = [
        _telemetry_record(vehicle_id=vehicle_id, recorded_at=start, message_id=1),
        _telemetry_record(
            vehicle_id=vehicle_id, recorded_at=start + timedelta(hours=1), message_id=2
        ),
    ]

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def history(
        db: AsyncSession, **kwargs: object
    ) -> list[VehicleTelemetryModel]:
        assert kwargs["vehicle_id"] == vehicle_id
        assert kwargs["start_time"] == start
        assert kwargs["end_time"] == end
        assert kwargs["limit"] == settings.TELEMETRY_HISTORY_DEFAULT_LIMIT
        return records

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(telemetry_repository, "get_vehicle_telemetry_history", history)

    response = await telemetry_service.get_vehicle_telemetry_history_response(
        _db(), vehicle_id=vehicle_id, start_time=start, end_time=end
    )

    assert response.vehicle_id == vehicle_id
    assert response.count == 2
    assert [point.recorded_at for point in response.points] == [
        start,
        start + timedelta(hours=1),
    ]


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_raises_not_found_for_unknown_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing/soft-deleted vehicle raises TelemetryNotFoundError, not a repository call."""

    async def no_vehicle(db: AsyncSession, value: UUID) -> None:
        return None

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", no_vehicle
    )

    with pytest.raises(TelemetryNotFoundError):
        await telemetry_service.get_vehicle_telemetry_history_response(
            _db(),
            vehicle_id=uuid4(),
            start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_rejects_naive_start_time() -> (
    None
):
    """A start_time with no timezone is rejected before any lookup runs."""
    with pytest.raises(TelemetryInvalidRangeError, match="start_time"):
        await telemetry_service.get_vehicle_telemetry_history_response(
            _db(),
            vehicle_id=uuid4(),
            start_time=datetime(2026, 9, 1),
            end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_rejects_naive_end_time() -> None:
    """An end_time with no timezone is rejected before any lookup runs."""
    with pytest.raises(TelemetryInvalidRangeError, match="end_time"):
        await telemetry_service.get_vehicle_telemetry_history_response(
            _db(),
            vehicle_id=uuid4(),
            start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 2),
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_rejects_non_positive_range() -> (
    None
):
    """end_time at or before start_time is rejected."""
    same_instant = datetime(2026, 9, 1, tzinfo=timezone.utc)

    with pytest.raises(TelemetryInvalidRangeError):
        await telemetry_service.get_vehicle_telemetry_history_response(
            _db(), vehicle_id=uuid4(), start_time=same_instant, end_time=same_instant
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_rejects_range_exceeding_max_days() -> (
    None
):
    """A span longer than TELEMETRY_HISTORY_MAX_RANGE_DAYS is rejected."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    too_far = start + timedelta(days=settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS + 1)

    with pytest.raises(TelemetryInvalidRangeError):
        await telemetry_service.get_vehicle_telemetry_history_response(
            _db(), vehicle_id=uuid4(), start_time=start, end_time=too_far
        )


@pytest.mark.asyncio
async def test_get_vehicle_telemetry_history_response_clamps_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A limit above the configured max is clamped, not rejected."""
    vehicle_id = uuid4()
    reference = VehicleReference(vehicle_id=vehicle_id, vin="1HGBH41JXMN109186")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    captured_limit: dict[str, int] = {}

    async def resolve_id(db: AsyncSession, value: UUID) -> VehicleReference:
        return reference

    async def history(
        db: AsyncSession, **kwargs: object
    ) -> list[VehicleTelemetryModel]:
        captured_limit["limit"] = cast(int, kwargs["limit"])
        return []

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_id
    )
    monkeypatch.setattr(telemetry_repository, "get_vehicle_telemetry_history", history)

    await telemetry_service.get_vehicle_telemetry_history_response(
        _db(),
        vehicle_id=vehicle_id,
        start_time=start,
        end_time=end,
        limit=settings.TELEMETRY_HISTORY_MAX_LIMIT + 1000,
    )

    assert captured_limit["limit"] == settings.TELEMETRY_HISTORY_MAX_LIMIT


@pytest.mark.asyncio
async def test_check_devices_for_silence_raises_alert_for_newly_silent_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device silent past the threshold with no prior alert gets exactly one (F-J1/F-J3)."""
    vehicle_id = uuid4()
    device = _telematic_record(vehicle_id)
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

    await device_health_monitor.check_devices_for_silence(_db())

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
    device = _telematic_record(vehicle_id)
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

    await device_health_monitor.check_devices_for_silence(_db())


@pytest.mark.asyncio
async def test_check_devices_for_silence_skips_device_never_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device that has never sent telemetry is skipped, not treated as silent (F-J1/F-J3)."""
    vehicle_id = uuid4()
    device = _telematic_record(vehicle_id)

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

    await device_health_monitor.check_devices_for_silence(_db())


@pytest.mark.asyncio
async def test_check_devices_for_silence_suppresses_duplicate_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device already alerted for this exact silence episode doesn't alert again (F-J1/F-J3)."""
    vehicle_id = uuid4()
    device = _telematic_record(vehicle_id)
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

    await device_health_monitor.check_devices_for_silence(_db())


@pytest.mark.asyncio
async def test_check_devices_for_silence_realerts_after_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device that recovered then went silent again alerts a second time (F-J1/F-J3)."""
    vehicle_id = uuid4()
    device = _telematic_record(vehicle_id)
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

    await device_health_monitor.check_devices_for_silence(_db())

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
