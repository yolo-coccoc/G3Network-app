"""Smoke tests for status-report ingestion and the device health dashboard (DEV-03, DEV-04).

The repository functions and the other domains' public services are
monkeypatched; nothing here touches a database.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from aiomqtt import Message
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.service as telematics_service
import app.domains.telemetry.service as telemetry_public_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.telematics.exceptions import TelematicNotConfigurableError
from app.domains.telematics.health_rule import classify_device_health
from app.domains.telematics.ingestion.mqtt_consumer import (
    StatusReportConsumer,
    parse_serial_from_topic,
)
from app.domains.telematics.models import TelematicModel, TelematicStatusReportModel
from app.domains.telematics.schemas import (
    TelematicConfigPushRequest,
    TelematicStatusEnvelope,
    TelematicStatusMessage,
)
from app.domains.telematics.types import TelematicHealthState, TelematicStatus
from app.domains.telemetry.types import VehicleLiveStatusReference
from app.domains.vehicles.types import VehicleReference
from tests.builders import build_telematic_record, fake_db_session
from tests.principals import build_internal_principal

FULL_STATUS_PAYLOAD = {
    "status": "online",
    "firmware_version": "1.2.3",
    "telemetry_interval_seconds": 10,
    "sim": {"iccid": "8984049000001234567", "is_esim": False, "data_status": "active"},
    "supply_voltage_v": 24.3,
    "signal_dbm": -78,
    "storage_used_percent": 41.5,
    "gnss_status": "FIX",
    "timestamp": "2026-07-25T10:30:00Z",
    "vendor_extra": {"anything": 1},
}


def test_status_message_reads_the_provisional_fields_and_ignores_unknown_ones() -> None:
    """A full message maps to a report row; an unknown key is ignored (mqtt-spec 2.2)."""
    message = TelematicStatusMessage.model_validate(FULL_STATUS_PAYLOAD)
    telematic_id = uuid4()
    received_at = datetime(2026, 7, 25, 10, 30, 2, tzinfo=timezone.utc)

    values = message.to_status_report_values(telematic_id, received_at)

    assert message.has_health_fields()
    assert values["telematic_id"] == telematic_id
    assert values["firmware_version"] == "1.2.3"
    assert values["sim_iccid"] == "8984049000001234567"
    assert values["sim_data_status"] == "ACTIVE"
    assert values["is_esim"] is False
    assert str(values["supply_voltage_v"]) == "24.3"
    assert values["signal_dbm"] == -78
    assert values["reported_at"] == datetime(2026, 7, 25, 10, 30, tzinfo=timezone.utc)
    assert values["received_at"] == received_at


def test_status_message_is_tolerant_of_wrong_types_and_missing_timestamp() -> None:
    """A wrong-typed or out-of-range field becomes empty; no timestamp means receive time."""
    message = TelematicStatusMessage.model_validate(
        {
            "firmware_version": "9.9",
            "signal_dbm": "strong",
            "supply_voltage_v": 5000,
            "storage_used_percent": 140,
            "sim": "none",
            "timestamp": "yesterday",
        }
    )
    received_at = datetime(2026, 7, 25, 10, 30, 2, tzinfo=timezone.utc)

    values = message.to_status_report_values(uuid4(), received_at)

    assert values["firmware_version"] == "9.9"
    assert values["signal_dbm"] is None
    assert values["supply_voltage_v"] is None
    assert values["storage_used_percent"] is None
    assert values["sim_iccid"] is None
    assert values["reported_at"] == received_at


def test_status_message_without_health_fields_is_not_a_report() -> None:
    """A bare Last Will notice carries no health field."""
    assert not TelematicStatusMessage.model_validate(
        {"status": "offline"}
    ).has_health_fields()


@pytest.mark.parametrize(
    ("topic", "expected_serial"),
    [
        ("g3network/telematics/TBOX-1/status", "TBOX-1"),
        ("g3network/telematics//status", None),
        ("g3network/telematics/status", None),
    ],
)
def test_parse_serial_from_topic(topic: str, expected_serial: str | None) -> None:
    """The serial is the third level of a four-level status topic."""
    assert parse_serial_from_topic(topic) == expected_serial


def _message(
    payload: bytes, topic: str = "g3network/telematics/TBOX-1/status"
) -> Message:
    """Build a stand-in for an aiomqtt message."""
    return cast(Message, SimpleNamespace(payload=payload, topic=topic))


@pytest.mark.asyncio
async def test_status_consumer_queues_valid_and_drops_malformed_messages() -> None:
    """A usable message is queued with its serial; garbage never escapes the handler."""
    queue: asyncio.Queue[TelematicStatusEnvelope] = asyncio.Queue()
    consumer = StatusReportConsumer(queue)

    await consumer._handle_message(_message(b"\xff\xfe not utf-8"))
    await consumer._handle_message(_message(b"{not json"))
    await consumer._handle_message(_message(b"[1, 2]"))
    await consumer._handle_message(_message(b"{}", topic="g3network/telematics/status"))
    assert queue.empty()

    await consumer._handle_message(_message(json.dumps(FULL_STATUS_PAYLOAD).encode()))

    envelope = queue.get_nowait()
    assert envelope.telematic_serial == "TBOX-1"
    assert envelope.message.firmware_version == "1.2.3"


@pytest.mark.asyncio
async def test_record_status_report_stores_one_row_and_skips_notices_and_unknown_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a known device's message with health fields is stored (TX-10)."""
    telematic_record = build_telematic_record(uuid4())
    inserted: list[dict[str, object]] = []

    async def find_device(
        db_session: AsyncSession, serial: str
    ) -> TelematicModel | None:
        return telematic_record if serial == telematic_record.telematic_serial else None

    async def insert_report(
        db_session: AsyncSession, values: dict[str, object]
    ) -> TelematicStatusReportModel:
        inserted.append(values)
        return TelematicStatusReportModel(**values)

    monkeypatch.setattr(telematics_repository, "find_by_serial", find_device)
    monkeypatch.setattr(telematics_repository, "insert_status_report", insert_report)
    session = fake_db_session()

    stored = await telematics_service.record_status_report(
        session,
        TelematicStatusEnvelope(
            telematic_record.telematic_serial,
            TelematicStatusMessage.model_validate(FULL_STATUS_PAYLOAD),
        ),
    )
    notice = await telematics_service.record_status_report(
        session,
        TelematicStatusEnvelope(
            telematic_record.telematic_serial,
            TelematicStatusMessage.model_validate({"status": "offline"}),
        ),
    )
    unknown = await telematics_service.record_status_report(
        session,
        TelematicStatusEnvelope(
            "UNKNOWN", TelematicStatusMessage.model_validate(FULL_STATUS_PAYLOAD)
        ),
    )

    assert stored == {"processed": 1, "skipped": 0, "errors": 0}
    assert notice["skipped"] == 1
    assert unknown["skipped"] == 1
    assert len(inserted) == 1
    assert inserted[0]["telematic_id"] == telematic_record.telematic_id


