"""Smoke tests for the charging session read side (F-B2 summary/filters, F-C5 series)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
from app.domains.charging_sessions.exceptions import ChargingSessionInputError
from app.domains.charging_sessions.models import ChargingSessionModel
from app.domains.charging_sessions.types import (
    ChargingSessionListFilter,
    EnergySeriesGranularity,
    SessionStatus,
)
from app.libs.common.config import settings
from tests.builders import build_charging_session, fake_db_session
from tests.principals import build_internal_principal

HO_CHI_MINH = ZoneInfo("Asia/Ho_Chi_Minh")
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


# --- session summary (F-B2) -------------------------------------------------


def test_duration_uses_ended_at_or_now_and_is_never_negative() -> None:
    """Completed: ended - started; active: now - started; clock skew floors at 0."""
    session = build_charging_session()
    session.started_at = T0
    session.ended_at = T0 + timedelta(minutes=45)

    assert charging_service.calculate_session_duration_seconds(
        session, now=T0 + timedelta(hours=5)
    ) == (45 * 60)

    session.ended_at = None
    assert (
        charging_service.calculate_session_duration_seconds(
            session, now=T0 + timedelta(seconds=90)
        )
        == 90
    )
    assert (
        charging_service.calculate_session_duration_seconds(
            session, now=T0 - timedelta(seconds=5)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_session_detail_adds_the_read_time_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /charging-sessions/{id} carries energy, duration, first/last SoC, max power."""
    session = build_charging_session(
        status=SessionStatus.COMPLETED,
        meter_start_wh=Decimal("1000"),
        meter_stop_wh=Decimal("51000"),
    )
    session.started_at = T0
    session.ended_at = T0 + timedelta(minutes=30)
    queried_measurands: list[str] = []

    async def get_by_id(
        db: AsyncSession, session_id: UUID, **_scope: object
    ) -> ChargingSessionModel:
        return session

    async def first_value(
        db: AsyncSession, session_id: UUID, *, measurand: str
    ) -> Decimal | None:
        queried_measurands.append(measurand)
        return Decimal("21.5")

    async def last_value(
        db: AsyncSession,
        session_id: UUID,
        *,
        measurand: str,
        measurement_location: str | None = None,
    ) -> Decimal | None:
        return Decimal("80")

    async def max_value(
        db: AsyncSession,
        session_id: UUID,
        *,
        measurand: str,
        measurement_location: str,
    ) -> Decimal | None:
        queried_measurands.append(f"{measurand}@{measurement_location}")
        return Decimal("150000")

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(
        charging_repository, "find_first_measurement_value", first_value
    )
    monkeypatch.setattr(charging_repository, "find_last_measurement_value", last_value)
    monkeypatch.setattr(charging_repository, "find_max_measurement_value", max_value)

    detail = await charging_service.get_charging_session(
        fake_db_session(), session.session_id, principal=build_internal_principal()
    )

    assert detail.session_id == session.session_id
    assert detail.duration_seconds == 1800
    assert detail.energy_delivered_wh == Decimal("50000")
    assert (detail.soc_start_percent, detail.soc_end_percent) == (21.5, 80.0)
    assert detail.max_power_kw == pytest.approx(150.0)
    assert queried_measurands == ["SoC", "Power.Active.Import@Outlet"]


