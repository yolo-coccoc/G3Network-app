"""Smoke tests for the verbatim OCPP message log (wrapper and service)."""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
from app.domains.charging_stations.exceptions import ChargingOcppMessageInputError
from app.domains.charging_stations.ocpp.raw_log import RecordingConnection
from app.domains.charging_stations.types import OcppMessageDirection
from tests.fakes import FakeSessionFactory

STATION_ID = uuid4()


class _FakeConnection:
    """Stand-in for a websockets connection that logs the order of its calls."""

    def __init__(
        self, events: list[str], frames: list[Any], *, fail_send: bool = False
    ) -> None:
        self._events = events
        self._frames = list(frames)
        self._fail_send = fail_send

    async def recv(self) -> Any:
        if not self._frames:
            raise ConnectionClosedOK(None, None)
        self._events.append("recv")
        return self._frames.pop(0)

    async def send(self, message: Any) -> None:
        if self._fail_send:
            raise ConnectionClosedError(None, None)
        self._events.append("send")


def _recording_connection(
    events: list[str],
    frames: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_send: bool = False,
    fail_record: bool = False,
) -> tuple[RecordingConnection, FakeSessionFactory, list[dict[str, Any]]]:
    """Build a wrapper around fakes and capture every recorded frame."""
    recorded: list[dict[str, Any]] = []

    async def fake_record(db: object, **kwargs: Any) -> None:
        if fail_record:
            raise RuntimeError("database unavailable")
        events.append(f"recorded:{kwargs['direction'].value}")
        recorded.append({"db": db, **kwargs})

    monkeypatch.setattr(charging_stations_service, "record_ocpp_message", fake_record)
    factory = FakeSessionFactory()
    connection = RecordingConnection(
        _FakeConnection(events, frames, fail_send=fail_send),  # type: ignore[arg-type]
        station_id=STATION_ID,
        ocpp_subprotocol="ocpp1.6",
        session_factory=factory,  # type: ignore[arg-type]
    )
    return connection, factory, recorded


@pytest.mark.asyncio
async def test_recording_connection_records_inbound_frame_before_returning_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The frame is stored before python-ocpp ever sees it."""
    events: list[str] = []
    connection, _, recorded = _recording_connection(
        events, ['[2,"1","Heartbeat",{}]'], monkeypatch
    )

    frame = await connection.recv()
    events.append("returned")

    assert frame == '[2,"1","Heartbeat",{}]'
    assert events == ["recv", "recorded:CP_TO_CSMS", "returned"]
    assert recorded[0]["raw_frame"] == '[2,"1","Heartbeat",{}]'
    assert recorded[0]["station_id"] == STATION_ID
    assert recorded[0]["ocpp_subprotocol"] == "ocpp1.6"


@pytest.mark.asyncio
async def test_recording_connection_records_outbound_frame_after_sending_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outbound frame is only recorded once it was handed to the socket."""
    events: list[str] = []
    connection, _, recorded = _recording_connection(events, [], monkeypatch)

    await connection.send('[3,"1",{"currentTime":"2026-09-24T00:00:00Z"}]')

    assert events == ["send", "recorded:CSMS_TO_CP"]
    assert recorded[0]["direction"] is OcppMessageDirection.CSMS_TO_CP


@pytest.mark.asyncio
async def test_recording_connection_does_not_return_frame_when_recording_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence must not silently go missing: a failed write stops processing."""
    events: list[str] = []
    connection, _, _ = _recording_connection(
        events, ['[2,"1","Heartbeat",{}]'], monkeypatch, fail_record=True
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await connection.recv()


@pytest.mark.asyncio
async def test_recording_connection_does_not_record_frame_that_failed_to_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame that never left is not claimed as sent."""
    events: list[str] = []
    connection, _, recorded = _recording_connection(
        events, [], monkeypatch, fail_send=True
    )

    with pytest.raises(ConnectionClosedError):
        await connection.send("[3]")

    assert recorded == []


@pytest.mark.asyncio
async def test_recording_connection_does_not_record_when_connection_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed connection is propagated to the library and nothing is logged."""
    events: list[str] = []
    connection, _, recorded = _recording_connection(events, [], monkeypatch)

    with pytest.raises(ConnectionClosedOK):
        await connection.recv()

    assert recorded == []


@pytest.mark.asyncio
async def test_recording_connection_uses_its_own_transaction_for_each_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each frame is committed independently of any handler transaction."""
    events: list[str] = []
    connection, factory, recorded = _recording_connection(
        events, ['[2,"1","Heartbeat",{}]'], monkeypatch
    )

    await connection.recv()
    await connection.send('[3,"1",{}]')

    assert factory.entered == 2
    assert factory.exited == 2
    assert len({id(entry["db"]) for entry in recorded}) == 2


