"""Smoke tests for OCPP 1.6J Authorize/StartTransaction/StopTransaction and the session fields."""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from ocpp.v16.enums import AuthorizationStatus

import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionNotFoundError,
    ChargingSessionStateError,
)
from app.domains.charging_sessions.models import ChargingSessionModel
from app.domains.charging_sessions.schemas import ChargingSessionResponse
from app.domains.charging_sessions.types import SessionEventType, SessionStatus
from app.domains.charging_stations.exceptions import ChargingOcppMessageInputError
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint

STATION_ID, EVSE_ID, CONNECTOR_ID = uuid4(), uuid4(), uuid4()
NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


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


def _patch_start(
    monkeypatch: pytest.MonkeyPatch, *, active_session: bool = False
) -> dict[str, Any]:
    """Replace the service calls StartTransaction makes and record them."""
    ids = count(1)
    record: dict[str, Any] = {"ingest": [], "allocated": []}

    async def fake_resolve(db: object, **kwargs: Any) -> tuple[Any, Any, Any]:
        record["resolve"] = kwargs
        return STATION_ID, EVSE_ID, CONNECTOR_ID

    async def fake_has_active(db: object, connector_id: Any) -> bool:
        return active_session

    async def fake_allocate(db: object) -> int:
        allocated = next(ids)
        record["allocated"].append(allocated)
        return allocated

    async def fake_ingest(db: object, **kwargs: Any) -> None:
        record["ingest"].append(kwargs)

    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp16_topology", fake_resolve)
    monkeypatch.setattr(
        charging_service, "has_active_session_on_connector", fake_has_active
    )
    monkeypatch.setattr(
        charging_service, "allocate_ocpp16_transaction_id", fake_allocate
    )
    monkeypatch.setattr(charging_service, "ingest_transaction_event", fake_ingest)
    return record


# --- Authorize ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", ["04AABBCCDD", "UNKNOWN-TAG", "0", "x" * 20])
async def test_authorize_accepts_every_id_tag(tag: str) -> None:
    """Decision D7: any tag is accepted; there is no tag registry yet."""
    response = await _charge_point().on_authorize(id_tag=tag)

    assert response.id_tag_info["status"] == AuthorizationStatus.accepted


# --- StartTransaction ------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_transaction_creates_the_session_and_returns_the_allocated_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CSMS assigns the integer transactionId and stores it as text."""
    record = _patch_start(monkeypatch)

    response = await _charge_point().on_start_transaction(
        connector_id=2,
        id_tag="TAG-1",
        meter_start=1000,
        timestamp="2026-09-24T10:00:00Z",
    )

    assert response.transaction_id == 1
    assert response.id_tag_info["status"] == AuthorizationStatus.accepted
    assert record["resolve"] == {"ocpp_identity": "LSC", "ocpp_connector_id": 2}
    ingested = record["ingest"][0]
    assert ingested["transaction_id"] == "1"
    assert ingested["event_type"] is SessionEventType.STARTED
    assert ingested["event_occurred_at"] == NOW
    assert ingested["meter_start_wh"] == Decimal(1000)
    assert ingested["id_tag"] == "TAG-1"
    assert ingested["station_id"] == STATION_ID
    assert ingested["connector_id"] == CONNECTOR_ID


@pytest.mark.asyncio
async def test_two_start_transactions_get_different_transaction_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every session gets its own number, so an ID is never reused."""
    _patch_start(monkeypatch)
    charge_point = _charge_point()

    first = await charge_point.on_start_transaction(
        connector_id=1, id_tag="A", meter_start=0, timestamp="2026-09-24T10:00:00Z"
    )
    second = await charge_point.on_start_transaction(
        connector_id=1, id_tag="A", meter_start=0, timestamp="2026-09-24T11:00:00Z"
    )

    assert (first.transaction_id, second.transaction_id) == (1, 2)


@pytest.mark.asyncio
async def test_start_on_a_connector_with_an_active_session_warns_and_proceeds(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Decision D14: the old session is left alone, but the situation is visible."""
    record = _patch_start(monkeypatch, active_session=True)

    with caplog.at_level(logging.WARNING):
        response = await _charge_point().on_start_transaction(
            connector_id=1,
            id_tag="A",
            meter_start=0,
            timestamp="2026-09-24T10:00:00Z",
        )

    warning = next(
        r for r in caplog.records if "already has an active" in r.getMessage()
    )
    assert warning.ocpp_connector_id == 1  # type: ignore[attr-defined]
    assert response.transaction_id == 1
    assert len(record["ingest"]) == 1


