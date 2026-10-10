"""Smoke tests for the gateway's command loop and the adapters' command mapping (CS-20, PR-16)."""

from datetime import timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from ocpp.exceptions import NotImplementedError as OcppNotImplementedError
from ocpp.v16 import call as call16
from ocpp.v16 import call_result as result16
from ocpp.v201 import call as call201
from ocpp.v201 import call_result as result201

import app.domains.charging_stations.ocpp.command_loop as command_loop
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
import app.domains.charging_stations.service as charging_stations_service
from app.domains.charging_stations.exceptions import ChargingStationCommandInputError
from app.domains.charging_stations.ocpp.command_types import (
    CommandResult,
    OutboundCommand,
    to_command_result,
)
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from app.domains.charging_stations.ocpp.ocpp201_charge_point import (
    OCPP201ChargePoint,
    to_report_entries,
)
from app.domains.charging_stations.types import (
    ConfigurationMutability,
    StationCommandOutcome,
    StationCommandType,
)
from tests.fakes import FakeSessionFactory

COMMAND_ID = uuid4()


def _command(command_type: StationCommandType, **overrides: Any) -> OutboundCommand:
    values: dict[str, Any] = {
        "command_id": COMMAND_ID,
        "command_type": command_type,
        "ocpp_message_id": "msg-1",
    }
    values.update(overrides)
    return OutboundCommand(**values)


def _sent_by(charge_point: Any, response: Any) -> list[dict[str, Any]]:
    """Replace ``call`` with a fake returning ``response`` and record the calls."""
    sent: list[dict[str, Any]] = []

    async def fake_call(payload: object, **kwargs: Any) -> Any:
        sent.append({"payload": payload, **kwargs})
        return response

    charge_point.call = fake_call
    return sent


def _charge_point_16() -> OCPP16ChargePoint:
    return OCPP16ChargePoint("LSC", object(), FakeSessionFactory())  # type: ignore[arg-type]


def _charge_point_201() -> OCPP201ChargePoint:
    return OCPP201ChargePoint("LSC", object(), FakeSessionFactory())  # type: ignore[arg-type]


# --- outcome mapping ------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        ("Accepted", StationCommandOutcome.ACCEPTED),
        ("Scheduled", StationCommandOutcome.ACCEPTED),
        ("RebootRequired", StationCommandOutcome.ACCEPTED),
        ("Unlocked", StationCommandOutcome.ACCEPTED),
        ("Rejected", StationCommandOutcome.REJECTED),
        ("UnlockFailed", StationCommandOutcome.REJECTED),
        ("NotSupported", StationCommandOutcome.REJECTED),
        (None, StationCommandOutcome.REJECTED),
    ],
)
def test_answer_status_maps_to_the_observed_outcome(
    status: str | None, outcome: StationCommandOutcome
) -> None:
    """The verdict is ACCEPTED or REJECTED; the charger's own word is kept as sent."""
    result = to_command_result(status)

    assert result.outcome is outcome
    assert result.response_status == status


