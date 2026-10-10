"""Smoke tests for the post-boot GetConfiguration capture and configuration snapshots (CS-21)."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import websockets
from ocpp.exceptions import NotImplementedError as OcppNotImplementedError
from ocpp.v16 import call, call_result
from websockets.exceptions import ConnectionClosed

import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
from app.domains.charging_stations.exceptions import (
    ChargingOcppMessageInputError,
    ChargingStationNotFoundError,
)
from app.domains.charging_stations.models import ChargingStationConfigurationEntryModel
from app.domains.charging_stations.ocpp.ocpp16_charge_point import (
    OCPP16ChargePoint,
    _to_configuration_entries,
)
from app.domains.charging_stations.types import (
    ConfigurationEntry,
    StationCommandOutcome,
)
from tests.builders import fake_db_session
from tests.principals import build_internal_principal

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
STATION_ID = uuid4()
COMMAND_ID = uuid4()


class _CountingFactory:
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
        station_id=STATION_ID,
    )


def _snapshot_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[dict[str, Any]]]:
    """Replace the three service calls of a capture and record their arguments."""
    recorded: dict[str, list[dict[str, Any]]] = {
        "start": [],
        "complete": [],
        "fail": [],
    }

    async def fake_start(db: object, **kwargs: Any) -> Any:
        recorded["start"].append(kwargs)
        return COMMAND_ID

    async def fake_complete(db: object, **kwargs: Any) -> None:
        recorded["complete"].append(kwargs)

    async def fake_fail(db: object, **kwargs: Any) -> None:
        recorded["fail"].append(kwargs)

    monkeypatch.setattr(
        ocpp_state_service, "start_boot_configuration_command", fake_start
    )
    monkeypatch.setattr(
        ocpp_state_service, "complete_configuration_capture", fake_complete
    )
    monkeypatch.setattr(ocpp_state_service, "fail_command", fake_fail)
    return recorded


# --- the hook and the task ---------------------------------------------------------


@pytest.mark.asyncio
async def test_after_boot_hook_schedules_a_task_and_never_awaits_the_call_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hook returns at once; the request goes out only when the loop next runs."""
    charge_point = _charge_point()
    sent: list[object] = []

    async def fake_call(request: object, **kwargs: Any) -> Any:
        sent.append(request)
        return call_result.GetConfiguration(configuration_key=[])

    charge_point.call = fake_call
    _snapshot_recorder(monkeypatch)

    result = charge_point.after_boot_notification()

    assert result is None  # not awaitable: the library must not wrap it
    assert sent == []  # nothing was awaited inline
    assert charge_point._configuration_task is not None
    await asyncio.sleep(0)  # let the task start
    await charge_point.cancel_background_tasks()
    assert len(sent) == 1
    assert isinstance(sent[0], call.GetConfiguration)
    assert sent[0].key is None  # no key -> the charger returns every key