@pytest.mark.asyncio
async def test_start_on_connector_zero_is_rejected_before_allocating_an_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connector 0 is the whole charger; a session cannot start on it."""
    record = _patch_start(monkeypatch)
    monkeypatch.undo()  # use the real resolver, which refuses connector 0
    allocated: list[int] = []

    async def fake_allocate(db: object) -> int:
        allocated.append(1)
        return 1

    monkeypatch.setattr(
        charging_service, "allocate_ocpp16_transaction_id", fake_allocate
    )

    with pytest.raises(ChargingOcppMessageInputError):
        await _charge_point().on_start_transaction(
            connector_id=0,
            id_tag="A",
            meter_start=0,
            timestamp="2026-09-24T10:00:00Z",
        )

    assert allocated == []
    assert record["allocated"] == []


@pytest.mark.asyncio
async def test_start_with_a_timestamp_without_timezone_opens_no_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strict timestamp rule (D11) is checked before any database work."""
    record = _patch_start(monkeypatch)
    factory = _CountingFactory()

    with pytest.raises(ValueError, match="timezone"):
        await _charge_point(factory).on_start_transaction(
            connector_id=1,
            id_tag="A",
            meter_start=0,
            timestamp="2026-09-24T10:00:00",
        )

    assert factory.entered == 0
    assert record["allocated"] == []


# --- StopTransaction ---------------------------------------------------------------


def _patch_stop(
    monkeypatch: pytest.MonkeyPatch, *, missing: bool = False
) -> list[dict[str, Any]]:
    ingested: list[dict[str, Any]] = []

    async def fake_station(db: object, **kwargs: Any) -> Any:
        return STATION_ID

    async def fake_reference(db: object, **kwargs: Any) -> Any:
        if missing:
            raise ChargingSessionNotFoundError("no such transaction")
        assert kwargs["transaction_id"] == "42"
        return SimpleNamespace(
            session_id=uuid4(),
            station_id=STATION_ID,
            evse_id=EVSE_ID,
            connector_id=CONNECTOR_ID,
            status=SessionStatus.ACTIVE,
        )

    async def fake_ingest(db: object, **kwargs: Any) -> None:
        ingested.append(kwargs)

    monkeypatch.setattr(
        ocpp_state_service, "resolve_station_id_by_identity", fake_station
    )
    monkeypatch.setattr(
        charging_service, "resolve_session_by_transaction", fake_reference
    )
    monkeypatch.setattr(charging_service, "ingest_transaction_event", fake_ingest)
    return ingested


@pytest.mark.asyncio
async def test_stop_transaction_finds_the_session_and_stores_meter_stop_and_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The topology comes from the stored session; meterStop is stored as such."""
    ingested = _patch_stop(monkeypatch)

    response = await _charge_point().on_stop_transaction(
        meter_stop=1500,
        timestamp="2026-09-24T10:30:00Z",
        transaction_id=42,
        reason="EVDisconnected",
    )

    assert response is not None
    stored = ingested[0]
    assert stored["event_type"] is SessionEventType.ENDED
    assert stored["transaction_id"] == "42"
    assert (stored["station_id"], stored["evse_id"], stored["connector_id"]) == (
        STATION_ID,
        EVSE_ID,
        CONNECTOR_ID,
    )
    assert stored["meter_end_wh"] == stored["meter_stop_wh"] == Decimal(1500)
    assert stored["meter_end_sampled_at"] == stored["event_occurred_at"]
    assert stored["event_occurred_at"] == NOW + timedelta(minutes=30)
    assert stored["stop_reason"] == "EVDisconnected"


@pytest.mark.asyncio
async def test_stop_transaction_for_an_unknown_transaction_propagates_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown transaction is a loud error (a CALLERROR), not a silent ACK."""
    _patch_stop(monkeypatch, missing=True)

    with pytest.raises(ChargingSessionNotFoundError):
        await _charge_point().on_stop_transaction(
            meter_stop=1, timestamp="2026-09-24T10:30:00Z", transaction_id=42
        )


# --- session service ----------------------------------------------------------------


