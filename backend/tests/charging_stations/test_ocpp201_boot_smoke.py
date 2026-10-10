"""Smoke tests for OCPP 2.0.1 BootNotification/Heartbeat and 2.0.1 liveness (F-G2)."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from ocpp.v201.enums import RegistrationStatusEnumType

import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
from app.domains.charging_stations.exceptions import ChargingStationNotFoundError
from app.domains.charging_stations.ocpp.ocpp201_charge_point import (
    OCPP201ChargePoint,
)
from app.domains.charging_stations.ocpp.parsing import parse_ocpp_timestamp
from app.domains.charging_stations.types import OcppMessageDirection
from app.libs.common.config import settings
from tests.fakes import FakeSessionFactory

STATION_ID = uuid4()


class _SentFrames:
    """Stand-in connection that keeps every frame the adapter sends.

    Attributes:
        frames: The sent frames, in order.
    """

    def __init__(self) -> None:
        self.frames: list[str] = []

    async def send(self, message: str) -> None:
        self.frames.append(message)


def _charge_point(connection: object | None = None) -> OCPP201ChargePoint:
    return OCPP201ChargePoint(
        "CS-201",
        connection if connection is not None else object(),  # type: ignore[arg-type]
        FakeSessionFactory(),  # type: ignore[arg-type]
        station_id=STATION_ID,
    )


@pytest.mark.asyncio
async def test_boot_notification_stores_the_station_identity_and_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 2.0.1 boot stores vendor/model/serial/firmware like a 1.6J boot does."""
    captured: dict[str, Any] = {}

    async def fake_record(db: object, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(ocpp_state_service, "record_charger_boot", fake_record)

    response = await _charge_point().on_boot_notification(
        charging_station={
            "model": "SIM-201",
            "vendor_name": "G3Network-Sim",
            "serial_number": "SN-201",
            "firmware_version": "1.0",
        },
        reason="PowerUp",
    )

    assert response.status == RegistrationStatusEnumType.accepted
    assert response.interval == settings.CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS
    assert response.current_time.endswith("Z")
    assert captured["station_id"] == STATION_ID
    assert (captured["vendor"], captured["model"]) == ("G3Network-Sim", "SIM-201")
    assert (captured["serial_number"], captured["firmware_version"]) == (
        "SN-201",
        "1.0",
    )
    assert captured["booted_at"].utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_boot_notification_accepts_with_only_the_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serial number and firmware are optional in 2.0.1 and stored as NULL."""
    captured: dict[str, Any] = {}

    async def fake_record(db: object, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(ocpp_state_service, "record_charger_boot", fake_record)

    await _charge_point().on_boot_notification(
        charging_station={"model": "M", "vendor_name": "V"}, reason="PowerUp"
    )

    assert (captured["serial_number"], captured["firmware_version"]) == (None, None)


@pytest.mark.asyncio
async def test_boot_notification_for_a_deleted_station_propagates_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A boot that cannot be stored is not accepted (it becomes a CALLERROR)."""

    async def fake_record(db: object, **kwargs: Any) -> None:
        raise ChargingStationNotFoundError("gone")

    monkeypatch.setattr(ocpp_state_service, "record_charger_boot", fake_record)

    with pytest.raises(ChargingStationNotFoundError):
        await _charge_point().on_boot_notification(
            charging_station={"model": "M", "vendor_name": "V"}, reason="PowerUp"
        )


@pytest.mark.asyncio
async def test_heartbeat_returns_the_current_time() -> None:
    """A 2.0.1 heartbeat reply carries the server time and stores nothing."""
    response = await _charge_point().on_heartbeat()

    assert abs(
        parse_ocpp_timestamp(response.current_time) - datetime.now(timezone.utc)
    ) < timedelta(seconds=2)


@pytest.mark.asyncio
async def test_boot_and_heartbeat_frames_pass_the_library_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routed through python-ocpp (schema validation on), both get a CALLRESULT."""

    async def fake_record(db: object, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(ocpp_state_service, "record_charger_boot", fake_record)
    connection = _SentFrames()
    charge_point = _charge_point(connection)

    await charge_point.route_message(
        json.dumps(
            [
                2,
                "boot-1",
                "BootNotification",
                {
                    "chargingStation": {"model": "SIM-201", "vendorName": "Sim"},
                    "reason": "PowerUp",
                },
            ]
        )
    )
    await charge_point.route_message(json.dumps([2, "hb-1", "Heartbeat", {}]))

    boot_answer = json.loads(connection.frames[0])
    heartbeat_answer = json.loads(connection.frames[1])
    assert boot_answer[:2] == [3, "boot-1"]
    assert boot_answer[2]["status"] == "Accepted"
    assert boot_answer[2]["interval"] == (
        settings.CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS
    )
    assert heartbeat_answer[:2] == [3, "hb-1"]
    assert "currentTime" in heartbeat_answer[2]


@pytest.mark.asyncio
async def test_an_inbound_201_frame_stamps_liveness_like_a_16_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw log marks a 2.0.1 station seen, so it can report is_online."""
    touches: list[dict[str, Any]] = []

    async def fake_insert(db: object, **kwargs: Any) -> None:
        return None

    async def fake_touch(db: object, station_id: Any, **kwargs: Any) -> None:
        touches.append({"station_id": station_id, **kwargs})

    monkeypatch.setattr(ocpp_state_repository, "insert_ocpp_message", fake_insert)
    monkeypatch.setattr(ocpp_state_repository, "touch_station_seen", fake_touch)
    station_id = uuid4()
    received_at = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)

    await ocpp_state_service.record_ocpp_message(
        object(),  # type: ignore[arg-type]
        station_id=station_id,
        occurred_at=received_at,
        ocpp_subprotocol="ocpp2.0.1",
        direction=OcppMessageDirection.CP_TO_CSMS,
        raw_frame='[2,"hb-1","Heartbeat",{}]',
    )

    assert touches == [
        {
            "station_id": station_id,
            "seen_at": received_at,
            "ocpp_protocol_version": "ocpp2.0.1",
        }
    ]
