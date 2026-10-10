"""Review smoke tests of the OCPP gateway, its adapters and the command loop.

Written by the 2026-10-10 security and correctness review of the charging
network. Two kinds of tests live here:

* Guard tests that pass today and pin behaviour the review verified (handshake
  refusals, a failing handler answered with CALLERROR on a live connection,
  connector ``0`` and negative connector numbers).
* One test per review finding, asserting the **correct** behaviour. Each is
  marked ``xfail(strict=True)`` while the defect exists, so the fix turns it
  into an unexpected pass that forces the marker to be removed. The finding ID
  (``CS-n`` from the review, ``RV-BL4`` from the billing review) is in the
  marker's reason.

Every test runs without a database or a socket: the adapters get a scripted
fake connection or are called handler by handler, and the services they call
are replaced with recording fakes.
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from ocpp.v16.enums import AuthorizationStatus
from websockets.datastructures import Headers
from websockets.http11 import Request

import app.domains.charging_sessions.repository as charging_session_repository
import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp.command_loop as command_loop
import app.domains.charging_stations.ocpp.ocpp_server as ocpp_server
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_sessions.exceptions import ChargingSessionTokenError
from app.domains.charging_sessions.types import SessionStatus
from app.domains.charging_stations.exceptions import ChargingEvseNotFoundError
from app.domains.charging_stations.ocpp.command_types import (
    CommandResult,
    OutboundCommand,
    to_command_result,
)
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from app.domains.charging_stations.ocpp.ocpp16_measurements import (
    extract_v16_measurements,
)
from app.domains.charging_stations.ocpp.ocpp201_charge_point import (
    OCPP201ChargePoint,
    extract_measurements,
)
from app.domains.charging_stations.ocpp.ocpp_server import OCPPServer
from app.domains.charging_stations.types import StationCommandOutcome
from tests.builders import build_charging_session
from tests.fakes import FakeSessionFactory

STATION_ID, EVSE_ID, CONNECTOR_ID, SESSION_ID = uuid4(), uuid4(), uuid4(), uuid4()
COMMAND_ID = uuid4()
# The QR token a scan issued; IS-07 says it must never reach a log line.
SECRET_TOKEN = "SECRET-QR-TK"


class _EndOfScript(Exception):
    """Raised by the scripted connection once every frame was delivered."""


class _ScriptedConnection:
    """Fake WebSocket that replays inbound frames and records outbound ones.

    Attributes:
        sent: Every frame the adapter sent, in order.
        _frames: Inbound frames still to deliver.
    """

    def __init__(self, frames: list[str]) -> None:
        """Queue the inbound frames.

        Args:
            frames: OCPP-J frames as the charger would send them.
        """
        self._frames = list(frames)
        self.sent: list[str] = []

    async def recv(self) -> str:
        """Deliver the next frame, or end the adapter's receive loop.

        Returns:
            The next inbound frame.

        Raises:
            _EndOfScript: When every frame was delivered.
        """
        if not self._frames:
            raise _EndOfScript
        return self._frames.pop(0)

    async def send(self, message: str) -> None:
        """Record a frame the adapter sent.

        Args:
            message: The outbound frame.
        """
        self.sent.append(message)


def _frame(message_id: str, action: str, payload: dict[str, Any]) -> str:
    """Build an OCPP-J CALL frame.

    Args:
        message_id: The frame's message ID.
        action: The OCPP action.
        payload: The request payload (camelCase keys).

    Returns:
        The frame as JSON text.
    """
    return json.dumps([2, message_id, action, payload])


def _answers(connection: _ScriptedConnection) -> list[list[Any]]:
    """Decode every frame the adapter sent.

    Args:
        connection: The scripted connection.

    Returns:
        The decoded frames, in order.
    """
    return [json.loads(frame) for frame in connection.sent]


def _recorder() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    """Build a call log and a factory of async fakes that append to it.

    Returns:
        ``(calls, make)``: ``make(name, result)`` returns an async function
        recording ``(name, kwargs)`` and returning ``result``.
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    def make(name: str, result: Any = None) -> Any:
        async def fake(*_args: Any, **kwargs: Any) -> Any:
            calls.append((name, kwargs))
            return result

        return fake

    return calls, make