@pytest.mark.asyncio
async def test_capture_stores_every_reported_key_with_its_readonly_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answer becomes one entry per key; the call raises on CALLERROR (suppress=False)."""
    charge_point = _charge_point()
    seen: dict[str, Any] = {}

    async def fake_call(request: object, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return call_result.GetConfiguration(
            configuration_key=[
                {
                    "key": "SupportedFeatureProfiles",
                    "readonly": True,
                    "value": "Core,SmartCharging",
                },
                {"key": "MeterValueSampleInterval", "readonly": False, "value": "60"},
                {"key": "NoValueKey", "readonly": False},
            ],
            unknown_key=["Nope"],
        )

    charge_point.call = fake_call
    recorded = _snapshot_recorder(monkeypatch)

    await charge_point._capture_configuration()

    assert seen["suppress"] is False
    # The frame carries the message ID stored on the command (CS-18, CS-21).
    assert seen["unique_id"] == recorded["start"][0]["ocpp_message_id"]
    assert recorded["start"][0]["station_id"] == STATION_ID
    complete = recorded["complete"][0]
    assert complete["command_id"] == COMMAND_ID
    assert complete["captured_at"].utcoffset() == timedelta(0)
    assert complete["entries"] == [
        ConfigurationEntry("SupportedFeatureProfiles", "Core,SmartCharging", True),
        ConfigurationEntry("MeterValueSampleInterval", "60", False),
        ConfigurationEntry("NoValueKey", None, False),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [OcppNotImplementedError(), TimeoutError("no answer")],
)
async def test_a_refusal_or_timeout_is_logged_and_fails_the_command(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
) -> None:
    """A charger without GetConfiguration must not break the connection or the log."""
    factory = _CountingFactory()
    charge_point = _charge_point(factory)

    async def fake_call(request: object, **kwargs: Any) -> Any:
        raise failure

    charge_point.call = fake_call
    recorded = _snapshot_recorder(monkeypatch)

    with caplog.at_level(logging.WARNING):
        await charge_point._capture_configuration()

    assert any(
        "did not answer GetConfiguration" in r.getMessage() for r in caplog.records
    )
    assert recorded["complete"] == []
    # One transaction for the command, one for its failure.
    assert factory.entered == 2
    failure_outcome = (
        StationCommandOutcome.TIMEOUT
        if isinstance(failure, TimeoutError)
        else StationCommandOutcome.ERROR
    )
    assert recorded["fail"][0]["outcome"] is failure_outcome
    assert recorded["fail"][0]["command_id"] == COMMAND_ID


@pytest.mark.asyncio
async def test_an_unexpected_failure_is_logged_at_the_task_boundary_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing awaits the task, so a database failure is logged there and swallowed."""
    charge_point = _charge_point()

    async def fake_call(request: object, **kwargs: Any) -> Any:
        return call_result.GetConfiguration(
            configuration_key=[{"key": "A", "readonly": False}]
        )

    async def failing_snapshot(db: object, **kwargs: Any) -> None:
        raise RuntimeError("database down")

    charge_point.call = fake_call
    _snapshot_recorder(monkeypatch)
    monkeypatch.setattr(
        ocpp_state_service, "complete_configuration_capture", failing_snapshot
    )

    with caplog.at_level(logging.ERROR):
        await charge_point._capture_configuration()  # must not raise

    assert any("Configuration capture failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_closing_the_connection_cancels_a_capture_that_is_still_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task must not outlive its connection."""
    charge_point = _charge_point()
    started = asyncio.Event()

    async def slow_call(request: object, **kwargs: Any) -> Any:
        started.set()
        await asyncio.sleep(3600)

    charge_point.call = slow_call
    _snapshot_recorder(monkeypatch)

    charge_point.after_boot_notification()
    await started.wait()
    task = charge_point._configuration_task
    await charge_point.cancel_background_tasks()

    assert task is not None and task.cancelled()


@pytest.mark.asyncio
async def test_a_second_boot_cancels_the_capture_of_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the newest boot's capture keeps running."""
    charge_point = _charge_point()
    started = asyncio.Event()

    async def slow_call(request: object, **kwargs: Any) -> Any:
        started.set()
        await asyncio.sleep(3600)

    charge_point.call = slow_call
    _snapshot_recorder(monkeypatch)

    charge_point.after_boot_notification()
    await started.wait()
    first = charge_point._configuration_task
    charge_point.after_boot_notification()
    await asyncio.sleep(0)

    assert first is not None and first.cancelled()
    assert charge_point._configuration_task is not first
    await charge_point.cancel_background_tasks()


def test_configuration_entries_skip_keyless_items_and_default_the_readonly_flag() -> (
    None
):
    """Odd chargers happen: a missing key is skipped, a missing flag is False."""
    entries = _to_configuration_entries(
        [
            {"readonly": True, "value": "orphan"},  # no key
            {"key": "", "value": "empty"},  # empty key
            {"key": "Numeric", "value": 5},  # non-text value
            {"key": "Plain"},
        ]
    )

    assert entries == [
        ConfigurationEntry("Numeric", "5", False),
        ConfigurationEntry("Plain", None, False),
    ]
    assert _to_configuration_entries(None) == []


# --- a real WebSocket, both directions ------------------------------------------------


@pytest.mark.asyncio
async def test_boot_reply_comes_first_then_the_request_and_nothing_deadlocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over a real socket: BootNotification.conf, then GetConfiguration, then the snapshot.

    This is the property the design exists for. python-ocpp's receive loop is
    sequential, so awaiting the request inside the boot handler would block the
    loop that must read the answer; the request must also go out only after the
    boot response.
    """
    recorded = _snapshot_recorder(monkeypatch)

    async def fake_boot(db: object, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(ocpp_state_service, "record_charger_boot", fake_boot)

    async def handler(connection: Any) -> None:
        charge_point = OCPP16ChargePoint(
            "LSC",
            connection,
            _CountingFactory(),  # type: ignore[arg-type]
            station_id=STATION_ID,
        )
        try:
            await charge_point.start()
        except ConnectionClosed:
            pass
        finally:
            await charge_point.cancel_background_tasks()

    async with websockets.serve(
        handler,
        "localhost",
        0,
        subprotocols=["ocpp1.6"],  # type: ignore[list-item]
    ) as server:
        port = server.sockets[0].getsockname()[1]
        async with websockets.connect(
            f"ws://localhost:{port}",
            subprotocols=["ocpp1.6"],  # type: ignore[list-item]
        ) as client:
            await client.send(
                json.dumps(
                    [
                        2,
                        "boot-1",
                        "BootNotification",
                        {"chargePointVendor": "V", "chargePointModel": "M"},
                    ]
                )
            )
            first = json.loads(await asyncio.wait_for(client.recv(), 5))
            second = json.loads(await asyncio.wait_for(client.recv(), 5))
            await client.send(
                json.dumps(
                    [
                        3,
                        second[1],
                        {
                            "configurationKey": [
                                {
                                    "key": "SupportedFeatureProfiles",
                                    "readonly": True,
                                    "value": "Core",
                                }
                            ]
                        },
                    ]
                )
            )
            for _ in range(50):  # wait up to ~2.5 s for the snapshot
                if recorded["complete"]:
                    break
                await asyncio.sleep(0.05)

    assert (first[0], first[1]) == (3, "boot-1")  # the boot answer comes first
    assert first[2]["status"] == "Accepted"
    assert (second[0], second[2]) == (2, "GetConfiguration")  # then the request
    assert recorded["complete"][0]["entries"] == [
        ConfigurationEntry("SupportedFeatureProfiles", "Core", True)
    ]


# --- service ------------------------------------------------------------------------


def _patch_capture_repository(
    monkeypatch: pytest.MonkeyPatch, *, capture: Any
) -> dict[str, list[dict[str, Any]]]:
    """Replace the repository calls of a completed capture and record them."""
    written: dict[str, list[dict[str, Any]]] = {
        "entries": [],
        "outcome": [],
        "answer": [],
    }

    async def fake_capture(db: object, command_id: Any) -> Any:
        return capture

    async def fake_insert(db: object, **kwargs: Any) -> None:
        written["entries"].append(kwargs)

    async def fake_outcome(db: object, capture_id: Any, **kwargs: Any) -> None:
        written["outcome"].append({"capture_id": capture_id, **kwargs})

    async def fake_answer(db: object, command_id: Any, **kwargs: Any) -> bool:
        written["answer"].append({"command_id": command_id, **kwargs})
        return True

    monkeypatch.setattr(
        ocpp_state_repository, "get_configuration_capture_by_command_id", fake_capture
    )
    monkeypatch.setattr(
        ocpp_state_repository, "insert_configuration_entry", fake_insert
    )
    monkeypatch.setattr(
        ocpp_state_repository, "set_configuration_capture_outcome", fake_outcome
    )
    monkeypatch.setattr(ocpp_state_repository, "set_command_answer", fake_answer)
    return written


@pytest.mark.asyncio
async def test_complete_configuration_capture_stores_entries_and_closes_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every key becomes an Actual entry of the snapshot; readonly maps to mutability."""
    capture_id = uuid4()
    written = _patch_capture_repository(
        monkeypatch, capture=SimpleNamespace(capture_id=capture_id)
    )
    local = datetime(2026, 9, 24, 17, 0, tzinfo=timezone(timedelta(hours=7)))

    result = await ocpp_state_service.complete_configuration_capture(
        fake_db_session(),
        command_id=COMMAND_ID,
        entries=[
            ConfigurationEntry("A", "1", True),
            ConfigurationEntry("B", None, False),
        ],
        captured_at=local,
    )

    assert result == capture_id
    assert [
        (r["capture_id"], r["variable_name"], r["value"], r["mutability"])
        for r in written["entries"]
    ] == [
        (capture_id, "A", "1", "READ_ONLY"),
        (capture_id, "B", None, "READ_WRITE"),
    ]
    assert written["outcome"][0]["captured_at"] == NOW
    assert written["outcome"][0]["outcome"].value == "COMPLETE"
    assert written["answer"][0]["outcome"] is StationCommandOutcome.ACCEPTED
    assert written["answer"][0]["answered_at"] == NOW


@pytest.mark.asyncio
async def test_complete_configuration_capture_without_a_snapshot_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command with no snapshot row (not a GET_CONFIGURATION) is left alone."""
    written = _patch_capture_repository(monkeypatch, capture=None)

    result = await ocpp_state_service.complete_configuration_capture(
        fake_db_session(),
        command_id=COMMAND_ID,
        entries=[ConfigurationEntry("A", "1", False)],
        captured_at=NOW,
    )

    assert result is None
    assert written == {"entries": [], "outcome": [], "answer": []}


@pytest.mark.asyncio
async def test_complete_configuration_capture_rejects_a_naive_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strict timestamp rule applies to the capture time."""
    _patch_capture_repository(monkeypatch, capture=None)

    with pytest.raises(ChargingOcppMessageInputError):
        await ocpp_state_service.complete_configuration_capture(
            fake_db_session(),
            command_id=COMMAND_ID,
            entries=[],
            captured_at=datetime(2026, 9, 24, 10, 0),
        )


@pytest.mark.asyncio
async def test_fail_command_fails_a_pending_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command with no usable answer closes its pending snapshot as FAILED."""
    capture_id = uuid4()
    written = _patch_capture_repository(
        monkeypatch, capture=SimpleNamespace(capture_id=capture_id, outcome="PENDING")
    )

    await ocpp_state_service.fail_command(
        fake_db_session(),
        command_id=COMMAND_ID,
        outcome=StationCommandOutcome.TIMEOUT,
        response_status=None,
        answered_at=NOW,
    )

    assert written["answer"][0]["outcome"] is StationCommandOutcome.TIMEOUT
    assert written["outcome"][0]["outcome"].value == "FAILED"
    assert written["outcome"][0]["captured_at"] is None


def _entry(
    key: str, value: str | None, mutability: str, capture_id: Any
) -> ChargingStationConfigurationEntryModel:
    return ChargingStationConfigurationEntryModel(
        entry_id=uuid4(),
        capture_id=capture_id,
        variable_name=key,
        attribute_type="Actual",
        value=value,
        mutability=mutability,
    )


@pytest.mark.asyncio
async def test_latest_configuration_returns_the_newest_capture_or_an_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API shows the latest capture, and an empty one before the first boot."""
    capture_id = uuid4()
    state: dict[str, Any] = {"latest": None}

    async def fake_station(db: object, station_id: Any, **_: Any) -> Any:
        return SimpleNamespace(station_id=STATION_ID, location_id=uuid4())

    async def fake_location(db: object, location_id: Any, **_: Any) -> Any:
        return SimpleNamespace(organization_id=uuid4(), is_public=True)

    async def fake_latest(db: object, station_id: Any) -> Any:
        return state["latest"]

    async def fake_entries(db: object, cap: Any) -> Any:
        assert cap == capture_id
        return [
            _entry("HeartbeatInterval", "60", "READ_WRITE", capture_id),
            _entry("SupportedFeatureProfiles", "Core", "READ_ONLY", capture_id),
        ]

    monkeypatch.setattr(charging_stations_repository, "get_station_by_id", fake_station)
    monkeypatch.setattr(
        charging_stations_repository, "get_location_by_id", fake_location
    )
    monkeypatch.setattr(
        ocpp_state_repository, "get_latest_configuration_capture", fake_latest
    )
    monkeypatch.setattr(
        ocpp_state_repository,
        "list_configuration_entries_by_capture_id",
        fake_entries,
    )

    empty = await charging_stations_service.get_latest_station_configuration(
        fake_db_session(),
        STATION_ID,
        principal=build_internal_principal(),
    )
    state["latest"] = (capture_id, NOW)
    latest = await charging_stations_service.get_latest_station_configuration(
        fake_db_session(),
        STATION_ID,
        principal=build_internal_principal(),
    )

    assert (empty.capture_id, empty.captured_at, empty.items) == (None, None, [])
    assert (latest.capture_id, latest.captured_at) == (capture_id, NOW)
    assert [(i.variable_name, i.value, i.mutability) for i in latest.items] == [
        ("HeartbeatInterval", "60", "READ_WRITE"),
        ("SupportedFeatureProfiles", "Core", "READ_ONLY"),
    ]


@pytest.mark.asyncio
async def test_latest_configuration_of_an_unknown_station_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint's 404 comes from this domain error."""

    async def fake_station(db: object, station_id: Any, **_: Any) -> None:
        return None

    monkeypatch.setattr(charging_stations_repository, "get_station_by_id", fake_station)

    with pytest.raises(ChargingStationNotFoundError):
        await charging_stations_service.get_latest_station_configuration(
            fake_db_session(),
            uuid4(),
            principal=build_internal_principal(),
        )