# --- 1.6J mapping ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected_call", "response"),
    [
        (
            _command(
                StationCommandType.REMOTE_START, ocpp_evse_id=2, id_token="TOKEN-1"
            ),
            call16.RemoteStartTransaction(id_tag="TOKEN-1", connector_id=2),
            result16.RemoteStartTransaction(status="Accepted"),
        ),
        (
            _command(StationCommandType.REMOTE_STOP, ocpp_transaction_id="17"),
            call16.RemoteStopTransaction(transaction_id=17),
            result16.RemoteStopTransaction(status="Accepted"),
        ),
        (
            _command(StationCommandType.UNLOCK_CONNECTOR, ocpp_evse_id=1),
            call16.UnlockConnector(connector_id=1),
            result16.UnlockConnector(status="Unlocked"),
        ),
        (
            _command(StationCommandType.RESET, parameters={"reset_type": "Hard"}),
            call16.Reset(type="Hard"),
            result16.Reset(status="Accepted"),
        ),
        (
            _command(
                StationCommandType.CHANGE_AVAILABILITY,
                ocpp_evse_id=2,
                parameters={"availability": "Operative"},
            ),
            call16.ChangeAvailability(connector_id=2, type="Operative"),
            result16.ChangeAvailability(status="Scheduled"),
        ),
        (
            _command(
                StationCommandType.CHANGE_CONFIGURATION,
                parameters={"key": "HeartbeatInterval", "value": "30"},
            ),
            call16.ChangeConfiguration(key="HeartbeatInterval", value="30"),
            result16.ChangeConfiguration(status="RebootRequired"),
        ),
        (
            _command(
                StationCommandType.TRIGGER_MESSAGE,
                parameters={"requested_message": "StatusNotification"},
            ),
            call16.TriggerMessage(
                requested_message="StatusNotification", connector_id=None
            ),
            result16.TriggerMessage(status="Accepted"),
        ),
    ],
)
async def test_1_6_adapter_sends_each_command_type_as_its_ocpp_call(
    command: OutboundCommand, expected_call: Any, response: Any
) -> None:
    """Gun n is the connectorId; the frame carries the row's message ID (CS-18)."""
    charge_point = _charge_point_16()
    sent = _sent_by(charge_point, response)

    result = await charge_point.send_command(command)

    assert sent[0]["payload"] == expected_call
    assert sent[0]["unique_id"] == "msg-1" and sent[0]["suppress"] is False
    assert result.outcome is StationCommandOutcome.ACCEPTED
    assert result.response_status == response.status


@pytest.mark.asyncio
async def test_1_6_remote_start_without_a_token_is_a_data_error() -> None:
    """A start with nothing to send is refused before any frame goes out."""
    charge_point = _charge_point_16()
    sent = _sent_by(charge_point, None)

    with pytest.raises(ValueError, match="token"):
        await charge_point.send_command(_command(StationCommandType.REMOTE_START))

    assert sent == []


# --- 2.0.1 mapping --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected_call", "response"),
    [
        (
            _command(
                StationCommandType.REMOTE_START,
                ocpp_evse_id=1,
                id_token="TOKEN-1",
                parameters={"remote_start_id": 1042},
            ),
            call201.RequestStartTransaction(
                id_token={"id_token": "TOKEN-1", "type": "Central"},
                remote_start_id=1042,
                evse_id=1,
            ),
            result201.RequestStartTransaction(status="Accepted"),
        ),
        (
            _command(StationCommandType.REMOTE_STOP, ocpp_transaction_id="TX-9"),
            call201.RequestStopTransaction(transaction_id="TX-9"),
            result201.RequestStopTransaction(status="Accepted"),
        ),
        (
            _command(
                StationCommandType.UNLOCK_CONNECTOR,
                ocpp_evse_id=1,
                ocpp_connector_id=1,
            ),
            call201.UnlockConnector(evse_id=1, connector_id=1),
            result201.UnlockConnector(status="Unlocked"),
        ),
        (
            _command(StationCommandType.RESET, parameters={"reset_type": "Hard"}),
            call201.Reset(type="Immediate"),
            result201.Reset(status="Accepted"),
        ),
        (
            _command(
                StationCommandType.CHANGE_AVAILABILITY,
                ocpp_evse_id=2,
                parameters={"availability": "Inoperative"},
            ),
            call201.ChangeAvailability(
                operational_status="Inoperative", evse={"id": 2}
            ),
            result201.ChangeAvailability(status="Accepted"),
        ),
        (
            _command(
                StationCommandType.TRIGGER_MESSAGE,
                parameters={"requested_message": "Heartbeat"},
            ),
            call201.TriggerMessage(requested_message="Heartbeat"),
            result201.TriggerMessage(status="Accepted"),
        ),
    ],
)
async def test_2_0_1_adapter_sends_each_command_type_as_its_ocpp_call(
    command: OutboundCommand, expected_call: Any, response: Any
) -> None:
    """The same command types become the 2.0.1 messages (CO-15)."""
    charge_point = _charge_point_201()
    sent = _sent_by(charge_point, response)

    result = await charge_point.send_command(command)

    assert sent[0]["payload"] == expected_call
    assert sent[0]["unique_id"] == "msg-1"
    assert result.outcome is StationCommandOutcome.ACCEPTED


