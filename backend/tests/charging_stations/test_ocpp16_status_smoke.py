"""Smoke tests for OCPP 1.6J StatusNotification, connector 0 and the widened status enum."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from ocpp.v16.enums import ChargePointErrorCode, ChargePointStatus

import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingOcppMessageInputError,
    ChargingStationNotFoundError,
)
from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingStationModel,
)
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from app.domains.charging_stations.ocpp.ocpp201_charge_point import OCPP201ChargePoint
from app.domains.charging_stations.schemas import ChargingConnectorResponse
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingStationMaintenanceStatus,
)

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
STATION_ID, EVSE_ID, CONNECTOR_ID = uuid4(), uuid4(), uuid4()


class _CountingFactory:
    """Session factory that counts how many transactions were opened."""

    def __init__(self) -> None:
        self.entered = 0

    def begin(self) -> "_CountingFactory":
        return self

    async def __aenter__(self) -> object:
        self.entered += 1
        return object()

    async def __aexit__(self, *_: object) -> bool:
        return False


def _charge_point(factory: _CountingFactory | None = None) -> OCPP16ChargePoint:
    return OCPP16ChargePoint(
        "LSC",
        object(),  # type: ignore[arg-type]
        factory or _CountingFactory(),  # type: ignore[arg-type]
    )


def _patch_service(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[dict[str, Any]]]:
    """Replace the service functions the handler calls and record their arguments."""
    calls: dict[str, list[dict[str, Any]]] = {
        "resolve": [],
        "connector": [],
        "charger": [],
    }

    async def fake_resolve(
        db: object, ocpp_identity: str, ocpp_connector_id: int
    ) -> tuple[Any, Any, Any]:
        calls["resolve"].append(
            {"ocpp_identity": ocpp_identity, "ocpp_connector_id": ocpp_connector_id}
        )
        return STATION_ID, EVSE_ID, CONNECTOR_ID

    async def fake_connector(db: object, **kwargs: Any) -> None:
        calls["connector"].append(kwargs)

    async def fake_charger(db: object, **kwargs: Any) -> None:
        calls["charger"].append(kwargs)

    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp16_topology", fake_resolve)
    monkeypatch.setattr(ocpp_state_service, "update_connector_status", fake_connector)
    monkeypatch.setattr(ocpp_state_service, "update_charger_status", fake_charger)
    return calls


# --- the widened status enum ------------------------------------------------


def test_connector_status_enum_carries_every_1_6_status_plus_2_0_1_occupied() -> None:
    """Ten values: the nine 1.6J statuses, plus 2.0.1's Occupied (still written)."""
    values = {member.value for member in ChargingConnectorStatus}

    assert values == {status.value for status in ChargePointStatus} | {"Occupied"}
    assert len(values) == 10


@pytest.mark.parametrize("status", [member.value for member in ChargePointStatus])
def test_every_1_6_status_label_converts_directly_to_the_enum(status: str) -> None:
    """No mapping function is needed: the enum values are the 1.6 labels."""
    assert ChargingConnectorStatus(status).value == status