@pytest.mark.asyncio
async def test_recording_connection_stores_binary_frame_as_text_but_returns_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A (non-OCPP-J) binary frame is stored decoded; the caller gets it unchanged."""
    events: list[str] = []
    payload = b'[2,"1","Heartbeat",{}]'
    connection, _, recorded = _recording_connection(events, [payload], monkeypatch)

    frame = await connection.recv()

    assert frame == payload
    assert recorded[0]["raw_frame"] == '[2,"1","Heartbeat",{}]'


@pytest.mark.asyncio
async def test_recording_connection_stamps_timezone_aware_receive_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """occurred_at is the receive time, timezone-aware and taken just now."""
    events: list[str] = []
    connection, _, recorded = _recording_connection(events, ["[2]"], monkeypatch)
    before = datetime.now(timezone.utc)

    await connection.recv()

    occurred_at = recorded[0]["occurred_at"]
    assert occurred_at.utcoffset() == timedelta(0)
    assert before <= occurred_at <= datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_record_ocpp_message_normalizes_time_to_utc_and_keeps_frame_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service converts to UTC and never touches the frame text."""
    captured: dict[str, Any] = {}

    async def fake_insert(db: object, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        charging_stations_repository, "insert_ocpp_message", fake_insert
    )

    async def fake_touch(db: object, station_id: UUID, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(charging_stations_repository, "touch_station_seen", fake_touch)
    frame = ' [2, "1",  "Heartbeat", {} ] \n'  # odd whitespace must survive
    local = datetime(2026, 9, 24, 8, 0, tzinfo=timezone(timedelta(hours=7)))

    await charging_stations_service.record_ocpp_message(
        object(),  # type: ignore[arg-type]
        station_id=STATION_ID,
        occurred_at=local,
        ocpp_subprotocol="ocpp2.0.1",
        direction=OcppMessageDirection.CP_TO_CSMS,
        raw_frame=frame,
    )

    assert captured["occurred_at"] == datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc)
    assert captured["occurred_at"].utcoffset() == timedelta(0)
    assert captured["raw_frame"] == frame
    assert isinstance(captured["station_id"], UUID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("occurred_at", "ocpp_subprotocol"),
    [
        (datetime(2026, 9, 24, 8, 0), "ocpp1.6"),  # naive timestamp
        (datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc), ""),  # empty subprotocol
        (datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc), "x" * 21),  # too long
    ],
)
async def test_record_ocpp_message_rejects_invalid_metadata(
    monkeypatch: pytest.MonkeyPatch,
    occurred_at: datetime,
    ocpp_subprotocol: str,
) -> None:
    """Only the metadata is validated; a bad value never reaches the repository."""
    called = False

    async def fake_insert(db: object, **kwargs: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        charging_stations_repository, "insert_ocpp_message", fake_insert
    )

    with pytest.raises(ChargingOcppMessageInputError):
        await charging_stations_service.record_ocpp_message(
            object(),  # type: ignore[arg-type]
            station_id=STATION_ID,
            occurred_at=occurred_at,
            ocpp_subprotocol=ocpp_subprotocol,
            direction=OcppMessageDirection.CSMS_TO_CP,
            raw_frame="[3]",
        )

    assert called is False


@pytest.mark.asyncio
async def test_record_ocpp_message_marks_station_seen_for_inbound_frames_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any inbound frame proves the charger is alive; an outbound frame does not."""
    touched: list[dict[str, Any]] = []

    async def fake_insert(db: object, **kwargs: Any) -> None:
        return None

    async def fake_touch(db: object, station_id: UUID, **kwargs: Any) -> None:
        touched.append({"station_id": station_id, **kwargs})

    monkeypatch.setattr(
        charging_stations_repository, "insert_ocpp_message", fake_insert
    )
    monkeypatch.setattr(charging_stations_repository, "touch_station_seen", fake_touch)
    moment = datetime(2026, 9, 24, 8, 0, tzinfo=timezone(timedelta(hours=7)))

    for direction in (
        OcppMessageDirection.CP_TO_CSMS,
        OcppMessageDirection.CSMS_TO_CP,
    ):
        await charging_stations_service.record_ocpp_message(
            object(),  # type: ignore[arg-type]
            station_id=STATION_ID,
            occurred_at=moment,
            ocpp_subprotocol="ocpp1.6",
            direction=direction,
            raw_frame="[2]",
        )

    assert len(touched) == 1
    assert touched[0]["station_id"] == STATION_ID
    assert touched[0]["ocpp_protocol_version"] == "ocpp1.6"
    assert touched[0]["seen_at"] == datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc)