# --- guards: the handshake ------------------------------------------------------


def _handshake_request(path: str, subprotocols: str | None) -> Request:
    """Build a WebSocket handshake request.

    Args:
        path: The request target.
        subprotocols: The ``Sec-WebSocket-Protocol`` header, if any.

    Returns:
        The request.
    """
    headers = Headers()
    if subprotocols is not None:
        headers["Sec-WebSocket-Protocol"] = subprotocols
    return Request(path, headers)


def _gateway(
    monkeypatch: pytest.MonkeyPatch, station: Any, lookups: list[dict[str, Any]]
) -> OCPPServer:
    """Build a gateway whose station lookup answers ``station`` and records calls.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        station: What the identity lookup returns (``None`` for unknown).
        lookups: Receives the identity and keyword arguments of each lookup.

    Returns:
        The gateway, with a fake session factory.
    """

    async def fake_get_station(db: object, identity: str, **kwargs: Any) -> Any:
        lookups.append({"identity": identity, **kwargs})
        return station

    monkeypatch.setattr(
        charging_stations_repository, "get_station_by_identity", fake_get_station
    )
    return OCPPServer(session_factory=FakeSessionFactory())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_handshake_refuses_unknown_or_soft_deleted_identity_with_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An identity with no live row is refused, and deleted rows are never looked at."""
    lookups: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, None, lookups)

    response = await gateway._process_request(
        None,  # type: ignore[arg-type]
        _handshake_request("/ocpp/GONE-01", "ocpp1.6"),
    )

    assert response is not None and response.status_code == 404
    assert lookups == [{"identity": "GONE-01", "include_deleted": False}]


@pytest.mark.asyncio
async def test_handshake_refuses_an_inactive_charger_with_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STN-02: a registered charger that is out of service cannot connect."""
    station = SimpleNamespace(station_id=STATION_ID, status="INACTIVE")
    gateway = _gateway(monkeypatch, station, [])

    response = await gateway._process_request(
        None,  # type: ignore[arg-type]
        _handshake_request("/ocpp/LSC", "ocpp2.0.1"),
    )

    assert response is not None and response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("offered", [None, "ocpp1.5", "wamp, mqtt"])
async def test_handshake_refuses_a_wrong_subprotocol_with_426_before_any_lookup(
    monkeypatch: pytest.MonkeyPatch, offered: str | None
) -> None:
    """Without ocpp1.6 or ocpp2.0.1 the answer is 426 and no station is queried."""
    lookups: list[dict[str, Any]] = []
    station = SimpleNamespace(station_id=STATION_ID, status="ACTIVE")
    gateway = _gateway(monkeypatch, station, lookups)

    response = await gateway._process_request(
        None,  # type: ignore[arg-type]
        _handshake_request("/ocpp/LSC", offered),
    )

    assert response is not None and response.status_code == 426
    assert lookups == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/ocpp/", "/ocpp/a/b", "/other/LSC", "/ocpp"])