@pytest.mark.asyncio
async def test_session_detail_without_measurements_has_null_summary_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session with no SoC or power sample has null summary fields."""
    session = build_charging_session()

    async def get_by_id(
        db: AsyncSession, session_id: UUID, **_scope: object
    ) -> ChargingSessionModel:
        return session

    async def no_value(
        db: AsyncSession, session_id: UUID, *, measurand: str, **_: object
    ) -> Decimal | None:
        return None

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "find_first_measurement_value", no_value)
    monkeypatch.setattr(charging_repository, "find_last_measurement_value", no_value)
    monkeypatch.setattr(charging_repository, "find_max_measurement_value", no_value)

    detail = await charging_service.get_charging_session(
        fake_db_session(), session.session_id, principal=build_internal_principal()
    )

    assert (detail.soc_start_percent, detail.soc_end_percent) == (None, None)
    assert (detail.max_power_kw, detail.energy_delivered_wh) == (None, None)
    assert detail.duration_seconds >= 0


# --- list filters (F-B2) ----------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_reach_both_queries_normalized_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every filter is passed on; time bounds are converted to UTC."""
    station_id, connector_id = uuid4(), uuid4()
    seen_filters: list[ChargingSessionListFilter] = []

    async def list_sessions(
        db: AsyncSession,
        *,
        filters: ChargingSessionListFilter,
        offset: int,
        limit: int,
    ) -> list[ChargingSessionModel]:
        seen_filters.append(filters)
        return []

    async def count_sessions(
        db: AsyncSession, filters: ChargingSessionListFilter
    ) -> int:
        seen_filters.append(filters)
        return 0

    monkeypatch.setattr(charging_repository, "list_sessions", list_sessions)
    monkeypatch.setattr(charging_repository, "count_sessions", count_sessions)
    started_from = datetime(2026, 10, 1, 7, 0, tzinfo=HO_CHI_MINH)

    await charging_service.list_charging_sessions(
        fake_db_session(),
        page=1,
        page_size=10,
        station_id=station_id,
        connector_id=connector_id,
        status=SessionStatus.COMPLETED,
        started_from=started_from,
        started_to=started_from + timedelta(days=1),
        principal=build_internal_principal(),
    )

    expected = ChargingSessionListFilter(
        station_id=station_id,
        connector_id=connector_id,
        status=SessionStatus.COMPLETED,
        started_from=datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
        started_to=datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc),
    )
    assert seen_filters == [expected, expected]
    assert seen_filters[0].started_from is not None
    assert seen_filters[0].started_from.tzinfo is timezone.utc


@pytest.mark.asyncio
async def test_list_filters_reject_naive_or_backward_time_bounds() -> None:
    """A naive bound or started_to <= started_from is a 400, before any query."""
    with pytest.raises(ChargingSessionInputError, match="started_from"):
        await charging_service.list_charging_sessions(
            fake_db_session(),
            page=1,
            page_size=10,
            started_from=datetime(2026, 10, 1),
            principal=build_internal_principal(),
        )
    with pytest.raises(ChargingSessionInputError, match="started_to"):
        await charging_service.list_charging_sessions(
            fake_db_session(),
            page=1,
            page_size=10,
            started_from=T0,
            started_to=T0,
            principal=build_internal_principal(),
        )


# --- energy series (F-C5) ---------------------------------------------------


def test_day_buckets_are_cut_at_local_midnight() -> None:
    """D4: a day is a calendar day of APP_REPORT_TIMEZONE, returned as UTC."""
    bucket_starts = charging_service.build_energy_series_bucket_starts(
        start_time=datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 10, 2, 17, 0, tzinfo=timezone.utc),
        granularity=EnergySeriesGranularity.DAY,
        report_zone=HO_CHI_MINH,
    )

    # 2026-09-30T20:00Z is 2026-10-01 03:00 local: its day starts at 17:00Z.
    assert bucket_starts == [
        datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc),
    ]


def test_hour_buckets_cover_the_window_densely() -> None:
    """Every clock hour from the one containing start_time up to end_time."""
    bucket_starts = charging_service.build_energy_series_bucket_starts(
        start_time=T0 + timedelta(minutes=30),
        end_time=T0 + timedelta(hours=3),
        granularity=EnergySeriesGranularity.HOUR,
        report_zone=HO_CHI_MINH,
    )

    assert bucket_starts == [T0 + timedelta(hours=hour) for hour in range(3)]


def test_energy_deltas_follow_time_order_and_skip_a_decreasing_register() -> None:
    """D5: each delta is stamped with its later reading; a reset adds nothing."""
    deltas = charging_service.calculate_energy_deltas(
        [
            (T0 + timedelta(minutes=20), Decimal("1600")),
            (T0, Decimal("1000")),
            (T0 + timedelta(minutes=10), Decimal("1250")),
            (T0 + timedelta(minutes=30), Decimal("100")),
            (T0 + timedelta(minutes=40), Decimal("300")),
        ]
    )

    assert deltas == [
        (T0 + timedelta(minutes=10), Decimal("250")),
        (T0 + timedelta(minutes=20), Decimal("350")),
        (T0 + timedelta(minutes=40), Decimal("200")),
    ]


