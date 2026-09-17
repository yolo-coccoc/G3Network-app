"""Smoke test for the backend's main service workflows."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
import app.domains.charging_stations.service as charging_stations_service
import app.domains.notifications.service as notifications_service
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
from app.domains.charging_stations.types import NearestChargingStation
from app.domains.notifications.models import NotificationModel
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import TelematicCreateRequest
from app.domains.telematics.types import TelematicStatus, TelematicVehicleMapping
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.schemas import TelemetryEnvelope, TelemetryMessage
from app.domains.telemetry.types import BatteryAlertLevel
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.schemas import VehicleCreateRequest
from app.domains.vehicles.types import VehicleReference, VehicleStatus
from app.libs.common.geo import coordinates_to_location, location_to_coordinates


def _db() -> AsyncSession:
    """Create a placeholder session for a unit test that doesn't touch the database."""
    return cast(AsyncSession, object())


def _vehicle_record() -> VehicleModel:
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


def _telemetry_envelope(soc: float = 80.0) -> TelemetryEnvelope:
    """Create a valid telemetry envelope for process_message."""
    message = TelemetryMessage.model_validate(
        {
            "message_uuid": str(uuid4()),
            "telematic_serial": "TBOX-TEST-001",
            "recorded_at": "2026-08-26T10:00:00Z",
            "location": {"latitude": 10.8, "longitude": 106.7},
            "battery": {"soc": soc},
        }
    )
    return TelemetryEnvelope(message=message, raw_payload={"test": True})


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

    monkeypatch.setattr(
        telematics_public_service,
        "resolve_mapping_by_serial",
        resolve_mapping,
    )
    monkeypatch.setattr(telemetry_repository, "insert_telemetry", insert_telemetry)
    monkeypatch.setattr(
        telemetry_repository, "get_latest_vehicle_telemetry", no_previous_telemetry
    )

    result = await telemetry_service.process_message(_db(), _telemetry_envelope())

    assert result == {"processed": 1, "skipped": 0, "errors": 0}


@pytest.mark.asyncio
async def test_telemetry_service_raises_battery_alert_on_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process_message raises exactly one notification when SOC crosses a threshold (F-A2)."""
    mapping = TelematicVehicleMapping(telematic_id=uuid4(), vehicle_id=uuid4())
    previous_telemetry = SimpleNamespace(soc=25.0)
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
