"""Smoke tests for OCPP 2.0.1 TransactionEvent: token-matched start, stop with the closing reading (CE-11, CE-12)."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

import app.domains.charging_sessions.service as charging_service
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
from app.domains.charging_sessions.exceptions import ChargingSessionTokenError
from app.domains.charging_sessions.types import SessionStatus
from app.domains.charging_stations.ocpp.ocpp201_charge_point import OCPP201ChargePoint

STATION_ID, EVSE_ID, CONNECTOR_ID, SESSION_ID = uuid4(), uuid4(), uuid4(), uuid4()


class _Factory:
    """Session factory whose transactions do nothing."""

    def begin(self) -> "_Factory":
        return self

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_: object) -> bool:
        return False


def _charge_point() -> OCPP201ChargePoint:
    return OCPP201ChargePoint(
        "LSC",
        object(),  # type: ignore[arg-type]
        _Factory(),  # type: ignore[arg-type]
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch, *, token_issued: bool
) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def topology(db: object, *args: object) -> tuple[Any, Any, Any]:
        return STATION_ID, EVSE_ID, CONNECTOR_ID

    async def activate(db: object, **kwargs: Any) -> Any:
        if not token_issued:
            raise ChargingSessionTokenError("not issued")
        calls.append(("activate", kwargs))
        return type(
            "R", (), {"session_id": SESSION_ID, "status": SessionStatus.ACTIVE}
        )()

    async def reference(db: object, **kwargs: Any) -> Any:
        return type("R", (), {"session_id": SESSION_ID})()

    async def meter(db: object, **kwargs: Any) -> None:
        calls.append(("energy", kwargs))

    async def complete(db: object, **kwargs: Any) -> None:
        calls.append(("complete", kwargs))

    monkeypatch.setattr(ocpp_state_service, "resolve_ocpp_topology", topology)
    monkeypatch.setattr(charging_service, "activate_pending_session", activate)
    monkeypatch.setattr(charging_service, "resolve_session_by_transaction", reference)
    monkeypatch.setattr(charging_service, "ingest_meter_values", meter)
    monkeypatch.setattr(charging_service, "complete_session", complete)
    return calls


_METER = [
    {
        "timestamp": "2026-10-01T09:00:00Z",
        "sampled_value": [
            {"value": 1520340, "measurand": "Energy.Active.Import.Register"}
        ],
    }
]


@pytest.mark.asyncio
async def test_started_event_with_the_issued_token_activates_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The idToken finds the PENDING session; the first energy reading is the start reading."""
    calls = _patch(monkeypatch, token_issued=True)

    response = await _charge_point().on_transaction_event(
        event_type="Started",
        timestamp="2026-10-01T09:00:00Z",
        trigger_reason="RemoteStart",
        seq_no=0,
        transaction_info={"transaction_id": "TX-1"},
        meter_value=_METER,
        evse={"id": 1, "connector_id": 1},
        id_token={"id_token": "TOKEN-1", "type": "Central"},
    )

    assert response.id_token_info is None
    activated = calls[0][1]
    assert (activated["id_token"], activated["transaction_id"]) == ("TOKEN-1", "TX-1")
    assert activated["meter_start_wh"] == Decimal(1520340)
    assert activated["started_at"] == datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_started_event_without_an_issued_token_is_answered_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A start with no token, or one no scan issued, creates nothing (CE-11)."""
    calls = _patch(monkeypatch, token_issued=False)

    for id_token in (None, {"id_token": "OTHER", "type": "Central"}):
        response = await _charge_point().on_transaction_event(
            event_type="Started",
            timestamp="2026-10-01T09:00:00Z",
            trigger_reason="Authorized",
            seq_no=0,
            transaction_info={"transaction_id": "TX-1"},
            meter_value=_METER,
            evse={"id": 1, "connector_id": 1},
            id_token=id_token,
        )
        assert response.id_token_info == {"status": "Invalid"}

    assert calls == []


@pytest.mark.asyncio
async def test_ended_event_stores_the_last_reading_as_the_closing_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stop completes the session with the last energy sample and the stopped reason."""
    calls = _patch(monkeypatch, token_issued=True)

    await _charge_point().on_transaction_event(
        event_type="Ended",
        timestamp="2026-10-01T10:00:00Z",
        trigger_reason="EVDeparted",
        seq_no=5,
        transaction_info={"transaction_id": "TX-1", "stopped_reason": "Local"},
        meter_value=_METER,
        evse={"id": 1, "connector_id": 1},
    )

    assert [kind for kind, _ in calls] == ["energy", "complete"]
    completed = calls[1][1]
    assert completed["meter_stop_wh"] == Decimal(1520340)
    assert completed["stop_reason"] == "Local"


@pytest.mark.asyncio
@pytest.mark.parametrize("is_issued", [True, False])
async def test_authorize_accepts_only_a_token_a_scan_issued(
    monkeypatch: pytest.MonkeyPatch, is_issued: bool
) -> None:
    """CE-11: the 2.0.1 Authorize answers Accepted for an issued token only."""
    asked: dict[str, Any] = {}

    async def station(db: object, ocpp_identity: str) -> Any:
        return STATION_ID

    async def valid(db: object, *, station_id: Any, id_token: str) -> bool:
        asked.update(station_id=station_id, id_token=id_token)
        return is_issued

    monkeypatch.setattr(ocpp_state_service, "resolve_station_id_by_identity", station)
    monkeypatch.setattr(charging_service, "is_start_token_valid", valid)

    response = await _charge_point().on_authorize(
        id_token={"id_token": "TOKEN-1", "type": "Central"}
    )

    assert response.id_token_info["status"] == ("Accepted" if is_issued else "Invalid")
    assert asked == {"station_id": STATION_ID, "id_token": "TOKEN-1"}