@pytest.mark.asyncio
async def test_energy_series_splits_a_session_across_a_bucket_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Energy is attributed per delta, not as one lump at ended_at (D5)."""
    station_id = uuid4()
    session = build_charging_session(status=SessionStatus.COMPLETED)
    session.station_id = station_id
    session.started_at = T0 + timedelta(minutes=40)
    session.meter_start_wh = Decimal("1000")
    session.ended_at = T0 + timedelta(hours=1, minutes=20)
    session.meter_stop_wh = Decimal("4000")
    samples = [
        (T0 + timedelta(minutes=55), Decimal("2000")),
        (T0 + timedelta(hours=1, minutes=10), Decimal("3500")),
        (T0 + timedelta(hours=1, minutes=20), Decimal("4000")),
    ]
    seen_bounds: list[datetime] = []

    async def list_sessions(
        db: AsyncSession,
        station: UUID,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> list[ChargingSessionModel]:
        assert station == station_id
        return [session]

    async def list_samples(
        db: AsyncSession, session_id: UUID, *, end_time: datetime
    ) -> list[tuple[datetime, Decimal]]:
        seen_bounds.append(end_time)
        return [sample for sample in samples if sample[0] < end_time]

    monkeypatch.setattr(
        charging_repository, "list_sessions_by_station_in_window", list_sessions
    )
    monkeypatch.setattr(charging_repository, "list_energy_samples_before", list_samples)

    series = await charging_service.get_station_energy_series(
        fake_db_session(),
        station_id=station_id,
        start_time=T0,
        end_time=T0 + timedelta(hours=3),
        granularity=EnergySeriesGranularity.HOUR,
    )

    assert [item.bucket_start for item in series.items] == [
        T0,
        T0 + timedelta(hours=1),
        T0 + timedelta(hours=2),
    ]
    # 09:40 start (1000) -> 09:55 (2000): 1 kWh in the 09:00 bucket;
    # 10:10 (3500) and 10:20 (4000): 2 kWh in the 10:00 bucket; 11:00 empty.
    assert [item.energy_kwh for item in series.items] == [1.0, 2.0, 0.0]
    assert series.total_energy_kwh == pytest.approx(3.0)
    assert series.report_timezone == settings.APP_REPORT_TIMEZONE
    assert seen_bounds == [T0 + timedelta(hours=3)]


@pytest.mark.asyncio
async def test_energy_series_counts_only_deltas_whose_later_reading_is_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reading before start_time is only the baseline of the first delta."""
    session = build_charging_session()
    session.started_at = T0 - timedelta(hours=2)
    session.meter_start_wh = Decimal("0")
    samples = [
        (T0 - timedelta(hours=1), Decimal("5000")),
        (T0 + timedelta(minutes=30), Decimal("7000")),
    ]

    async def list_sessions(
        db: AsyncSession,
        station: UUID,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> list[ChargingSessionModel]:
        return [session]

    async def list_samples(
        db: AsyncSession, session_id: UUID, *, end_time: datetime
    ) -> list[tuple[datetime, Decimal]]:
        return samples

    monkeypatch.setattr(
        charging_repository, "list_sessions_by_station_in_window", list_sessions
    )
    monkeypatch.setattr(charging_repository, "list_energy_samples_before", list_samples)

    series = await charging_service.get_station_energy_series(
        fake_db_session(),
        station_id=session.station_id,
        start_time=T0,
        end_time=T0 + timedelta(hours=1),
        granularity=EnergySeriesGranularity.HOUR,
    )

    assert [item.energy_kwh for item in series.items] == [2.0]


@pytest.mark.asyncio
async def test_energy_series_rejects_a_window_longer_than_the_setting() -> None:
    """The range is bounded by CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS."""
    too_long = timedelta(days=settings.CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS, hours=1)

    with pytest.raises(ChargingSessionInputError, match="must not exceed"):
        await charging_service.get_station_energy_series(
            fake_db_session(),
            station_id=uuid4(),
            start_time=T0,
            end_time=T0 + too_long,
            granularity=EnergySeriesGranularity.DAY,
        )
    with pytest.raises(ChargingSessionInputError, match="start_time"):
        await charging_service.get_station_energy_series(
            fake_db_session(),
            station_id=uuid4(),
            start_time=datetime(2026, 10, 1),
            end_time=T0,
            granularity=EnergySeriesGranularity.DAY,
        )


@pytest.mark.asyncio
async def test_resolve_station_energy_total_returns_a_dto_in_wh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross-domain F-C5 entry point returns a frozen DTO, never a schema."""
    station_id = uuid4()

    async def energy_summary(db: AsyncSession, **kwargs: object) -> tuple[Decimal, int]:
        return Decimal("2500.000"), 2

    monkeypatch.setattr(
        charging_repository, "get_station_energy_summary", energy_summary
    )

    total = await charging_service.resolve_station_energy_total(
        fake_db_session(),
        station_id=station_id,
        start_time=T0,
        end_time=T0 + timedelta(days=1),
    )

    assert (total.station_id, total.total_energy_wh, total.session_count) == (
        station_id,
        Decimal("2500.000"),
        2,
    )