@pytest.mark.asyncio
async def test_2_0_1_change_configuration_reads_the_one_set_variable_result() -> None:
    """One setting per command, so one SetVariables result decides the verdict (CS-20)."""
    charge_point = _charge_point_201()
    sent = _sent_by(
        charge_point,
        result201.SetVariables(
            set_variable_result=[
                {
                    "attribute_status": "Rejected",
                    "component": {"name": "OCPPCommCtrlr"},
                    "variable": {"name": "HeartbeatInterval"},
                }
            ]
        ),
    )

    result = await charge_point.send_command(
        _command(
            StationCommandType.CHANGE_CONFIGURATION,
            parameters={"key": "HeartbeatInterval", "value": "30"},
        )
    )

    data = sent[0]["payload"].set_variable_data[0]
    assert data["component"] == {"name": "OCPPCommCtrlr"}
    assert (data["variable"], data["attribute_value"]) == (
        {"name": "HeartbeatInterval"},
        "30",
    )
    assert result.outcome is StationCommandOutcome.REJECTED
    assert result.response_status == "Rejected"


def test_notify_report_parts_become_entries_with_their_component() -> None:
    """A reported variable keeps its component, EVSE and mutability (CS-19)."""
    entries = to_report_entries(
        [
            {
                "component": {
                    "name": "Connector",
                    "evse": {"id": 1, "connector_id": 2},
                },
                "variable": {"name": "Power"},
                "variable_attribute": [
                    {"type": "Actual", "value": "120", "mutability": "ReadOnly"},
                    {"type": "MaxSet", "value": "240", "mutability": "ReadWrite"},
                ],
            },
            {
                "component": {"name": "SecurityCtrlr"},
                "variable": {"name": "BasicAuthPassword"},
                "variable_attribute": [{"value": "secret", "mutability": "WriteOnly"}],
            },
            {"variable": {"name": "no component"}},
        ]
    )

    assert [
        (
            e.component_name,
            e.ocpp_evse_id,
            e.ocpp_connector_id,
            e.attribute_type,
            e.value,
        )
        for e in entries
    ] == [
        ("Connector", 1, 2, "Actual", "120"),
        ("Connector", 1, 2, "MaxSet", "240"),
        ("SecurityCtrlr", None, None, "Actual", None),
    ]
    assert entries[0].mutability is ConfigurationMutability.READ_ONLY
    assert entries[2].mutability is ConfigurationMutability.WRITE_ONLY


# --- the loop's task boundary ---------------------------------------------------


class _Sender:
    """Stand-in adapter returning a result or raising a failure."""

    protocol_version = "ocpp1.6"

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome

    async def send_command(self, command: OutboundCommand) -> CommandResult:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome  # type: ignore[no-any-return]


def _patch_loop_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[dict[str, Any]]]:
    recorded: dict[str, list[dict[str, Any]]] = {"answer": [], "fail": []}
    row = SimpleNamespace(
        command_id=COMMAND_ID,
        station_id=uuid4(),
        evse_id=None,
        session_id=None,
        command_type="RESET",
        parameters={"reset_type": "Soft"},
        ocpp_message_id="msg-1",
    )

    async def fake_get(db: object, command_id: Any) -> Any:
        return row

    async def fake_answer(db: object, command_id: Any, **kwargs: Any) -> None:
        recorded["answer"].append({"command_id": command_id, **kwargs})

    async def fake_fail(db: object, **kwargs: Any) -> None:
        recorded["fail"].append(kwargs)

    monkeypatch.setattr(ocpp_state_repository, "get_station_command_by_id", fake_get)
    monkeypatch.setattr(ocpp_state_repository, "set_command_answer", fake_answer)
    monkeypatch.setattr(ocpp_state_service, "fail_command", fake_fail)
    return recorded