def _session(**overrides: Any) -> ChargingSessionModel:
    values: dict[str, Any] = {
        "session_id": uuid4(),
        "station_id": STATION_ID,
        "evse_id": EVSE_ID,
        "connector_id": CONNECTOR_ID,
        "ocpp_transaction_id": "42",
        "status": SessionStatus.ACTIVE,
        "started_at": NOW,
        "ended_at": None,
        "meter_start_wh": Decimal(1000),
        "meter_end_wh": None,
        "meter_end_sampled_at": None,
        "energy_delivered_wh": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ChargingSessionModel(**values)


def _patch_repository(
    monkeypatch: pytest.MonkeyPatch, session: ChargingSessionModel
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def create_session(db: object, **kwargs: Any) -> ChargingSessionModel:
        seen["create"] = kwargs
        return session

    async def get_by_transaction(
        db: object, station_id: Any, transaction_id: str
    ) -> Any:
        return session

    async def insert_event(db: object, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(charging_repository, "create_session", create_session)
    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )
    monkeypatch.setattr(charging_repository, "insert_event", insert_event)
    monkeypatch.setattr(charging_repository, "utc_now", lambda: NOW)
    return seen


@pytest.mark.asyncio
async def test_started_event_passes_the_id_tag_to_the_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The idTag that started the session is stored with it."""
    seen = _patch_repository(monkeypatch, _session())

    await charging_service.ingest_transaction_event(
        object(),  # type: ignore[arg-type]
        station_id=STATION_ID,
        evse_id=EVSE_ID,
        connector_id=CONNECTOR_ID,
        transaction_id="42",
        event_type=SessionEventType.STARTED,
        event_occurred_at=NOW,
        seq_no=None,
        meter_start_wh=Decimal(1000),
        id_tag="TAG-1",
    )

    assert seen["create"]["id_tag"] == "TAG-1"


@pytest.mark.asyncio
async def test_ended_event_stores_stop_reason_and_meter_stop_and_completes_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closing reading and reason land on the session; energy = stop - start."""
    session = _session()
    _patch_repository(monkeypatch, session)

    result = await charging_service.ingest_transaction_event(
        object(),  # type: ignore[arg-type]
        station_id=STATION_ID,
        evse_id=EVSE_ID,
        connector_id=CONNECTOR_ID,
        transaction_id="42",
        event_type=SessionEventType.ENDED,
        event_occurred_at=NOW,
        seq_no=None,
        meter_end_wh=Decimal(1500),
        meter_end_sampled_at=NOW,
        stop_reason="EmergencyStop",
        meter_stop_wh=Decimal(1500),
    )

    assert result.status is SessionStatus.COMPLETED
    assert session.stop_reason == "EmergencyStop"
    assert session.meter_stop_wh == Decimal(1500)
    assert session.meter_end_wh == Decimal(1500)
    assert session.energy_delivered_wh == Decimal(500)


@pytest.mark.asyncio
async def test_meter_stop_is_kept_even_when_a_stale_timestamp_discards_the_aggregate_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The charger's own closing figure is stored; the F-B2 watermark still protects meter_end."""
    session = _session(
        meter_end_wh=Decimal(1700),
        meter_end_sampled_at=NOW + timedelta(minutes=5),
    )
    _patch_repository(monkeypatch, session)

    await charging_service.ingest_transaction_event(
        object(),  # type: ignore[arg-type]
        station_id=STATION_ID,
        evse_id=EVSE_ID,
        connector_id=CONNECTOR_ID,
        transaction_id="42",
        event_type=SessionEventType.ENDED,
        event_occurred_at=NOW,
        seq_no=None,
        meter_end_wh=Decimal(1500),
        meter_end_sampled_at=NOW,  # older than the stored watermark
        meter_stop_wh=Decimal(1500),
    )

    assert session.meter_stop_wh == Decimal(1500)
    assert session.meter_end_wh == Decimal(1700)  # not overwritten


@pytest.mark.asyncio
async def test_a_second_stop_for_a_completed_session_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMPLETED stays terminal for 1.6J's StopTransaction too."""
    session = _session(status=SessionStatus.COMPLETED)
    _patch_repository(monkeypatch, session)

    with pytest.raises(ChargingSessionStateError):
        await charging_service.ingest_transaction_event(
            object(),  # type: ignore[arg-type]
            station_id=STATION_ID,
            evse_id=EVSE_ID,
            connector_id=CONNECTOR_ID,
            transaction_id="42",
            event_type=SessionEventType.ENDED,
            event_occurred_at=NOW,
            seq_no=None,
            meter_stop_wh=Decimal(1),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "kwargs"),
    [
        (SessionEventType.STARTED, {"id_tag": "x" * 21}),
        (SessionEventType.ENDED, {"stop_reason": "x" * 31}),
        (SessionEventType.ENDED, {"meter_stop_wh": Decimal(-1)}),
    ],
)
async def test_ingest_rejects_out_of_contract_new_fields(
    monkeypatch: pytest.MonkeyPatch,
    event_type: SessionEventType,
    kwargs: dict[str, Any],
) -> None:
    """An idTag over 20 characters, a stop reason over 30, or a negative meter fails."""
    _patch_repository(monkeypatch, _session())

    with pytest.raises(ChargingSessionInputError):
        await charging_service.ingest_transaction_event(
            object(),  # type: ignore[arg-type]
            station_id=STATION_ID,
            evse_id=EVSE_ID,
            connector_id=CONNECTOR_ID,
            transaction_id="42",
            event_type=event_type,
            event_occurred_at=NOW,
            seq_no=None,
            **kwargs,
        )


@pytest.mark.asyncio
async def test_ingest_without_the_new_fields_still_works_for_the_2_0_1_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2.0.1 caller passes none of the new arguments; they default to None."""
    session = _session()
    _patch_repository(monkeypatch, session)

    await charging_service.ingest_transaction_event(
        object(),  # type: ignore[arg-type]
        station_id=STATION_ID,
        evse_id=EVSE_ID,
        connector_id=CONNECTOR_ID,
        transaction_id="42",
        event_type=SessionEventType.ENDED,
        event_occurred_at=NOW,
        seq_no=3,
        meter_end_wh=Decimal(1750),
    )

    assert session.stop_reason is None
    assert session.meter_stop_wh is None


@pytest.mark.asyncio
async def test_resolve_session_by_transaction_returns_a_reference_or_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message carrying only (station, transactionId) can find its session."""
    session = _session()
    found: list[Any] = [session]

    async def get_by_transaction(
        db: object, station_id: Any, transaction_id: str
    ) -> Any:
        return found[0]

    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )

    reference = await charging_service.resolve_session_by_transaction(
        object(),  # type: ignore[arg-type]
        station_id=STATION_ID,
        transaction_id="42",
    )
    found[0] = None

    assert (reference.session_id, reference.connector_id, reference.status) == (
        session.session_id,
        CONNECTOR_ID,
        SessionStatus.ACTIVE,
    )
    with pytest.raises(ChargingSessionNotFoundError):
        await charging_service.resolve_session_by_transaction(
            object(),  # type: ignore[arg-type]
            station_id=STATION_ID,
            transaction_id="42",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count_value", "expected"), [(0, False), (1, True), (3, True)]
)
async def test_has_active_session_on_connector_reflects_the_count(
    monkeypatch: pytest.MonkeyPatch, count_value: int, expected: bool
) -> None:
    """Any open session on the connector counts."""

    async def fake_count(db: object, connector_id: Any) -> int:
        return count_value

    monkeypatch.setattr(
        charging_repository, "count_active_sessions_by_connector_id", fake_count
    )

    assert (
        await charging_service.has_active_session_on_connector(
            object(),
            CONNECTOR_ID,  # type: ignore[arg-type]
        )
        is expected
    )


@pytest.mark.asyncio
async def test_allocate_ocpp16_transaction_id_returns_the_sequence_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service simply exposes the database sequence's next value."""

    async def fake_next(db: object) -> int:
        return 7

    monkeypatch.setattr(charging_repository, "next_ocpp16_transaction_id", fake_next)

    assert await charging_service.allocate_ocpp16_transaction_id(object()) == 7  # type: ignore[arg-type]


def test_session_response_exposes_the_new_fields() -> None:
    """The monitoring API shows the idTag, stop reason and closing meter reading."""
    session = _session(
        id_tag="TAG-1",
        stop_reason="EVDisconnected",
        meter_stop_wh=Decimal(1500),
        status=SessionStatus.COMPLETED,
    )

    response = ChargingSessionResponse.model_validate(session)

    assert (response.id_tag, response.stop_reason, response.meter_stop_wh) == (
        "TAG-1",
        "EVDisconnected",
        Decimal(1500),
    )
