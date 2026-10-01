"""Smoke tests for the charging_sessions service: lifecycle, meter watermark, station energy (F-B2, F-C5)."""

from datetime import datetime, timedelta, timezone
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
)
from app.domains.charging_sessions.models import ChargingSessionModel
from app.domains.charging_sessions.types import (
    MeterSampleInput,
    SessionEventType,
    SessionStatus,
)
from tests.builders import build_charging_session, fake_db_session


@pytest.mark.asyncio
async def test_charging_service_runs_started_meter_ended_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The charging service runs the correct lifecycle from Started to Ended."""
    session = build_charging_session()
    now = datetime.now(timezone.utc)
    inserted_events: list[SessionEventType] = []
    inserted_meters: list[Decimal] = []

    async def create_session(
        db: AsyncSession, **kwargs: object
    ) -> ChargingSessionModel:
        session.meter_start_wh = cast(Decimal | None, kwargs["meter_start_wh"])
        session.started_at = cast(datetime, kwargs["started_at"])
        return session

    async def get_by_transaction(
        db: AsyncSession, station_id: UUID, transaction_id: str
    ) -> ChargingSessionModel:
        return session

    async def get_by_id(db: AsyncSession, session_id: UUID) -> ChargingSessionModel:
        return session

    async def insert_event(db: AsyncSession, **kwargs: object) -> None:
        inserted_events.append(cast(SessionEventType, kwargs["event_type"]))

    async def insert_meter(db: AsyncSession, **kwargs: object) -> None:
        inserted_meters.append(cast(Decimal, kwargs["value"]))

    monkeypatch.setattr(charging_repository, "create_session", create_session)
    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )
    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_event", insert_event)
    monkeypatch.setattr(charging_repository, "insert_measurement", insert_meter)
    monkeypatch.setattr(charging_repository, "utc_now", lambda: now)

    started = await charging_service.ingest_transaction_event(
        fake_db_session(),
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=session.ocpp_transaction_id,
        event_type=SessionEventType.STARTED,
        event_occurred_at=now,
        seq_no=0,
        meter_start_wh=Decimal("1000"),
    )
    meter = await charging_service.ingest_meter_values(
        fake_db_session(),
        session_id=session.session_id,
        sample=MeterSampleInput(sampled_at=now, value_wh=Decimal("1500")),
    )
    ended = await charging_service.ingest_transaction_event(
        fake_db_session(),
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=session.ocpp_transaction_id,
        event_type=SessionEventType.ENDED,
        event_occurred_at=now,
        seq_no=2,
        meter_end_wh=Decimal("1750"),
    )

    assert started.status is SessionStatus.ACTIVE
    assert meter.accepted_count == 1
    assert ended.status is SessionStatus.COMPLETED
    assert session.meter_end_wh == Decimal("1750")
    assert session.energy_delivered_wh == Decimal("750")
    assert inserted_events == [SessionEventType.STARTED, SessionEventType.ENDED]
    assert inserted_meters == [Decimal("1500")]


@pytest.mark.asyncio
async def test_ingest_transaction_event_persists_seq_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """seq_no is threaded through to the repository unchanged (F-B2)."""
    session = build_charging_session()
    now = datetime.now(timezone.utc)
    captured: dict[str, object] = {}

    async def create_session(
        db: AsyncSession, **kwargs: object
    ) -> ChargingSessionModel:
        return session

    async def insert_event(db: AsyncSession, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(charging_repository, "create_session", create_session)
    monkeypatch.setattr(charging_repository, "insert_event", insert_event)
    monkeypatch.setattr(charging_repository, "utc_now", lambda: now)

    await charging_service.ingest_transaction_event(
        fake_db_session(),
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=session.ocpp_transaction_id,
        event_type=SessionEventType.STARTED,
        event_occurred_at=now,
        seq_no=7,
    )

    assert captured["seq_no"] == 7


@pytest.mark.asyncio
async def test_ingest_transaction_event_rejects_negative_seq_no() -> None:
    """A negative seq_no is rejected before any write (F-B2)."""
    session = build_charging_session()
    now = datetime.now(timezone.utc)

    with pytest.raises(ChargingSessionInputError):
        await charging_service.ingest_transaction_event(
            fake_db_session(),
            station_id=session.station_id,
            evse_id=session.evse_id,
            connector_id=session.connector_id,
            transaction_id=session.ocpp_transaction_id,
            event_type=SessionEventType.STARTED,
            event_occurred_at=now,
            seq_no=-1,
        )


@pytest.mark.asyncio
async def test_ingest_transaction_event_rejects_boolean_seq_no() -> None:
    """A bool seq_no is rejected - isinstance(True, int) must not collide with 0 (F-B2)."""
    session = build_charging_session()
    now = datetime.now(timezone.utc)

    with pytest.raises(ChargingSessionInputError):
        await charging_service.ingest_transaction_event(
            fake_db_session(),
            station_id=session.station_id,
            evse_id=session.evse_id,
            connector_id=session.connector_id,
            transaction_id=session.ocpp_transaction_id,
            event_type=SessionEventType.STARTED,
            event_occurred_at=now,
            seq_no=cast(int, True),
        )


@pytest.mark.asyncio
async def test_ingest_transaction_event_rejects_update_on_completed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An event for an already-COMPLETED session is refused, not applied (F-B2)."""
    session = build_charging_session(status=SessionStatus.COMPLETED)
    now = datetime.now(timezone.utc)

    async def get_by_transaction(
        db: AsyncSession, station_id: UUID, transaction_id: str
    ) -> ChargingSessionModel:
        return session

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("insert_event must not run on a COMPLETED session")

    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )
    monkeypatch.setattr(charging_repository, "insert_event", fail_if_called)

    with pytest.raises(ChargingSessionStateError):
        await charging_service.ingest_transaction_event(
            fake_db_session(),
            station_id=session.station_id,
            evse_id=session.evse_id,
            connector_id=session.connector_id,
            transaction_id=session.ocpp_transaction_id,
            event_type=SessionEventType.UPDATED,
            event_occurred_at=now,
            seq_no=5,
        )


