"""Smoke tests for the charging_sessions service: scan, start, stop, station energy (F-B2, F-C5, CE-10, CE-11)."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionStateError,
    ChargingSessionTokenError,
)
from app.domains.charging_sessions.models import ChargingSessionModel
from app.domains.charging_sessions.types import (
    QR_TOKEN_LENGTH,
    ChargingSessionListFilter,
    MeterSampleInput,
    SessionStatus,
)
from tests.builders import build_charging_session, fake_db_session

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_create_pending_session_issues_a_token_that_fits_ocpp16(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan creates a PENDING row and returns a token of at most 20 characters."""
    captured: dict[str, object] = {}
    session = build_charging_session(status=SessionStatus.PENDING)

    async def create_pending(
        db: AsyncSession, **kwargs: object
    ) -> ChargingSessionModel:
        captured.update(kwargs)
        return session

    monkeypatch.setattr(charging_repository, "create_pending_session", create_pending)

    reference = await charging_service.create_pending_session(
        fake_db_session(),
        station_id=session.station_id,
        organization_id=session.organization_id,
        started_by=session.started_by,
    )

    assert 0 < len(reference.id_token) <= QR_TOKEN_LENGTH <= 20
    assert captured["id_token"] == reference.id_token
    assert captured["vehicle_id"] is None
    assert reference.session_id == session.session_id


@pytest.mark.asyncio
async def test_activate_pending_session_fills_the_charger_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A start message with the issued token turns the PENDING row ACTIVE (CE-11)."""
    session = build_charging_session(status=SessionStatus.PENDING)
    evse_id, connector_id = uuid4(), uuid4()

    async def find_pending(
        db: AsyncSession, station_id: UUID, id_token: str
    ) -> ChargingSessionModel | None:
        return session if id_token == session.id_token else None

    monkeypatch.setattr(
        charging_repository, "find_pending_session_by_token", find_pending
    )

    result = await charging_service.activate_pending_session(
        fake_db_session(),
        station_id=session.station_id,
        evse_id=evse_id,
        connector_id=connector_id,
        id_token=session.id_token,
        transaction_id=" 1042 ",
        started_at=NOW,
        meter_start_wh=Decimal("1520340"),
    )

    assert (result.session_id, result.status) == (
        session.session_id,
        SessionStatus.ACTIVE,
    )
    assert session.ocpp_transaction_id == "1042"
    assert (session.evse_id, session.connector_id) == (evse_id, connector_id)
    assert session.started_at == NOW
    assert session.meter_start_wh == Decimal("1520340")


@pytest.mark.asyncio
async def test_activate_pending_session_refuses_a_token_no_scan_issued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown token creates no row and raises the token error (CE-11)."""

    async def find_pending(
        db: AsyncSession, station_id: UUID, id_token: str
    ) -> ChargingSessionModel | None:
        return None

    monkeypatch.setattr(
        charging_repository, "find_pending_session_by_token", find_pending
    )

    with pytest.raises(ChargingSessionTokenError):
        await charging_service.activate_pending_session(
            fake_db_session(),
            station_id=uuid4(),
            evse_id=uuid4(),
            connector_id=uuid4(),
            id_token="NOT-ISSUED",
            transaction_id="1",
            started_at=NOW,
            meter_start_wh=Decimal(0),
        )


@pytest.mark.asyncio
async def test_activate_pending_session_requires_a_start_reading_and_timezone() -> None:
    """An ACTIVE session always has a start reading (CE-10); naive times are refused."""
    arguments = dict(
        station_id=uuid4(),
        evse_id=uuid4(),
        connector_id=uuid4(),
        id_token="TOKEN",
        transaction_id="1",
    )
    with pytest.raises(ChargingSessionInputError, match="meter_start_wh"):
        await charging_service.activate_pending_session(
            fake_db_session(),
            started_at=NOW,
            meter_start_wh=None,
            **arguments,  # type: ignore[arg-type]
        )
    with pytest.raises(ChargingSessionInputError, match="started_at"):
        await charging_service.activate_pending_session(
            fake_db_session(),
            started_at=datetime(2026, 10, 1),
            meter_start_wh=Decimal(0),
            **arguments,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_complete_session_stores_the_declared_closing_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stop message completes the session; meter_stop_wh is stored as sent (CE-12)."""
    session = build_charging_session(meter_start_wh=Decimal("1000"))

    async def get_by_transaction(
        db: AsyncSession, station_id: UUID, transaction_id: str
    ) -> ChargingSessionModel:
        return session

    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )

    result = await charging_service.complete_session(
        fake_db_session(),
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=cast(str, session.ocpp_transaction_id),
        ended_at=NOW,
        stop_reason="EVDisconnected",
        meter_stop_wh=Decimal("1750"),
    )

    assert result.status is SessionStatus.COMPLETED
    assert session.ended_at == NOW
    assert session.meter_stop_wh == Decimal("1750")
    assert session.stop_reason == "EVDisconnected"


@pytest.mark.asyncio
async def test_complete_session_refuses_a_repeated_stop_and_a_wrong_gun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate stop does not re-stamp ended_at; another gun is an input error."""
    session = build_charging_session(status=SessionStatus.COMPLETED)
    original_ended_at = session.ended_at

    async def get_by_transaction(
        db: AsyncSession, station_id: UUID, transaction_id: str
    ) -> ChargingSessionModel:
        return session

    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )

    with pytest.raises(ChargingSessionStateError):
        await charging_service.complete_session(
            fake_db_session(),
            station_id=session.station_id,
            transaction_id="TX-TEST-001",
            ended_at=NOW,
        )
    session.status = SessionStatus.ACTIVE
    with pytest.raises(ChargingSessionInputError, match="topology"):
        await charging_service.complete_session(
            fake_db_session(),
            station_id=session.station_id,
            connector_id=uuid4(),
            transaction_id="TX-TEST-001",
            ended_at=NOW,
        )
    assert session.ended_at == original_ended_at


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SessionStatus.PENDING, SessionStatus.COMPLETED])
async def test_ingest_meter_values_refuses_a_session_that_is_not_active(
    monkeypatch: pytest.MonkeyPatch, status: SessionStatus
) -> None:
    """A sample for a session that is not ACTIVE is refused, nothing is stored (F-B2)."""
    session = build_charging_session(status=status)

    async def get_by_id(db: AsyncSession, session_id: UUID) -> ChargingSessionModel:
        return session

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("insert_measurement must not run")

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_measurement", fail_if_called)

    with pytest.raises(ChargingSessionStateError):
        await charging_service.ingest_meter_values(
            fake_db_session(),
            session_id=session.session_id,
            sample=MeterSampleInput(sampled_at=NOW, value_wh=Decimal("100")),
        )


