"""Smoke tests for OCPP 1.6J Authorize/StartTransaction/StopTransaction and the session lookups."""

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
    ChargingSessionNotFoundError,
    ChargingSessionTokenError,
)
from app.domains.charging_sessions.models import ChargingSessionModel
from app.domains.charging_sessions.types import SessionStatus
from app.domains.charging_stations.exceptions import ChargingOcppMessageInputError
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from tests.builders import fake_db_session

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
    monkeypatch: pytest.MonkeyPatch,
    *,
    active_session: bool = False,
    token_issued: bool = True,
) -> dict[str, Any]:
    """Replace the service calls StartTransaction makes and record them."""
    ids = count(1)
    record: dict[str, Any] = {"ingest": [], "allocated": []}

    async def fake_resolve(
        db: object, ocpp_identity: str, ocpp_connector_id: int
    ) -> tuple[Any, Any, Any]:
        record["resolve"] = {
            "ocpp_identity": ocpp_identity,
            "ocpp_connector_id": ocpp_connector_id,
        }
        return STATION_ID, EVSE_ID, CONNECTOR_ID

    async def fake_has_active(db: object, connector_id: Any) -> bool:
        return active_session

    async def fake_allocate(db: object) -> int:
        allocated = next(ids)
        record["allocated"].append(allocated)
        return allocated

    async def fake_ingest(db: object, **kwargs: Any) -> None:
        if not token_issued:
            raise ChargingSessionTokenError("no scan issued this token")
        record["ingest"].append(kwargs)

    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp16_topology", fake_resolve)
    monkeypatch.setattr(
        charging_service, "has_active_session_on_connector", fake_has_active
    )
    monkeypatch.setattr(
        charging_service, "allocate_ocpp16_transaction_id", fake_allocate
    )
    monkeypatch.setattr(charging_service, "activate_pending_session", fake_ingest)
    return record


# --- Authorize ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("is_issued", [True, False])
async def test_authorize_accepts_only_a_tag_a_scan_issued(
    monkeypatch: pytest.MonkeyPatch, is_issued: bool
) -> None:
    """CE-11: Authorize is Accepted for an issued token and Invalid otherwise."""
    asked: dict[str, Any] = {}

    async def fake_station(db: object, ocpp_identity: str) -> Any:
        return STATION_ID

    async def fake_valid(db: object, *, station_id: Any, id_token: str) -> bool:
        asked.update(station_id=station_id, id_token=id_token)
        return is_issued

    monkeypatch.setattr(
        ocpp_state_service, "resolve_station_id_by_identity", fake_station
    )
    monkeypatch.setattr(charging_service, "is_start_token_valid", fake_valid)

    response = await _charge_point().on_authorize(id_tag="TAG-1")

    assert response.id_tag_info["status"] == (
        AuthorizationStatus.accepted if is_issued else AuthorizationStatus.invalid
    )
    assert asked == {"station_id": STATION_ID, "id_token": "TAG-1"}


# --- StartTransaction ------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_transaction_activates_the_session_and_returns_the_allocated_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CSMS assigns the integer transactionId and stores it as text (CE-03, CE-11)."""
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
    assert ingested["started_at"] == NOW
    assert ingested["meter_start_wh"] == Decimal(1000)
    assert ingested["id_token"] == "TAG-1"
    assert ingested["station_id"] == STATION_ID
    assert ingested["connector_id"] == CONNECTOR_ID


@pytest.mark.asyncio
async def test_start_with_a_token_no_scan_issued_is_answered_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CE-11: any other token is answered Invalid and creates no session."""
    record = _patch_start(monkeypatch, token_issued=False)

    response = await _charge_point().on_start_transaction(
        connector_id=1,
        id_tag="NOT-ISSUED",
        meter_start=0,
        timestamp="2026-09-24T10:00:00Z",
    )

    assert response.id_tag_info["status"] == AuthorizationStatus.invalid
    assert record["ingest"] == []


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

    async def fake_station(db: object, ocpp_identity: str) -> Any:
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
    monkeypatch.setattr(charging_service, "complete_session", fake_ingest)
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
    assert stored["transaction_id"] == "42"
    assert (stored["station_id"], stored["evse_id"], stored["connector_id"]) == (
        STATION_ID,
        EVSE_ID,
        CONNECTOR_ID,
    )
    assert stored["meter_stop_wh"] == Decimal(1500)
    assert stored["ended_at"] == NOW + timedelta(minutes=30)
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
        "organization_id": uuid4(),
        "started_by": uuid4(),
        "ocpp_transaction_id": "42",
        "status": SessionStatus.ACTIVE,
        "started_at": NOW,
        "meter_start_wh": Decimal(1000),
        "id_token": "TAG-1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ChargingSessionModel(**values)


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
        fake_db_session(),
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
            fake_db_session(),
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
            fake_db_session(),
            CONNECTOR_ID,
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