@pytest.mark.asyncio
async def test_ingest_transaction_event_rejects_repeated_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate Ended is refused and does not re-stamp ended_at (F-B2)."""
    original_ended_at = datetime.now(timezone.utc)
    session = build_charging_session(status=SessionStatus.COMPLETED)
    session.ended_at = original_ended_at

    async def get_by_transaction(
        db: AsyncSession, station_id: UUID, transaction_id: str
    ) -> ChargingSessionModel:
        return session

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("insert_event must not run on a COMPLETED session")

    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )
    monkeypatch.setattr(charging_repository, "insert_event", fail_if_called)

    with pytest.raises(ChargingSessionStateError):
        await charging_service.ingest_transaction_event(
            fake_db_session(),
            station_id=session.station_id,
            evse_id=session.evse_id,
            connector_id=session.connector_id,
            transaction_id=session.ocpp_transaction_id,
            event_type=SessionEventType.ENDED,
            event_occurred_at=datetime.now(timezone.utc),
            seq_no=9,
        )

    assert session.ended_at == original_ended_at


@pytest.mark.asyncio
async def test_ingest_meter_values_rejects_sample_on_completed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A MeterValues sample for a COMPLETED session is refused (F-B2)."""
    session = build_charging_session(status=SessionStatus.COMPLETED)

    async def get_by_id(db: AsyncSession, session_id: UUID) -> ChargingSessionModel:
        return session

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("insert_measurement must not run on a COMPLETED session")

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_measurement", fail_if_called)

    with pytest.raises(ChargingSessionStateError):
        await charging_service.ingest_meter_values(
            fake_db_session(),
            session_id=session.session_id,
            sample=MeterSampleInput(
                sampled_at=datetime.now(timezone.utc), value_wh=Decimal("100")
            ),
        )