async def test_handshake_refuses_a_malformed_path_with_404_before_any_lookup(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """A path that is not ``/ocpp/<identity>`` is refused without a query."""
    lookups: list[dict[str, Any]] = []
    gateway = _gateway(monkeypatch, None, lookups)

    response = await gateway._process_request(
        None,  # type: ignore[arg-type]
        _handshake_request(path, "ocpp1.6"),
    )

    assert response is not None and response.status_code == 404
    assert lookups == []


# --- guards: handler failures and connector numbers -----------------------------


def _charge_point_16(connection: object | None = None) -> OCPP16ChargePoint:
    """Build a 1.6J adapter on a fake connection and session factory.

    Args:
        connection: The connection to read from, a placeholder by default.

    Returns:
        The adapter.
    """
    return OCPP16ChargePoint(
        "LSC",
        connection or object(),  # type: ignore[arg-type]
        FakeSessionFactory(),  # type: ignore[arg-type]
    )


def _charge_point_201(connection: object | None = None) -> OCPP201ChargePoint:
    """Build a 2.0.1 adapter on a fake connection and session factory.

    Args:
        connection: The connection to read from, a placeholder by default.

    Returns:
        The adapter.
    """
    return OCPP201ChargePoint(
        "LSC",
        connection or object(),  # type: ignore[arg-type]
        FakeSessionFactory(),  # type: ignore[arg-type]
    )


def _status_frame(message_id: str, connector_id: int) -> str:
    """Build a 1.6J StatusNotification frame.

    Args:
        message_id: The frame's message ID.
        connector_id: The connector number reported.

    Returns:
        The frame as JSON text.
    """
    return _frame(
        message_id,
        "StatusNotification",
        {"connectorId": connector_id, "errorCode": "NoError", "status": "Available"},
    )


@pytest.mark.asyncio
async def test_a_failing_handler_is_answered_callerror_and_the_connection_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown gun fails its handler; the next frame is still answered normally."""

    async def unknown_evse(*_args: Any, **_kwargs: Any) -> Any:
        raise ChargingEvseNotFoundError("OCPP EVSE '7' not found in station")

    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp_topology", unknown_evse)
    connection = _ScriptedConnection(
        [_status_frame("m1", 7), _frame("m2", "Heartbeat", {})]
    )

    with pytest.raises(_EndOfScript):
        await _charge_point_16(connection).start()

    first, second = _answers(connection)
    assert first[:2] == [4, "m1"]
    assert second[:2] == [3, "m2"] and "currentTime" in second[2]


@pytest.mark.asyncio
async def test_status_for_connector_zero_updates_only_the_whole_charger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connector 0 goes to the charger's state; no gun is resolved or written."""
    calls, make = _recorder()
    monkeypatch.setattr(
        ocpp_state_service, "update_charger_status", make("charger_status")
    )
    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp_topology", make("topology"))
    monkeypatch.setattr(
        ocpp_state_service, "update_connector_status", make("connector_status")
    )
    connection = _ScriptedConnection([_status_frame("m1", 0)])

    with pytest.raises(_EndOfScript):
        await _charge_point_16(connection).start()

    assert [name for name, _ in calls] == ["charger_status"]
    assert _answers(connection) == [[3, "m1", {}]]


@pytest.mark.asyncio
async def test_status_for_a_negative_connector_is_refused_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative connectorId is answered CALLERROR before any lookup or write."""
    calls, make = _recorder()
    monkeypatch.setattr(
        ocpp_state_service, "update_charger_status", make("charger_status")
    )
    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp_topology", make("topology"))
    monkeypatch.setattr(
        ocpp_state_service, "update_connector_status", make("connector_status")
    )
    connection = _ScriptedConnection([_status_frame("m1", -1)])

    with pytest.raises(_EndOfScript):
        await _charge_point_16(connection).start()

    assert calls == []
    assert _answers(connection)[0][:2] == [4, "m1"]


# --- RV-CS1: raw frames in application logs --------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["ocpp1.6", "ocpp2.0.1"])
async def test_gateway_never_writes_raw_frames_or_id_tags_to_the_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    protocol: str,
) -> None:
    """IS-07: an Authorize carrying a QR token leaves no trace of it in the logs."""
    _, make = _recorder()
    monkeypatch.setattr(
        ocpp_state_service, "resolve_station_id_by_identity", make("station", uuid4())
    )
    monkeypatch.setattr(
        charging_sessions_service, "is_start_token_valid", make("valid", True)
    )
    if protocol == "ocpp1.6":
        connection = _ScriptedConnection(
            [_frame("m1", "Authorize", {"idTag": SECRET_TOKEN})]
        )
        charge_point: OCPP16ChargePoint | OCPP201ChargePoint = _charge_point_16(
            connection
        )
    else:
        connection = _ScriptedConnection(
            [
                _frame(
                    "m1",
                    "Authorize",
                    {"idToken": {"idToken": SECRET_TOKEN, "type": "Central"}},
                )
            ]
        )
        charge_point = _charge_point_201(connection)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(_EndOfScript):
        await charge_point.start()

    assert _answers(connection)[0][:2] == [3, "m1"]
    assert SECRET_TOKEN not in caplog.text


# --- RV-CS2: optional evse / connectorId in 2.0.1 TransactionEvent ----------------


def _patch_transaction_services(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any]]]:
    """Replace the services a 2.0.1 TransactionEvent calls with recording fakes.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The call log: ``(name, kwargs)`` per call.
    """
    calls, make = _recorder()
    activated = SimpleNamespace(session_id=SESSION_ID, status=SessionStatus.ACTIVE)
    reference = SimpleNamespace(
        session_id=SESSION_ID,
        station_id=STATION_ID,
        evse_id=EVSE_ID,
        connector_id=CONNECTOR_ID,
        status=SessionStatus.ACTIVE,
    )
    monkeypatch.setattr(
        ocpp_state_service,
        "resolve_ocpp_topology",
        make("topology", (STATION_ID, EVSE_ID, CONNECTOR_ID)),
    )
    monkeypatch.setattr(
        ocpp_state_service,
        "resolve_station_id_by_identity",
        make("station", STATION_ID),
    )
    monkeypatch.setattr(
        charging_sessions_service,
        "activate_pending_session",
        make("activate", activated),
    )
    monkeypatch.setattr(
        charging_sessions_service,
        "resolve_session_by_transaction",
        make("reference", reference),
    )
    monkeypatch.setattr(
        charging_sessions_service, "ingest_meter_values", make("energy")
    )
    monkeypatch.setattr(
        charging_sessions_service, "ingest_measurements", make("measurements")
    )
    monkeypatch.setattr(charging_sessions_service, "complete_session", make("complete"))
    return calls


def _energy_group(*sampled_values: dict[str, Any]) -> list[dict[str, Any]]:
    """Build one snake_cased meterValue group at a fixed time.

    Args:
        *sampled_values: The group's sampled values.

    Returns:
        A one-element meterValue list.
    """
    return [
        {"timestamp": "2026-10-01T10:00:00Z", "sampled_value": list(sampled_values)}
    ]


@pytest.mark.asyncio
async def test_2_0_1_ended_event_without_evse_completes_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``evse`` is optional after the first event; Ended still closes the session."""
    calls = _patch_transaction_services(monkeypatch)

    await _charge_point_201().on_transaction_event(
        event_type="Ended",
        timestamp="2026-10-01T10:00:00Z",
        trigger_reason="EVDeparted",
        seq_no=5,
        transaction_info={"transaction_id": "TX-1", "stopped_reason": "EVDisconnected"},
        meter_value=_energy_group({"value": 52000, "context": "Transaction.End"}),
    )

    completed = [kwargs for name, kwargs in calls if name == "complete"]
    assert len(completed) == 1
    assert completed[0]["meter_stop_wh"] == Decimal(52000)


@pytest.mark.asyncio
async def test_2_0_1_started_event_with_evse_id_only_activates_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``EVSEType.connectorId`` is optional; a one-plug EVSE still starts."""
    calls = _patch_transaction_services(monkeypatch)

    response = await _charge_point_201().on_transaction_event(
        event_type="Started",
        timestamp="2026-10-01T10:00:00Z",
        trigger_reason="RemoteStart",
        seq_no=0,
        transaction_info={"transaction_id": "TX-1"},
        evse={"id": 1},
        id_token={"id_token": SECRET_TOKEN, "type": "Central"},
        meter_value=_energy_group({"value": 50000, "context": "Transaction.Begin"}),
    )

    assert [name for name, _ in calls if name == "activate"] == ["activate"]
    assert response.id_token_info is None or (
        response.id_token_info["status"] == "Accepted"
    )


# --- RV-CS4 / RV-BL5: phase and location of the energy register ---------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-CS4/RV-BL5: meter_stop taken from a per-phase or Inlet register",
)
async def test_2_0_1_meter_stop_ignores_per_phase_and_inlet_registers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closing reading is the outlet total, not the last sample in the payload."""
    calls = _patch_transaction_services(monkeypatch)

    await _charge_point_201().on_transaction_event(
        event_type="Ended",
        timestamp="2026-10-01T10:00:00Z",
        trigger_reason="EVDeparted",
        seq_no=5,
        transaction_info={"transaction_id": "TX-1"},
        evse={"id": 1, "connector_id": 1},
        meter_value=_energy_group(
            {"value": 50000, "context": "Transaction.End"},
            {"value": 17000, "phase": "L3", "context": "Transaction.End"},
            {"value": 50400, "location": "Inlet", "context": "Transaction.End"},
        ),
    )

    completed = [kwargs for name, kwargs in calls if name == "complete"]
    assert completed[0]["meter_stop_wh"] == Decimal(50000)


@pytest.mark.xfail(
    strict=True,
    reason="RV-CS4/RV-BL5: a per-phase 1.6J energy register loses its phase",
)
def test_1_6_per_phase_energy_register_is_not_stored_as_the_total() -> None:
    """An L1 register reading either keeps its phase or stays out of the totals."""
    extraction = extract_v16_measurements(
        _energy_group(
            {"value": "90000", "measurand": "Energy.Active.Import.Register"},
            {
                "value": "30000",
                "measurand": "Energy.Active.Import.Register",
                "phase": "L1",
            },
        )
    )

    per_phase = [sample for sample in extraction.energy if sample.value_wh == 30000]
    assert all(getattr(sample, "phase", None) == "L1" for sample in per_phase)
    assert [sample.value_wh for sample in extraction.energy].count(Decimal(90000)) == 1


# --- RV-CS5: values beyond the measurement column ---------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="RV-CS5: a value beyond Numeric(24,6) is not skipped (overflow)",
)
def test_1_6_out_of_range_vendor_value_is_skipped_and_counted() -> None:
    """A uint64 'not available' sentinel is skipped, never sent to the database."""
    extraction = extract_v16_measurements(
        _energy_group(
            {"value": "18446744073709551615", "measurand": "Vendor.Counter"},
            {"value": "42", "measurand": "SoC", "unit": "Percent"},
        )
    )

    assert extraction.skipped_count == 1
    assert [sample.measurand for sample in extraction.measurements] == ["SoC"]
    assert all(
        abs(sample.value) < Decimal(10) ** 18 for sample in extraction.measurements
    )