@pytest.mark.asyncio
async def test_run_command_writes_the_charger_verdict_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An answered command gets its outcome, status and time."""
    recorded = _patch_loop_repository(monkeypatch)

    await command_loop.run_command(
        FakeSessionFactory(),  # type: ignore[arg-type]
        _Sender(to_command_result("Accepted")),
        COMMAND_ID,
    )

    assert recorded["fail"] == []
    answer = recorded["answer"][0]
    assert (answer["outcome"], answer["response_status"]) == (
        StationCommandOutcome.ACCEPTED,
        "Accepted",
    )
    assert answer["answered_at"].utcoffset() == timezone.utc.utcoffset(None)


@pytest.mark.asyncio
async def test_run_command_leaves_an_answer_the_adapter_already_wrote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET_CONFIGURATION completes its own row together with the snapshot."""
    recorded = _patch_loop_repository(monkeypatch)
    handled = CommandResult(
        outcome=StationCommandOutcome.ACCEPTED,
        response_status="Accepted",
        handled=True,
    )

    await command_loop.run_command(
        FakeSessionFactory(),  # type: ignore[arg-type]
        _Sender(handled),
        COMMAND_ID,
    )

    assert recorded == {"answer": [], "fail": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "outcome", "status"),
    [
        (TimeoutError("no answer"), StationCommandOutcome.TIMEOUT, None),
        (OcppNotImplementedError(), StationCommandOutcome.ERROR, "NotImplemented"),
        (RuntimeError("boom"), StationCommandOutcome.ERROR, "InternalError"),
    ],
)
async def test_run_command_turns_failures_into_outcomes_on_the_row(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
    outcome: StationCommandOutcome,
    status: str | None,
) -> None:
    """No answer is a TIMEOUT, an OCPP error is an ERROR, nothing escapes the task."""
    recorded = _patch_loop_repository(monkeypatch)

    await command_loop.run_command(
        FakeSessionFactory(),  # type: ignore[arg-type]
        _Sender(failure),
        COMMAND_ID,
    )

    assert recorded["answer"] == []
    assert recorded["fail"][0]["outcome"] is outcome
    assert recorded["fail"][0]["response_status"] == status
    assert recorded["fail"][0]["command_id"] == COMMAND_ID


def test_registry_keeps_the_newest_connection_of_a_charger() -> None:
    """A reconnect replaces the old adapter; the old connection's close is ignored."""
    registry = command_loop.StationConnectionRegistry()
    station_id = uuid4()
    old, new = _Sender(None), _Sender(None)

    registry.register(station_id, old)
    registry.register(station_id, new)
    registry.unregister(station_id, old)

    assert registry.get(station_id) is new
    assert registry.connected_station_ids() == [station_id]
    registry.unregister(station_id, new)
    assert registry.get(station_id) is None


# --- queueing validation --------------------------------------------------------


@pytest.mark.parametrize(
    ("command_type", "kwargs"),
    [
        (StationCommandType.REMOTE_START, {}),
        (StationCommandType.REMOTE_STOP, {}),
        (StationCommandType.UNLOCK_CONNECTOR, {}),
        (StationCommandType.CHANGE_CONFIGURATION, {"parameters": {"key": "A"}}),
        (StationCommandType.TRIGGER_MESSAGE, {}),
    ],
)
def test_a_command_missing_what_its_type_needs_is_refused(
    command_type: StationCommandType, kwargs: dict[str, Any]
) -> None:
    """Required links and parameters are checked when the command is queued."""
    with pytest.raises(ChargingStationCommandInputError):
        charging_stations_service._validate_command_parameters(
            command_type,
            evse_id=None,
            session_id=None,
            parameters=kwargs.get("parameters"),
        )


def test_defaults_are_filled_and_a_remote_start_gets_its_remote_start_id() -> None:
    """RESET defaults to Soft; a remote start keeps the 2.0.1 remoteStartId (CS-20)."""
    reset = charging_stations_service._validate_command_parameters(
        StationCommandType.RESET, evse_id=None, session_id=None, parameters=None
    )
    start = charging_stations_service._validate_command_parameters(
        StationCommandType.REMOTE_START,
        evse_id=None,
        session_id=uuid4(),
        parameters=None,
    )

    assert reset == {"reset_type": "Soft"}
    assert start is not None and isinstance(start["remote_start_id"], int)