@pytest.mark.asyncio
async def test_ingest_meter_values_ignores_stale_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sample older than the stored watermark is discarded, not applied (F-B2)."""
    watermark = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    session = build_charging_session(
        meter_start_wh=Decimal("1000"),
        meter_end_wh=Decimal("1500"),
        meter_end_sampled_at=watermark,
    )
    inserted: list[Decimal] = []

    async def get_by_id(db: AsyncSession, session_id: UUID) -> ChargingSessionModel:
        return session

    async def insert_meter(db: AsyncSession, **kwargs: object) -> None:
        inserted.append(cast(Decimal, kwargs["value"]))

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_measurement", insert_meter)

    result = await charging_service.ingest_meter_values(
        fake_db_session(),
        session_id=session.session_id,
        sample=MeterSampleInput(
            sampled_at=watermark - timedelta(minutes=5), value_wh=Decimal("1400")
        ),
    )

    # The sample row is still appended to history (append-only)...
    assert inserted == [Decimal("1400")]
    # ...but the aggregate's watermark and meter reading are untouched.
    assert session.meter_end_wh == Decimal("1500")
    assert session.meter_end_sampled_at == watermark
    assert result.accepted_count == 1


@pytest.mark.asyncio
async def test_ingest_meter_values_applies_newer_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sample newer than the stored watermark advances the aggregate (F-B2)."""
    watermark = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    session = build_charging_session(
        meter_start_wh=Decimal("1000"),
        meter_end_wh=Decimal("1500"),
        meter_end_sampled_at=watermark,
    )

    async def get_by_id(db: AsyncSession, session_id: UUID) -> ChargingSessionModel:
        return session

    async def insert_meter(db: AsyncSession, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_measurement", insert_meter)

    newer = watermark + timedelta(minutes=5)
    await charging_service.ingest_meter_values(
        fake_db_session(),
        session_id=session.session_id,
        sample=MeterSampleInput(sampled_at=newer, value_wh=Decimal("1600")),
    )

    assert session.meter_end_wh == Decimal("1600")
    assert session.meter_end_sampled_at == newer
    assert session.energy_delivered_wh == Decimal("600")


@pytest.mark.asyncio
async def test_ingest_meter_values_applies_equal_timestamp_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tie (same timestamp as the watermark) still applies (F-B2).

    One OCPP message can carry several sampledValues sharing one
    timestamp - rejecting ties would drop legitimate samples.
    """
    watermark = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    session = build_charging_session(
        meter_start_wh=Decimal("1000"),
        meter_end_wh=Decimal("1500"),
        meter_end_sampled_at=watermark,
    )

    async def get_by_id(db: AsyncSession, session_id: UUID) -> ChargingSessionModel:
        return session

    async def insert_meter(db: AsyncSession, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_measurement", insert_meter)

    await charging_service.ingest_meter_values(
        fake_db_session(),
        session_id=session.session_id,
        sample=MeterSampleInput(sampled_at=watermark, value_wh=Decimal("1550")),
    )

    assert session.meter_end_wh == Decimal("1550")


@pytest.mark.asyncio
async def test_ingest_meter_values_still_applies_decreasing_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer-timestamped but lower reading still applies (F-B2).

    This pins the item-27 boundary: fix #3 is a *time*-ordering check
    only, never a value check. A meter reset (register decreases while
    time still moves forward) is deliberately NOT caught here - that
    belongs to the deferred reliability path. If this test starts
    failing, someone has widened the fix beyond its intended scope.
    """
    watermark = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    session = build_charging_session(
        meter_start_wh=Decimal("1000"),
        meter_end_wh=Decimal("1500"),
        meter_end_sampled_at=watermark,
    )

    async def get_by_id(db: AsyncSession, session_id: UUID) -> ChargingSessionModel:
        return session

    async def insert_meter(db: AsyncSession, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_measurement", insert_meter)

    newer_but_lower = watermark + timedelta(minutes=5)
    await charging_service.ingest_meter_values(
        fake_db_session(),
        session_id=session.session_id,
        sample=MeterSampleInput(sampled_at=newer_but_lower, value_wh=Decimal("200")),
    )

    assert session.meter_end_wh == Decimal("200")
    assert session.meter_end_sampled_at == newer_but_lower


@pytest.mark.asyncio
async def test_ingest_transaction_event_uses_sample_time_for_meter_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The embedded meter sample's own timestamp is preferred over the event time (F-B2)."""
    session = build_charging_session(meter_start_wh=Decimal("1000"))
    sample_time = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    event_time = sample_time + timedelta(minutes=10)

    async def get_by_transaction(
        db: AsyncSession, station_id: UUID, transaction_id: str
    ) -> ChargingSessionModel:
        return session

    async def insert_event(db: AsyncSession, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )
    monkeypatch.setattr(charging_repository, "insert_event", insert_event)
    monkeypatch.setattr(charging_repository, "utc_now", lambda: event_time)

    await charging_service.ingest_transaction_event(
        fake_db_session(),
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=session.ocpp_transaction_id,
        event_type=SessionEventType.UPDATED,
        event_occurred_at=event_time,
        seq_no=1,
        meter_end_wh=Decimal("1200"),
        meter_end_sampled_at=sample_time,
    )

    assert session.meter_end_sampled_at == sample_time


@pytest.mark.asyncio
async def test_ingest_transaction_event_falls_back_to_event_time_for_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an embedded sample timestamp, the event's own time is used (F-B2)."""
    session = build_charging_session(meter_start_wh=Decimal("1000"))
    event_time = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    async def get_by_transaction(
        db: AsyncSession, station_id: UUID, transaction_id: str
    ) -> ChargingSessionModel:
        return session

    async def insert_event(db: AsyncSession, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )
    monkeypatch.setattr(charging_repository, "insert_event", insert_event)
    monkeypatch.setattr(charging_repository, "utc_now", lambda: event_time)

    await charging_service.ingest_transaction_event(
        fake_db_session(),
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=session.ocpp_transaction_id,
        event_type=SessionEventType.UPDATED,
        event_occurred_at=event_time,
        seq_no=1,
        meter_end_wh=Decimal("1200"),
    )

    assert session.meter_end_sampled_at == event_time


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