@pytest.mark.xfail(
    strict=True,
    reason="RV-CS5: a huge 2.0.1 multiplier raises decimal.InvalidOperation",
)
def test_2_0_1_huge_multiplier_does_not_fail_the_message() -> None:
    """An absurd multiplier drops that sample; the other readings still come through."""
    measurements = extract_measurements(
        _energy_group(
            {
                "value": 5,
                "measurand": "Power.Active.Import",
                "unit_of_measure": {"unit": "W", "multiplier": 1_000_000_000},
            },
            {"value": 42, "measurand": "SoC"},
        )
    )

    assert [sample.measurand for sample in measurements] == ["SoC"]


# --- RV-BL4: signed energy samples in StopTransaction -----------------------------


@pytest.mark.asyncio
async def test_1_6_stop_with_signed_energy_samples_completes_from_meter_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signed sample is skipped; the session closes with ``meterStop``."""
    calls = _patch_transaction_services(monkeypatch)
    signed = {
        "value": "AP;0;3;ALCV3D...;BIHEIWSHAAA",
        "format": "SignedData",
        "context": "Transaction.End",
        "measurand": "Energy.Active.Import.Register",
    }

    response = await _charge_point_16().on_stop_transaction(
        meter_stop=152000,
        timestamp="2026-10-01T10:00:00Z",
        transaction_id=42,
        reason="EVDisconnected",
        transaction_data=_energy_group(signed),
    )

    completed = [kwargs for name, kwargs in calls if name == "complete"]
    assert response is not None
    assert completed[0]["meter_stop_wh"] == Decimal(152000)
    assert [name for name, _ in calls if name == "energy"] == []


# --- RV-CS6 / RV-CS10 / RV-CS8: the command loop ---------------------------------------


class _RecordingSender:
    """Stand-in adapter that records every command it is asked to send.

    Attributes:
        sent: The outbound commands, in order.
    """

    protocol_version = "ocpp1.6"

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.sent: list[OutboundCommand] = []

    async def send_command(self, command: OutboundCommand) -> CommandResult:
        """Record the command and answer Accepted.

        Args:
            command: The command to send.

        Returns:
            An accepted result.
        """
        self.sent.append(command)
        return to_command_result("Accepted")


def _patch_command_row(
    monkeypatch: pytest.MonkeyPatch, row: Any
) -> list[tuple[str, dict[str, Any]]]:
    """Serve ``row`` as the command and record the writes to it.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        row: The command row the repository returns.

    Returns:
        The call log of answers and failures.
    """
    calls, make = _recorder()
    monkeypatch.setattr(
        ocpp_state_repository, "get_station_command_by_id", make("get", row)
    )
    monkeypatch.setattr(ocpp_state_repository, "set_command_answer", make("answer"))
    monkeypatch.setattr(ocpp_state_service, "fail_command", make("fail"))
    return calls


def _command_row(**overrides: Any) -> SimpleNamespace:
    """Build a claimed command row.

    Args:
        **overrides: Columns to change.

    Returns:
        The row.
    """
    values: dict[str, Any] = {
        "command_id": COMMAND_ID,
        "station_id": STATION_ID,
        "evse_id": None,
        "session_id": None,
        "command_type": "RESET",
        "parameters": {"reset_type": "Soft"},
        "ocpp_message_id": "msg-1",
        "outcome": StationCommandOutcome.PENDING.value,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-CS6: a command the sweep already closed is still sent",
)
async def test_run_command_does_not_send_a_command_that_was_already_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row swept to TIMEOUT while queued on the call lock is neither sent nor rewritten."""
    calls = _patch_command_row(
        monkeypatch, _command_row(outcome=StationCommandOutcome.TIMEOUT.value)
    )
    sender = _RecordingSender()

    await command_loop.run_command(
        FakeSessionFactory(),  # type: ignore[arg-type]
        sender,
        COMMAND_ID,
    )

    assert sender.sent == []
    assert [name for name, _ in calls if name == "answer"] == []


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-CS10: a command for a deleted EVSE widens to the whole charger",
)
async def test_command_for_a_deleted_evse_is_not_sent_to_the_whole_charger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Inoperative meant for one gun never reaches the charger as connector 0."""
    calls = _patch_command_row(
        monkeypatch,
        _command_row(
            evse_id=EVSE_ID,
            command_type="CHANGE_AVAILABILITY",
            parameters={"availability": "Inoperative"},
        ),
    )
    _, make = _recorder()
    monkeypatch.setattr(
        charging_stations_repository, "get_evse_by_id", make("evse", None)
    )
    sender = _RecordingSender()

    await command_loop.run_command(
        FakeSessionFactory(),  # type: ignore[arg-type]
        sender,
        COMMAND_ID,
    )

    assert sender.sent == []
    assert [kwargs["outcome"] for name, kwargs in calls if name == "fail"] == [
        StationCommandOutcome.ERROR
    ]


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-CS8: a failed start abandons a session of another charger",
)
async def test_failed_start_never_abandons_a_session_of_another_charger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A REMOTE_START naming another charger's PENDING session leaves that session alone."""
    foreign_session = build_charging_session(status=SessionStatus.PENDING)
    assert foreign_session.station_id != STATION_ID
    calls = _patch_command_row(
        monkeypatch,
        _command_row(
            command_type="REMOTE_START",
            session_id=foreign_session.session_id,
            parameters={"remote_start_id": 7},
        ),
    )
    _, make = _recorder()
    monkeypatch.setattr(
        charging_session_repository,
        "get_session_by_id",
        make("session", foreign_session),
    )
    monkeypatch.setattr(
        charging_sessions_service,
        "abandon_pending_session",
        _recorder_into(calls, "abandon", True),
    )

    await command_loop.abandon_session_of_failed_start(
        object(),  # type: ignore[arg-type]
        COMMAND_ID,
        StationCommandOutcome.REJECTED,
    )

    assert [name for name, _ in calls if name == "abandon"] == []


