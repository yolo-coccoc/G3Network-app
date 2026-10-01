"""Smoke tests for OCPP 1.6J BootNotification/Heartbeat and the station liveness fields."""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from ocpp.v16.enums import RegistrationStatus

import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
from app.domains.charging_stations.exceptions import (
    ChargingOcppMessageInputError,
    ChargingStationNotFoundError,
)
from app.domains.charging_stations.models import ChargingStationModel
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from app.domains.charging_stations.ocpp.parsing import (
    format_ocpp_timestamp,
    parse_ocpp_timestamp,
)
from app.domains.charging_stations.types import ChargingStationMaintenanceStatus
from app.libs.common.config import settings
from tests.fakes import FakeSessionFactory

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def _charge_point() -> OCPP16ChargePoint:
    return OCPP16ChargePoint(
        "LSC",
        object(),  # type: ignore[arg-type]
        FakeSessionFactory(),  # type: ignore[arg-type]
    )


def _station(**overrides: Any) -> ChargingStationModel:
    values: dict[str, Any] = {
        "station_id": uuid4(),
        "ocpp_identity": "LSC",
        "display_name": "Station",
        "location": None,
        "power_rating_kw": None,
        "connector_standard": None,
        "operating_hours": None,
        "maintenance_status": ChargingStationMaintenanceStatus.OPERATIONAL,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    values.update(overrides)
    return ChargingStationModel(**values)


# --- adapter handlers -------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_notification_accepts_with_only_the_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vendor and model are the only mandatory 1.6 fields; the rest may be missing."""
    captured: dict[str, Any] = {}

    async def fake_record(db: object, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(ocpp_state_service, "record_charger_boot", fake_record)

    response = await _charge_point().on_boot_notification(
        charge_point_vendor="Willdigits", charge_point_model="240kW"
    )

    assert response.status == RegistrationStatus.accepted
    assert response.interval == settings.CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS
    assert captured["ocpp_identity"] == "LSC"
    assert captured["vendor"] == "Willdigits"
    assert captured["model"] == "240kW"
    assert captured["serial_number"] is None
    assert captured["firmware_version"] is None
    assert captured["booted_at"].utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_boot_notification_returns_a_parseable_current_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The charger syncs its clock from currentTime, so it must be a valid UTC timestamp."""

    async def fake_record(db: object, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(ocpp_state_service, "record_charger_boot", fake_record)
    before = datetime.now(timezone.utc) - timedelta(seconds=1)

    response = await _charge_point().on_boot_notification(
        charge_point_vendor="V", charge_point_model="M"
    )

    assert response.current_time.endswith("Z")
    assert (
        before
        <= parse_ocpp_timestamp(response.current_time)
        <= datetime.now(timezone.utc) + timedelta(seconds=1)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("point_serial", "box_serial", "expected"),
    [
        ("POINT-1", "BOX-1", "POINT-1"),  # chargePointSerialNumber wins
        (None, "BOX-1", "BOX-1"),  # legacy chargeBoxSerialNumber as fallback
        (None, None, None),
    ],
)
async def test_boot_notification_prefers_charge_point_serial_number(
    monkeypatch: pytest.MonkeyPatch,
    point_serial: str | None,
    box_serial: str | None,
    expected: str | None,
) -> None:
    """Both serial fields are optional; the newer one is preferred."""
    captured: dict[str, Any] = {}

    async def fake_record(db: object, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(ocpp_state_service, "record_charger_boot", fake_record)

    await _charge_point().on_boot_notification(
        charge_point_vendor="V",
        charge_point_model="M",
        firmware_version="FW-1",
        charge_point_serial_number=point_serial,
        charge_box_serial_number=box_serial,
    )

    assert captured["serial_number"] == expected
    assert captured["firmware_version"] == "FW-1"


@pytest.mark.asyncio
async def test_boot_notification_for_unknown_station_propagates_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A boot that cannot be stored is not accepted (it becomes a CALLERROR)."""

    async def fake_record(db: object, **kwargs: Any) -> None:
        raise ChargingStationNotFoundError("gone")

    monkeypatch.setattr(ocpp_state_service, "record_charger_boot", fake_record)

    with pytest.raises(ChargingStationNotFoundError):
        await _charge_point().on_boot_notification(
            charge_point_vendor="V", charge_point_model="M"
        )


@pytest.mark.asyncio
async def test_heartbeat_returns_the_current_time() -> None:
    """A heartbeat reply carries the server time and touches nothing else."""
    response = await _charge_point().on_heartbeat()

    assert response.current_time.endswith("Z")
    assert abs(
        (parse_ocpp_timestamp(response.current_time) - datetime.now(timezone.utc))
    ) < timedelta(seconds=2)


# --- service ----------------------------------------------------------------


def _patch_boot_repository(
    monkeypatch: pytest.MonkeyPatch, station: Any
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []

    async def fake_get(db: object, identity: str, **_: Any) -> Any:
        return station

    async def fake_update(db: object, station_id: Any, **kwargs: Any) -> bool:
        updates.append({"station_id": station_id, **kwargs})
        return True

    monkeypatch.setattr(
        charging_stations_repository, "get_station_by_identity", fake_get
    )
    monkeypatch.setattr(ocpp_state_repository, "update_station_boot_info", fake_update)
    return updates


@pytest.mark.asyncio
async def test_record_charger_boot_overwrites_device_fields_without_warning_on_first_boot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The very first boot stores the baseline and warns about nothing."""
    station = SimpleNamespace(station_id=uuid4(), firmware_version=None)
    updates = _patch_boot_repository(monkeypatch, station)

    with caplog.at_level(logging.WARNING):
        await ocpp_state_service.record_charger_boot(
            object(),  # type: ignore[arg-type]
            ocpp_identity="LSC",
            vendor="V",
            model="M",
            serial_number="S1",
            firmware_version="FW-1",
            booted_at=NOW,
        )

    assert updates[0]["firmware_version"] == "FW-1"
    assert updates[0]["booted_at"] == NOW
    assert not [r for r in caplog.records if "firmware" in r.getMessage().lower()]


@pytest.mark.asyncio
async def test_record_charger_boot_warns_but_accepts_when_the_update_matches_no_row(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A station deleted between load and update is logged, never raised."""
    station = SimpleNamespace(station_id=uuid4(), firmware_version=None)

    async def fake_get(db: object, identity: str, **_: Any) -> Any:
        return station

    async def no_row(db: object, station_id: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(
        charging_stations_repository, "get_station_by_identity", fake_get
    )
    monkeypatch.setattr(ocpp_state_repository, "update_station_boot_info", no_row)

    with caplog.at_level(logging.WARNING):
        await ocpp_state_service.record_charger_boot(
            object(),  # type: ignore[arg-type]
            ocpp_identity="LSC",
            vendor="V",
            model="M",
            serial_number=None,
            firmware_version=None,
            booted_at=NOW,
        )

    assert [
        record
        for record in caplog.records
        if record.getMessage() == "Charger boot info matched no active station"
    ]


@pytest.mark.asyncio
async def test_record_charger_boot_warns_when_firmware_changes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A different firmware version than the stored baseline is logged as a warning."""
    station = SimpleNamespace(station_id=uuid4(), firmware_version="FW-1")
    updates = _patch_boot_repository(monkeypatch, station)

    with caplog.at_level(logging.WARNING):
        await ocpp_state_service.record_charger_boot(
            object(),  # type: ignore[arg-type]
            ocpp_identity="LSC",
            vendor="V",
            model="M",
            serial_number=None,
            firmware_version="FW-2",
            booted_at=NOW,
        )

    warning = next(r for r in caplog.records if "firmware" in r.getMessage().lower())
    assert warning.levelno == logging.WARNING
    assert warning.previous_firmware_version == "FW-1"  # type: ignore[attr-defined]
    assert warning.firmware_version == "FW-2"  # type: ignore[attr-defined]
    assert (
        updates[0]["firmware_version"] == "FW-2"
    )  # the new value becomes the baseline


@pytest.mark.asyncio
async def test_record_charger_boot_does_not_warn_when_firmware_is_unchanged_or_unreported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Same version, or a charger that stops reporting one, is not a firmware swap."""
    station = SimpleNamespace(station_id=uuid4(), firmware_version="FW-1")
    _patch_boot_repository(monkeypatch, station)

    with caplog.at_level(logging.WARNING):
        for reported in ("FW-1", None):
            await ocpp_state_service.record_charger_boot(
                object(),  # type: ignore[arg-type]
                ocpp_identity="LSC",
                vendor="V",
                model="M",
                serial_number=None,
                firmware_version=reported,
                booted_at=NOW,
            )

    assert not [r for r in caplog.records if "firmware" in r.getMessage().lower()]


@pytest.mark.asyncio
async def test_record_charger_boot_rejects_missing_station_and_naive_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A soft-deleted/unknown station and a timezone-less boot time are both refused."""
    _patch_boot_repository(monkeypatch, None)

    with pytest.raises(ChargingStationNotFoundError):
        await ocpp_state_service.record_charger_boot(
            object(),  # type: ignore[arg-type]
            ocpp_identity="LSC",
            vendor="V",
            model="M",
            serial_number=None,
            firmware_version=None,
            booted_at=NOW,
        )
    with pytest.raises(ChargingOcppMessageInputError):
        await ocpp_state_service.record_charger_boot(
            object(),  # type: ignore[arg-type]
            ocpp_identity="LSC",
            vendor="V",
            model="M",
            serial_number=None,
            firmware_version=None,
            booted_at=datetime(2026, 9, 24, 10, 0),
        )


# --- is_online ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("seen_ago_seconds", "expected"),
    [
        (None, False),  # never connected
        (0, True),
        (settings.CHARGING_OFFLINE_TIMEOUT_SECONDS - 1, True),
        (settings.CHARGING_OFFLINE_TIMEOUT_SECONDS, True),  # boundary counts as online
        (settings.CHARGING_OFFLINE_TIMEOUT_SECONDS + 1, False),
    ],
)
def test_station_response_derives_is_online_from_last_seen(
    seen_ago_seconds: float | None, expected: bool
) -> None:
    """Online means a frame arrived within the offline timeout; never stored."""
    last_seen = (
        None if seen_ago_seconds is None else NOW - timedelta(seconds=seen_ago_seconds)
    )
    station = _station(last_seen_at=last_seen)

    response = charging_stations_service.to_charging_station_response(
        station, connector_count=2, available_connector_count=1, now=NOW
    )

    assert response.is_online is expected
    assert response.last_seen_at == last_seen


def test_station_response_carries_the_device_fields() -> None:
    """Device info reported at boot is exposed on the station response."""
    station = _station(
        ocpp_protocol_version="ocpp1.6",
        vendor="Willdigits",
        model="240kW",
        serial_number="SN-1",
        firmware_version="FW-1",
        last_boot_at=NOW,
        last_seen_at=NOW,
    )

    response = charging_stations_service.to_charging_station_response(
        station, connector_count=2, available_connector_count=1, now=NOW
    )

    assert (
        response.ocpp_protocol_version,
        response.vendor,
        response.model,
        response.serial_number,
        response.firmware_version,
        response.last_boot_at,
    ) == ("ocpp1.6", "Willdigits", "240kW", "SN-1", "FW-1", NOW)


# --- timestamp formatting ---------------------------------------------------


def test_format_ocpp_timestamp_returns_utc_with_milliseconds_and_z() -> None:
    """Offsets are converted to UTC and rendered in the OCPP form."""
    local = datetime(2026, 9, 24, 17, 0, 0, 123000, tzinfo=timezone(timedelta(hours=7)))

    assert format_ocpp_timestamp(local) == "2026-09-24T10:00:00.123Z"
    assert parse_ocpp_timestamp(format_ocpp_timestamp(local)) == local


def test_format_ocpp_timestamp_rejects_naive_datetimes() -> None:
    """A timestamp without a timezone is never sent to a charger."""
    with pytest.raises(ValueError, match="timezone"):
        format_ocpp_timestamp(datetime(2026, 9, 24, 10, 0))
