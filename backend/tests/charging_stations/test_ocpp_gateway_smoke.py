"""Smoke tests for the OCPP gateway's protocol negotiation and adapter selection."""

from types import SimpleNamespace
from typing import Any

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request
from websockets.typing import Subprotocol

import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from app.domains.charging_stations.ocpp.ocpp201_charge_point import OCPP201ChargePoint
from app.domains.charging_stations.ocpp.ocpp_server import (
    SUPPORTED_SUBPROTOCOLS,
    OCPPServer,
    create_charge_point,
    select_ocpp_subprotocol,
)
from tests.fakes import FakeSessionFactory


def _request(path: str, subprotocols: str | None) -> Request:
    """Build a handshake request as a charger would send it."""
    headers = Headers()
    if subprotocols is not None:
        headers["Sec-WebSocket-Protocol"] = subprotocols
    return Request(path, headers)


def _server(monkeypatch: pytest.MonkeyPatch, *, station_exists: bool) -> OCPPServer:
    """Build a gateway whose station lookup answers without a database."""

    async def fake_get_station(db: object, identity: str, **_: Any) -> Any:
        return SimpleNamespace(station_id="station") if station_exists else None

    monkeypatch.setattr(
        charging_stations_repository, "get_station_by_identity", fake_get_station
    )
    return OCPPServer(session_factory=FakeSessionFactory())  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offered", ["ocpp1.6", "ocpp2.0.1", "ocpp1.6, ocpp2.0.1", "ocpp2.0.1,ocpp1.6"]
)
async def test_handshake_accepts_either_supported_subprotocol(
    monkeypatch: pytest.MonkeyPatch, offered: str
) -> None:
    """A provisioned station offering 1.6J and/or 2.0.1 is accepted, not 426."""
    server = _server(monkeypatch, station_exists=True)

    response = await server._process_request(None, _request("/ocpp/LSC", offered))  # type: ignore[arg-type]

    assert response is None


@pytest.mark.asyncio
@pytest.mark.parametrize("offered", [None, "", "ocpp1.5", "mqtt, ocpp1.2"])
async def test_handshake_rejects_when_no_supported_subprotocol_is_offered(
    monkeypatch: pytest.MonkeyPatch, offered: str | None
) -> None:
    """With none of the supported protocols offered the answer is 426 naming both."""
    server = _server(monkeypatch, station_exists=True)

    response = await server._process_request(None, _request("/ocpp/LSC", offered))  # type: ignore[arg-type]

    assert response is not None
    assert response.status_code == 426
    body = response.body.decode()
    assert "ocpp2.0.1" in body
    assert "ocpp1.6" in body


@pytest.mark.asyncio
async def test_handshake_rejects_bad_path_and_unknown_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path and identity checks still apply to a 1.6J client."""
    known = _server(monkeypatch, station_exists=True)
    bad_path = await known._process_request(
        None,  # type: ignore[arg-type]
        _request("/steve/websocket/CentralSystemService/LSC", "ocpp1.6"),
    )
    unknown = _server(monkeypatch, station_exists=False)
    unknown_station = await unknown._process_request(
        None,  # type: ignore[arg-type]
        _request("/ocpp/UNKNOWN", "ocpp1.6"),
    )

    assert bad_path is not None and bad_path.status_code == 404
    assert unknown_station is not None and unknown_station.status_code == 404


def test_select_subprotocol_prefers_2_0_1_when_both_are_offered() -> None:
    """The server's preference order decides, not the client's order."""
    chosen = select_ocpp_subprotocol(
        None,  # type: ignore[arg-type]
        [Subprotocol("ocpp1.6"), Subprotocol("ocpp2.0.1")],
    )

    assert chosen == "ocpp2.0.1"


def test_select_subprotocol_returns_1_6_when_only_1_6_is_offered() -> None:
    """A 1.6J-only charger is given the ocpp1.6 subprotocol."""
    assert (
        select_ocpp_subprotocol(None, [Subprotocol("ocpp1.6")])  # type: ignore[arg-type]
        == "ocpp1.6"
    )


def test_select_subprotocol_returns_none_for_unsupported_protocols() -> None:
    """Nothing supported means no subprotocol is selected."""
    assert (
        select_ocpp_subprotocol(None, [Subprotocol("ocpp1.5")])  # type: ignore[arg-type]
        is None
    )


def test_supported_subprotocols_lists_both_protocols_in_preference_order() -> None:
    """The negotiated set is exactly 2.0.1 then 1.6J."""
    assert SUPPORTED_SUBPROTOCOLS == ("ocpp2.0.1", "ocpp1.6")


@pytest.mark.parametrize(
    ("subprotocol", "expected_class", "expected_version"),
    [
        ("ocpp1.6", OCPP16ChargePoint, "1.6"),
        ("ocpp2.0.1", OCPP201ChargePoint, "2.0.1"),
        (None, OCPP201ChargePoint, "2.0.1"),
    ],
)
def test_create_charge_point_picks_the_adapter_for_the_negotiated_protocol(
    subprotocol: str | None, expected_class: type, expected_version: str
) -> None:
    """Each negotiated protocol gets its own adapter; the default stays 2.0.1."""
    charge_point = create_charge_point(
        subprotocol,
        "LSC",
        object(),  # type: ignore[arg-type]
        FakeSessionFactory(),  # type: ignore[arg-type]
    )

    assert type(charge_point) is expected_class
    assert charge_point._ocpp_version == expected_version
    assert charge_point.id == "LSC"


def test_1_6_adapter_handles_only_the_messages_implemented_so_far() -> None:
    """Later planner steps add the other 1.6J messages; only these exist today."""
    charge_point = OCPP16ChargePoint(
        "LSC",
        object(),  # type: ignore[arg-type]
        FakeSessionFactory(),  # type: ignore[arg-type]
    )

    handled = {
        action
        for action, handlers in charge_point.route_map.items()
        if "_on_action" in handlers
    }
    assert handled == {
        "BootNotification",
        "Heartbeat",
        "StatusNotification",
        "Authorize",
        "StartTransaction",
        "StopTransaction",
        "MeterValues",
    }