def _recorder_into(
    calls: list[tuple[str, dict[str, Any]]], name: str, result: Any
) -> Any:
    """Build an async fake that appends to an existing call log.

    Args:
        calls: The log to append to.
        name: The name recorded for each call.
        result: What the fake returns.

    Returns:
        The async fake.
    """

    async def fake(*args: Any, **kwargs: Any) -> Any:
        calls.append((name, {"args": args, **kwargs}))
        return result

    return fake


# --- RV-CS9: the gateway re-resolves the charger by its identity string -----------


class _StubChargePoint:
    """Adapter stand-in whose receive loop ends at once.

    Attributes:
        id: The identity it was created with.
    """

    def __init__(self, identity: str) -> None:
        """Remember the identity.

        Args:
            identity: The OCPP identity.
        """
        self.id = identity

    async def start(self) -> None:
        """End the receive loop immediately."""


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-CS9: adapters resolve the charger by identity, not station_id",
)
async def test_gateway_hands_the_station_fixed_at_handshake_to_the_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rename after the handshake cannot redirect the open connection's messages."""
    station = SimpleNamespace(station_id=STATION_ID, status="ACTIVE")
    gateway = _gateway(monkeypatch, station, [])
    created: dict[str, Any] = {}

    def fake_create(*args: Any, **kwargs: Any) -> _StubChargePoint:
        created["args"], created["kwargs"] = args, kwargs
        created["charge_point"] = _StubChargePoint(str(args[1]))
        return created["charge_point"]  # type: ignore[no-any-return]

    async def fake_close(**_: Any) -> None:
        return None

    monkeypatch.setattr(ocpp_server, "create_charge_point", fake_create)
    connection = SimpleNamespace(
        request=SimpleNamespace(path="/ocpp/LSC"),
        subprotocol="ocpp1.6",
        close=fake_close,
    )

    await gateway._handle_connection(connection)  # type: ignore[arg-type]

    handed_over: list[object] = [
        *created["args"],
        *created["kwargs"].values(),
        getattr(created["charge_point"], "station_id", None),
    ]
    assert STATION_ID in handed_over