@pytest.mark.asyncio
async def test_ingest_meter_values_stores_wh_with_the_ocpp_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The energy register is stored in Wh with the default context and location."""
    session = build_charging_session()
    inserted: list[dict[str, object]] = []

    async def get_by_id(db: AsyncSession, session_id: UUID) -> ChargingSessionModel:
        return session

    async def insert_measurement(db: AsyncSession, **kwargs: object) -> None:
        inserted.append(kwargs)

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_measurement", insert_measurement)

    await charging_service.ingest_meter_values(
        fake_db_session(),
        session_id=session.session_id,
        sample=MeterSampleInput(sampled_at=NOW, value_wh=Decimal("1500")),
    )

    assert inserted == [
        {
            "session_id": session.session_id,
            "sampled_at": NOW,
            "measurand": "Energy.Active.Import.Register",
            "value": Decimal("1500"),
            "unit": "Wh",
            "context": "Sample.Periodic",
            "measurement_location": "Outlet",
        }
    ]


@pytest.mark.asyncio
async def test_get_station_energy_summary_converts_wh_to_kwh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The energy summary converts the repository's Wh total to kWh (F-C5)."""
    station_id = uuid4()
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)

    async def energy_summary(db: AsyncSession, **kwargs: object) -> tuple[Decimal, int]:
        assert kwargs["station_id"] == station_id
        assert kwargs["start_time"] == start
        assert kwargs["end_time"] == end
        return Decimal("12500.000"), 3

    monkeypatch.setattr(
        charging_repository, "get_station_energy_summary", energy_summary
    )

    summary = await charging_service.get_station_energy_summary(
        fake_db_session(), station_id=station_id, start_time=start, end_time=end
    )

    assert summary.station_id == station_id
    assert summary.total_energy_kwh == pytest.approx(12.5)
    assert summary.session_count == 3


@pytest.mark.asyncio
async def test_get_station_energy_summary_rejects_naive_timestamp() -> None:
    """A start_time/end_time with no timezone is rejected before any query runs."""
    with pytest.raises(ChargingSessionInputError, match="start_time"):
        await charging_service.get_station_energy_summary(
            fake_db_session(),
            station_id=uuid4(),
            start_time=datetime(2026, 9, 1),
            end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_get_station_energy_summary_rejects_non_positive_range() -> None:
    """end_time at or before start_time is rejected."""
    same_instant = datetime(2026, 9, 1, tzinfo=timezone.utc)

    with pytest.raises(ChargingSessionInputError):
        await charging_service.get_station_energy_summary(
            fake_db_session(),
            station_id=uuid4(),
            start_time=same_instant,
            end_time=same_instant,
        )


@pytest.mark.asyncio
async def test_list_charging_sessions_keeps_a_page_size_below_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small page_size is honoured, not raised to the default page size."""
    session = build_charging_session()
    seen: dict[str, object] = {}

    async def list_sessions(
        db: AsyncSession,
        *,
        filters: ChargingSessionListFilter,
        offset: int,
        limit: int,
    ) -> list[ChargingSessionModel]:
        seen.update(offset=offset, limit=limit)
        return [session]

    async def count_sessions(
        db: AsyncSession, filters: ChargingSessionListFilter
    ) -> int:
        return 7

    monkeypatch.setattr(charging_repository, "list_sessions", list_sessions)
    monkeypatch.setattr(charging_repository, "count_sessions", count_sessions)

    response = await charging_service.list_charging_sessions(
        fake_db_session(), page=2, page_size=3
    )

    assert seen == {"offset": 3, "limit": 3}
    assert (response.total, response.page, response.page_size) == (7, 2, 3)
    assert [item.session_id for item in response.items] == [session.session_id]