# --- handler: guns ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [member.value for member in ChargePointStatus])
async def test_status_notification_stores_each_1_6_status_exactly_as_reported(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """A gun's status is kept as sent, not folded into a smaller vocabulary."""
    calls = _patch_service(monkeypatch)

    await _charge_point().on_status_notification(
        connector_id=1,
        error_code="NoError",
        status=status,
        timestamp="2026-09-24T10:00:00Z",
    )

    assert calls["connector"][0]["status"] is ChargingConnectorStatus(status)
    assert calls["connector"][0]["status_updated_at"] == NOW
    assert calls["charger"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code", [member.value for member in ChargePointErrorCode]
)
async def test_status_notification_stores_every_standard_error_code_as_sent(
    monkeypatch: pytest.MonkeyPatch, error_code: str
) -> None:
    """All 16 standard error codes (NoError included) are stored unchanged."""
    calls = _patch_service(monkeypatch)

    await _charge_point().on_status_notification(
        connector_id=2,
        error_code=error_code,
        status="Faulted",
        timestamp="2026-09-24T10:00:00Z",
        vendor_error_code="23",
        info="details",
    )

    stored = calls["connector"][0]
    assert stored["error_code"] == error_code
    assert stored["vendor_error_code"] == "23"
    assert stored["status_info"] == "details"


@pytest.mark.asyncio
async def test_status_notification_maps_gun_n_to_evse_n_connector_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The topology resolver receives the 1.6 connector number and the identity."""
    calls = _patch_service(monkeypatch)

    await _charge_point().on_status_notification(
        connector_id=2, error_code="NoError", status="Available"
    )

    assert calls["resolve"] == [{"ocpp_identity": "LSC", "ocpp_connector_id": 2}]
    assert calls["connector"][0]["connector_id"] == CONNECTOR_ID


@pytest.mark.asyncio
async def test_status_notification_uses_receive_time_when_timestamp_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """timestamp is optional in 1.6, so the server's own time is used instead."""
    calls = _patch_service(monkeypatch)
    before = datetime.now(timezone.utc)

    await _charge_point().on_status_notification(
        connector_id=1, error_code="NoError", status="Charging"
    )

    stored_at = calls["connector"][0]["status_updated_at"]
    assert stored_at.utcoffset() == timedelta(0)
    assert before <= stored_at <= datetime.now(timezone.utc)


# --- handler: connector 0 -----------------------------------------------------


@pytest.mark.asyncio
async def test_connector_zero_is_stored_on_the_station_and_never_touches_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connector 0 is the whole charger: station columns, no EVSE/connector row."""
    calls = _patch_service(monkeypatch)

    await _charge_point().on_status_notification(
        connector_id=0,
        error_code="PowerMeterFailure",
        status="Faulted",
        timestamp="2026-09-24T10:00:00Z",
        vendor_error_code="23",
    )

    assert calls["resolve"] == []
    assert calls["connector"] == []
    assert calls["charger"] == [
        {
            "ocpp_identity": "LSC",
            "status": ChargingConnectorStatus.FAULTED,
            "status_updated_at": NOW,
            "error_code": "PowerMeterFailure",
            "vendor_error_code": "23",
        }
    ]


# --- handler: failures --------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_status_is_rejected_before_any_database_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown status label fails loudly and opens no transaction."""
    calls = _patch_service(monkeypatch)
    factory = _CountingFactory()

    with pytest.raises(ValueError):
        await _charge_point(factory).on_status_notification(
            connector_id=1, error_code="NoError", status="Exploding"
        )

    assert factory.entered == 0
    assert calls["connector"] == calls["charger"] == []


@pytest.mark.asyncio
async def test_timestamp_without_timezone_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strict timestamp rule (decision D11) applies to 1.6J too."""
    calls = _patch_service(monkeypatch)

    with pytest.raises(ValueError, match="timezone"):
        await _charge_point().on_status_notification(
            connector_id=1,
            error_code="NoError",
            status="Available",
            timestamp="2026-09-24T10:00:00",
        )

    assert calls["connector"] == []


@pytest.mark.asyncio
async def test_unprovisioned_gun_propagates_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A status for a gun that was never provisioned is an error, not auto-created."""

    async def missing(db: object, *args: Any) -> Any:
        raise ChargingConnectorNotFoundError("no such connector")

    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp16_topology", missing)

    with pytest.raises(ChargingConnectorNotFoundError):
        await _charge_point().on_status_notification(
            connector_id=5, error_code="NoError", status="Available"
        )


# --- 2.0.1 regression -----------------------------------------------------------


@pytest.mark.asyncio
async def test_2_0_1_status_notification_still_stores_occupied_without_error_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2.0.1 handler is unchanged: only status and time, no 1.6J error fields."""
    captured: list[dict[str, Any]] = []

    async def fake_resolve(db: object, *args: Any) -> tuple[Any, Any, Any]:
        return STATION_ID, EVSE_ID, CONNECTOR_ID

    async def fake_update(db: object, **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp_topology", fake_resolve)
    monkeypatch.setattr(ocpp_state_service, "update_connector_status", fake_update)
    charge_point = OCPP201ChargePoint(
        "LSC",
        object(),  # type: ignore[arg-type]
        _CountingFactory(),  # type: ignore[arg-type]
    )

    await charge_point.on_status_notification(
        timestamp="2026-09-24T10:00:00Z",
        connector_status="Occupied",
        evse_id=1,
        connector_id=1,
    )

    assert captured == [
        {
            "connector_id": CONNECTOR_ID,
            "status": ChargingConnectorStatus.OCCUPIED,
            "status_updated_at": NOW,
        }
    ]


# --- service ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("connector", [3, 1])
async def test_resolve_ocpp16_topology_maps_gun_n_to_evse_n_connector_1(
    monkeypatch: pytest.MonkeyPatch, connector: int
) -> None:
    """Gun n is EVSE n holding connector 1 (decision D3)."""
    seen: dict[str, Any] = {}

    async def fake_resolve(
        db: object, ocpp_identity: str, ocpp_evse_id: int, ocpp_connector_id: int
    ) -> tuple[Any, Any, Any]:
        seen.update(
            ocpp_identity=ocpp_identity,
            ocpp_evse_id=ocpp_evse_id,
            ocpp_connector_id=ocpp_connector_id,
        )
        return STATION_ID, EVSE_ID, CONNECTOR_ID

    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp_topology", fake_resolve)

    result = await ocpp_state_service.resolve_ocpp16_topology(
        object(),  # type: ignore[arg-type]
        "LSC",
        connector,
    )

    assert result == (STATION_ID, EVSE_ID, CONNECTOR_ID)
    assert seen == {
        "ocpp_identity": "LSC",
        "ocpp_evse_id": connector,
        "ocpp_connector_id": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("connector", [0, -1])
async def test_resolve_ocpp16_topology_rejects_connector_zero_and_negatives(
    connector: int,
) -> None:
    """Connector 0 has no topology row; callers must use the charger-level path."""
    with pytest.raises(ChargingOcppMessageInputError):
        await ocpp_state_service.resolve_ocpp16_topology(
            object(),  # type: ignore[arg-type]
            "LSC",
            connector,
        )


@pytest.mark.asyncio
async def test_update_charger_status_writes_utc_time_and_reports_missing_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The station row is updated in UTC; an unknown station and a naive time fail."""
    written: list[dict[str, Any]] = []
    station: Any = SimpleNamespace(station_id=STATION_ID)

    async def fake_get(db: object, identity: str, **_: Any) -> Any:
        return station

    async def fake_update(db: object, station_id: Any, **kwargs: Any) -> bool:
        written.append({"station_id": station_id, **kwargs})
        return True

    monkeypatch.setattr(
        charging_stations_repository, "get_station_by_identity", fake_get
    )
    monkeypatch.setattr(
        ocpp_state_repository, "update_station_charger_status", fake_update
    )
    local = datetime(2026, 9, 24, 17, 0, tzinfo=timezone(timedelta(hours=7)))

    await ocpp_state_service.update_charger_status(
        object(),  # type: ignore[arg-type]
        ocpp_identity="LSC",
        status=ChargingConnectorStatus.FAULTED,
        status_updated_at=local,
        error_code="HighTemperature",
        vendor_error_code=None,
    )

    assert written[0]["status_updated_at"] == NOW
    assert written[0]["error_code"] == "HighTemperature"
    with pytest.raises(ChargingOcppMessageInputError):
        await ocpp_state_service.update_charger_status(
            object(),  # type: ignore[arg-type]
            ocpp_identity="LSC",
            status=ChargingConnectorStatus.FAULTED,
            status_updated_at=datetime(2026, 9, 24, 10, 0),
            error_code="NoError",
            vendor_error_code=None,
        )
    station = None
    with pytest.raises(ChargingStationNotFoundError):
        await ocpp_state_service.update_charger_status(
            object(),  # type: ignore[arg-type]
            ocpp_identity="LSC",
            status=ChargingConnectorStatus.FAULTED,
            status_updated_at=NOW,
            error_code="NoError",
            vendor_error_code=None,
        )


@pytest.mark.asyncio
async def test_update_connector_status_passes_the_error_fields_to_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service forwards the new detail fields; omitting them passes None."""
    captured: list[dict[str, Any]] = []

    async def fake_update(db: object, connector_id: Any, **kwargs: Any) -> Any:
        captured.append(kwargs)
        return object()

    monkeypatch.setattr(ocpp_state_repository, "update_connector_status", fake_update)
    common: dict[str, Any] = {
        "connector_id": CONNECTOR_ID,
        "status": ChargingConnectorStatus.FAULTED,
        "status_updated_at": NOW,
    }

    await ocpp_state_service.update_connector_status(
        object(),  # type: ignore[arg-type]
        error_code="ConnectorLockFailure",
        vendor_error_code="3",
        status_info="lock",
        **common,
    )
    await ocpp_state_service.update_connector_status(
        object(),  # type: ignore[arg-type]
        **common,
    )

    assert captured[0]["error_code"] == "ConnectorLockFailure"
    assert captured[0]["vendor_error_code"] == "3"
    assert captured[0]["status_info"] == "lock"
    assert captured[1]["error_code"] is None
    assert captured[1]["vendor_error_code"] is None
    assert captured[1]["status_info"] is None


# --- responses ----------------------------------------------------------------


def test_connector_response_exposes_status_and_error_fields() -> None:
    """The API shows the exact 1.6 status and the error details."""
    connector = ChargingConnectorModel(
        connector_id=CONNECTOR_ID,
        evse_id=EVSE_ID,
        ocpp_connector_id=1,
        status=ChargingConnectorStatus.SUSPENDED_EVSE,
        status_updated_at=NOW,
        error_code="NoError",
        vendor_error_code=None,
        status_info="load sharing",
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
    )

    response = ChargingConnectorResponse.model_validate(connector)

    assert response.status is ChargingConnectorStatus.SUSPENDED_EVSE
    assert (response.error_code, response.vendor_error_code, response.status_info) == (
        "NoError",
        None,
        "load sharing",
    )


def test_station_response_exposes_the_charger_level_status() -> None:
    """Connector 0 (the whole charger) is visible on the station response."""
    station = ChargingStationModel(
        station_id=STATION_ID,
        ocpp_identity="LSC",
        display_name="Station",
        location=None,
        power_rating_kw=None,
        connector_standard=None,
        operating_hours=None,
        maintenance_status=ChargingStationMaintenanceStatus.OPERATIONAL,
        charger_status=ChargingConnectorStatus.FAULTED,
        charger_status_updated_at=NOW,
        charger_error_code="PowerMeterFailure",
        charger_vendor_error_code="23",
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
    )

    response = charging_stations_service.to_charging_station_response(
        station, connector_count=2, available_connector_count=1, now=NOW
    )

    assert response.charger_status is ChargingConnectorStatus.FAULTED
    assert response.charger_status_updated_at == NOW
    assert response.charger_error_code == "PowerMeterFailure"
    assert response.charger_vendor_error_code == "23"