# --- RV-CS3 smoke counterpart: 1.6J start answers ---------------------------------


@pytest.mark.asyncio
async def test_1_6_start_with_a_token_no_scan_issued_is_invalid_with_transaction_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: an unknown tag gets Invalid and transactionId 0 (the RV-CS3 retry path).

    The retried-start defect itself (RV-CS3) needs the real session table and is
    tested on PostgreSQL in ``tests/test_postgres_charging_review.py``.
    """

    async def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise ChargingSessionTokenError("no scan issued this token")

    _, make = _recorder()
    monkeypatch.setattr(
        ocpp_state_service,
        "resolve_ocpp16_topology",
        make("topology", (STATION_ID, EVSE_ID, CONNECTOR_ID)),
    )
    monkeypatch.setattr(
        charging_sessions_service, "has_active_session_on_connector", make("a", False)
    )
    monkeypatch.setattr(
        charging_sessions_service, "allocate_ocpp16_transaction_id", make("tx", 9)
    )
    monkeypatch.setattr(charging_sessions_service, "activate_pending_session", refuse)
    monkeypatch.setattr(
        charging_sessions_service,
        "find_started_session_by_token",
        make("retry", None),
    )

    response = await _charge_point_16().on_start_transaction(
        connector_id=1,
        id_tag="UNKNOWN-TAG",
        meter_start=1000,
        timestamp=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).isoformat(),
    )

    assert response.transaction_id == 0
    assert response.id_tag_info["status"] == AuthorizationStatus.invalid
