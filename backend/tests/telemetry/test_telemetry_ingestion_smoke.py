"""Smoke tests for telemetry ingestion and the alert detectors (F-A1, F-A2, F-A3, F-A4)."""

import json
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.service as charging_stations_service
import app.domains.notifications.service as notifications_service
import app.domains.telematics.service as telematics_public_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.domains.charging_stations.types import (
    NearestChargingStation,
)
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.telematics.types import TelematicVehicleMapping
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.types import (
    BatteryAlertLevel,
    VehicleAnomalyType,
)
from tests.builders import build_telemetry_envelope, fake_db_session


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

    result = await telemetry_service.process_message(
        fake_db_session(), build_telemetry_envelope()
    )

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

    result = await telemetry_service.process_message(
        fake_db_session(), build_telemetry_envelope()
    )

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
        fake_db_session(), build_telemetry_envelope(soc=18.0)
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
        fake_db_session(),
        build_telemetry_envelope(soc=80.0, soh_percent=68.0, cycle_count=142),
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
    message = build_telemetry_envelope(
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
    message = build_telemetry_envelope(errors=["E001", "E042"]).message

    anomalies = telemetry_service.detect_vehicle_anomalies(
        cast(VehicleTelemetryModel, previous_telemetry), message
    )

    assert len(anomalies) == 1
    assert anomalies[0].evidence["new_codes"] == ["E042"]


def test_detect_vehicle_anomalies_returns_empty_for_no_previous_reading() -> None:
    """A vehicle's very first message with unremarkable readings raises nothing."""
    message = build_telemetry_envelope().message

    assert telemetry_service.detect_vehicle_anomalies(None, message) == []


def test_to_telemetry_snapshot_is_json_serializable() -> None:
    """to_telemetry_snapshot() only contains values the JSONB payload column can store."""
    message = build_telemetry_envelope(
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
        fake_db_session(),
        build_telemetry_envelope(
            soc=80.0, battery_temperature=65.0, battery_voltage=650.0
        ),
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