@pytest.mark.parametrize(
    ("kwargs", "expected_state"),
    [
        ({"status": TelematicStatus.INACTIVE}, TelematicHealthState.INACTIVE),
        ({"is_mounted": False}, TelematicHealthState.NOT_MOUNTED),
        ({"last_seen_at": None}, TelematicHealthState.NO_DATA),
        ({"is_silent": True}, TelematicHealthState.SILENT),
        ({"sim_data_status": "NO_DATA"}, TelematicHealthState.ATTENTION),
        ({"gnss_status": "ANTENNA_FAULT"}, TelematicHealthState.ATTENTION),
        ({}, TelematicHealthState.HEALTHY),
    ],
)
def test_classify_device_health(
    kwargs: dict[str, object], expected_state: TelematicHealthState
) -> None:
    """The first rule that holds decides the health state (DEV-04)."""
    arguments: dict[str, object] = {
        "status": TelematicStatus.ACTIVE,
        "is_mounted": True,
        "last_seen_at": datetime.now(timezone.utc),
        "is_silent": False,
        "sim_data_status": "ACTIVE",
        "gnss_status": "FIX",
    }
    arguments.update(kwargs)

    assert classify_device_health(**arguments) is expected_state  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_health_summary_counts_states_and_healthy_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The summary counts each state and the healthy share of reporting devices."""
    healthy = build_telematic_record(uuid4())
    silent = build_telematic_record(uuid4())
    stock = build_telematic_record(uuid4())
    stock.vehicle_id = None
    records = {record.vehicle_id: record for record in (healthy, silent)}
    now = datetime.now(timezone.utc)

    async def list_in_scope(
        db_session: AsyncSession, *, organization_id: UUID | None = None
    ) -> list[TelematicModel]:
        return [healthy, silent, stock]

    async def no_report(
        db_session: AsyncSession, telematic_id: UUID
    ) -> TelematicStatusReportModel | None:
        return None

    async def live_vehicle(
        db_session: AsyncSession, vehicle_id: UUID
    ) -> VehicleReference:
        return VehicleReference(
            organization_id=uuid4(),
            vehicle_id=vehicle_id,
            vin="1HGBH41JXMN109186",
            battery_capacity_kwh=None,
        )

    async def last_telemetry_at(db_session: AsyncSession, vehicle_id: UUID) -> datetime:
        return now if records[vehicle_id] is healthy else now - timedelta(days=2)

    async def live_status(
        db_session: AsyncSession, vehicle_id: UUID
    ) -> VehicleLiveStatusReference:
        return VehicleLiveStatusReference(
            vehicle_id=vehicle_id,
            latitude=10.0,
            longitude=106.0,
            recorded_at=now,
            received_at=now,
            is_online=records[vehicle_id] is healthy,
            signal_strength_dbm=None,
        )

    monkeypatch.setattr(telematics_repository, "list_in_scope", list_in_scope)
    monkeypatch.setattr(telematics_repository, "find_latest_status_report", no_report)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", live_vehicle
    )
    monkeypatch.setattr(
        telemetry_public_service, "resolve_last_telemetry_at", last_telemetry_at
    )
    monkeypatch.setattr(
        telemetry_public_service, "resolve_vehicle_live_status", live_status
    )

    summary = await telematics_service.get_device_health_summary(
        fake_db_session(), principal=build_internal_principal()
    )

    assert summary.total_count == 3
    assert summary.healthy_count == 1
    assert summary.silent_count == 1
    assert summary.not_mounted_count == 1
    assert summary.online_count == 1
    assert summary.healthy_percent == 50.0


@pytest.mark.asyncio
async def test_config_push_refuses_a_device_that_is_not_mounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a mounted, ACTIVE device receives configuration (TX-08)."""
    stock_device = build_telematic_record(uuid4())
    stock_device.vehicle_id = None

    async def get_device(
        db_session: AsyncSession,
        telematic_id: UUID,
        *,
        organization_id: UUID | None = None,
    ) -> TelematicModel:
        return stock_device

    monkeypatch.setattr(telematics_repository, "get_by_id", get_device)

    with pytest.raises(TelematicNotConfigurableError):
        await telematics_service.push_telematic_config(
            fake_db_session(),
            stock_device.telematic_id,
            TelematicConfigPushRequest(telemetry_interval_seconds=30),
            principal=build_internal_principal(),
        )
