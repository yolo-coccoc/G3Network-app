"""Smoke tests for the charging_stations service.

Covers the nearby search and live availability (F-D1, F-A2 under decision D3),
connector status and the station status view (F-C2), and the all-stations
energy report (F-C5).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
from app.domains.charging_sessions.types import StationEnergyTotal
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingStationNotFoundError,
    ChargingStationReportRangeError,
)
from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingStationModel,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingStationMaintenanceStatus,
)
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location
from tests.builders import build_charging_station_record, fake_db_session


def test_to_nearby_charging_station_response_decodes_location_and_distance() -> None:
    """to_nearby_charging_station_response() decodes lat/lon and carries distance_km (F-D1)."""
    station = build_charging_station_record()

    response = charging_stations_service.to_nearby_charging_station_response(
        station, connector_count=4, available_connector_count=1, distance_km=2.5
    )

    assert response.latitude == pytest.approx(10.762622)
    assert response.longitude == pytest.approx(106.660172)
    assert response.connector_count == 4
    assert response.available_connector_count == 1
    assert response.distance_km == pytest.approx(2.5)
    assert response.power_rating_kw == pytest.approx(120.0)
    # The builder's station never connected.
    assert response.is_online is False


def test_nearby_response_is_online_follows_last_seen_at() -> None:
    """is_online on a nearby result is derived exactly like the station detail's."""
    now = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    station = build_charging_station_record()
    station.last_seen_at = now - timedelta(
        seconds=settings.CHARGING_OFFLINE_TIMEOUT_SECONDS - 1
    )

    online = charging_stations_service.to_nearby_charging_station_response(
        station,
        connector_count=2,
        available_connector_count=2,
        distance_km=1.0,
        now=now,
    )
    offline = charging_stations_service.to_nearby_charging_station_response(
        station,
        connector_count=2,
        available_connector_count=2,
        distance_km=1.0,
        now=now + timedelta(seconds=2),
    )

    assert (online.is_online, offline.is_online) == (True, False)


@pytest.mark.asyncio
async def test_list_nearby_charging_stations_clamps_radius_and_paginates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nearby-search service clamps radius/page/page_size before querying."""
    station = build_charging_station_record()
    captured: dict[str, object] = {}

    async def list_nearby(
        db: AsyncSession, **kwargs: object
    ) -> list[tuple[ChargingStationModel, float]]:
        captured.update(kwargs)
        return [(station, 1500.0)]

    async def count_nearby(db: AsyncSession, **kwargs: object) -> int:
        return 1

    async def connector_count(db: AsyncSession, station_id: UUID) -> int:
        return 2

    async def available_connector_count(db: AsyncSession, station_id: UUID) -> int:
        return 1

    monkeypatch.setattr(
        charging_stations_repository, "list_nearby_stations", list_nearby
    )
    monkeypatch.setattr(
        charging_stations_repository, "count_nearby_stations", count_nearby
    )
    monkeypatch.setattr(
        charging_stations_repository, "count_connectors_by_station_id", connector_count
    )
    monkeypatch.setattr(
        charging_stations_repository,
        "count_available_connectors_by_station_id",
        available_connector_count,
    )

    response = await charging_stations_service.list_nearby_charging_stations(
        fake_db_session(),
        latitude=10.762622,
        longitude=106.660172,
        radius_km=settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM + 100,
        page=0,
        page_size=0,
    )

    assert captured["radius_meters"] == pytest.approx(
        settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM * 1000
    )
    # is_available_only defaults to False (additive API change).
    assert captured["is_available_only"] is False
    assert response.page == settings.API_DEFAULT_PAGE
    # normalize_page_window floors a non-positive page size at 1.
    assert response.page_size == 1
    assert len(response.items) == 1
    assert response.items[0].distance_km == pytest.approx(1.5)
    assert response.items[0].connector_count == 2
    assert response.items[0].available_connector_count == 1


def _compile(statement: Any) -> str:
    """Render a SQLAlchemy statement as PostgreSQL SQL with literal values."""
    return str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )


def test_available_only_nearby_filter_requires_operational_and_a_free_connector() -> (
    None
):
    """is_available_only adds OPERATIONAL plus an EXISTS on an Available connector (D3)."""
    location = coordinates_to_location(10.0, 106.0)
    assert location is not None

    plain = charging_stations_repository._nearby_station_conditions(
        location,
        radius_meters=1000.0,
        connector_standard=None,
        min_power_kw=None,
        is_operational_only=False,
        is_available_only=False,
    )
    available = charging_stations_repository._nearby_station_conditions(
        location,
        radius_meters=1000.0,
        connector_standard=None,
        min_power_kw=None,
        is_operational_only=False,
        is_available_only=True,
    )

    plain_sql = _compile(select(ChargingStationModel).where(*plain))
    available_sql = _compile(select(ChargingStationModel).where(*available))
    assert "EXISTS" not in plain_sql and "OPERATIONAL" not in plain_sql
    assert "maintenance_status = 'OPERATIONAL'" in available_sql
    assert "EXISTS" in available_sql
    assert "charging_connectors.status = 'Available'" in available_sql
    assert "charging_connectors.deleted_at IS NULL" in available_sql
    assert "charging_evses.deleted_at IS NULL" in available_sql
    # The subquery is correlated to the outer station row.
    assert "charging_evses.station_id = charging_stations.station_id" in available_sql


class _FirstRowRecorder:
    """Stand-in session whose single query returns no row.

    Attributes:
        statements: Every statement passed to ``execute``, in order.
    """

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> SimpleNamespace:
        self.statements.append(statement)
        return SimpleNamespace(first=lambda: None)


@pytest.mark.asyncio
async def test_nearest_station_lookup_requires_an_available_connector() -> None:
    """F-A2's nearest-station query now also needs a connector reporting Available."""
    recorder = _FirstRowRecorder()

    reference = await charging_stations_service.find_nearest_operational_station(
        recorder,  # type: ignore[arg-type]
        latitude=10.0,
        longitude=106.0,
    )

    # The query point is a geography bind with no literal renderer, so check
    # the SQL shape and the bound values separately.
    compiled = recorder.statements[0].compile(
        dialect=postgresql.dialect()  # type: ignore[no-untyped-call]
    )
    sql = str(compiled)
    assert reference is None
    assert "EXISTS" in sql
    assert "charging_connectors.status = " in sql
    assert "charging_evses.station_id = charging_stations.station_id" in sql
    assert ChargingConnectorStatus.AVAILABLE in compiled.params.values()
    assert ChargingStationMaintenanceStatus.OPERATIONAL in compiled.params.values()


@pytest.mark.asyncio
async def test_station_response_carries_both_connector_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The station detail reports all connectors and the Available ones (F-D1)."""
    station = build_charging_station_record()

    async def get_station(db: AsyncSession, station_id: UUID) -> ChargingStationModel:
        return station

    async def connector_count(db: AsyncSession, station_id: UUID) -> int:
        return 3

    async def available_connector_count(db: AsyncSession, station_id: UUID) -> int:
        return 2

    monkeypatch.setattr(charging_stations_repository, "get_station_by_id", get_station)
    monkeypatch.setattr(
        charging_stations_repository, "count_connectors_by_station_id", connector_count
    )
    monkeypatch.setattr(
        charging_stations_repository,
        "count_available_connectors_by_station_id",
        available_connector_count,
    )

    response = await charging_stations_service.get_charging_station(
        fake_db_session(), station.station_id
    )

    assert (response.connector_count, response.available_connector_count) == (3, 2)


@pytest.mark.asyncio
async def test_available_connector_count_query_counts_only_available_active_rows() -> (
    None
):
    """The count only includes active connectors of active EVSEs reporting Available."""
    recorder = _ScalarRecorder(scalar=4)

    count = await charging_stations_repository.count_available_connectors_by_station_id(
        recorder,  # type: ignore[arg-type]
        uuid4(),
    )

    sql = _compile(recorder.statements[0])
    assert count == 4
    assert "charging_connectors.status = 'Available'" in sql
    assert "charging_connectors.deleted_at IS NULL" in sql
    assert "charging_evses.deleted_at IS NULL" in sql


class _ScalarRecorder:
    """Stand-in session whose queries return one scalar.

    Attributes:
        statements: Every statement passed to ``execute``, in order.
        scalar: The value every query returns.
    """

    def __init__(self, scalar: int) -> None:
        self.statements: list[Any] = []
        self.scalar = scalar

    async def execute(self, statement: Any) -> SimpleNamespace:
        self.statements.append(statement)
        return SimpleNamespace(scalar=lambda: self.scalar)


@pytest.mark.asyncio
async def test_station_status_lists_the_charger_and_every_gun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-C2: connector 0 status on the station plus each gun with its EVSE number."""
    now = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    station = build_charging_station_record()
    station.charger_status = ChargingConnectorStatus.AVAILABLE
    station.charger_status_updated_at = now
    station.charger_error_code = "NoError"
    gun_one = ChargingConnectorModel(
        connector_id=uuid4(),
        evse_id=uuid4(),
        ocpp_connector_id=1,
        status=ChargingConnectorStatus.CHARGING,
        status_updated_at=now,
        error_code="NoError",
        vendor_error_code=None,
        status_info=None,
    )
    gun_two = ChargingConnectorModel(
        connector_id=uuid4(),
        evse_id=uuid4(),
        ocpp_connector_id=1,
        status=ChargingConnectorStatus.FAULTED,
        status_updated_at=now,
        error_code="GroundFailure",
        vendor_error_code="E42",
        status_info="insulation",
    )

    async def get_station(db: AsyncSession, station_id: UUID) -> ChargingStationModel:
        return station

    async def list_connectors(
        db: AsyncSession, station_id: UUID
    ) -> list[tuple[ChargingConnectorModel, int]]:
        return [(gun_one, 1), (gun_two, 2)]

    monkeypatch.setattr(charging_stations_repository, "get_station_by_id", get_station)
    monkeypatch.setattr(
        charging_stations_repository, "list_connectors_by_station_id", list_connectors
    )

    response = await charging_stations_service.get_charging_station_status(
        fake_db_session(), station.station_id
    )

    assert response.charger_status is ChargingConnectorStatus.AVAILABLE
    assert response.charger_error_code == "NoError"
    assert [
        (item.ocpp_evse_id, item.ocpp_connector_id, item.status)
        for item in response.connectors
    ] == [
        (1, 1, ChargingConnectorStatus.CHARGING),
        (2, 1, ChargingConnectorStatus.FAULTED),
    ]
    assert response.connectors[1].vendor_error_code == "E42"
    assert response.connectors[1].status_info == "insulation"


@pytest.mark.asyncio
async def test_station_status_of_an_unknown_station_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown or soft-deleted station is a 404, not an empty board."""

    async def no_station(db: AsyncSession, station_id: UUID) -> None:
        return None

    monkeypatch.setattr(charging_stations_repository, "get_station_by_id", no_station)

    with pytest.raises(ChargingStationNotFoundError):
        await charging_stations_service.get_charging_station_status(
            fake_db_session(), uuid4()
        )


@pytest.mark.asyncio
async def test_station_energy_totals_rank_every_station_highest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-C5: every active station is listed (zero included), highest energy first."""
    quiet = build_charging_station_record()
    quiet.display_name = "A quiet"
    busy = build_charging_station_record()
    busy.display_name = "B busy"
    medium = build_charging_station_record()
    medium.display_name = "C medium"
    energy_by_station = {
        quiet.station_id: (Decimal(0), 0),
        busy.station_id: (Decimal("42000"), 3),
        medium.station_id: (Decimal("1500"), 1),
    }
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 10, 1, tzinfo=timezone.utc)

    async def list_stations(db: AsyncSession) -> list[ChargingStationModel]:
        return [quiet, busy, medium]

    async def energy_total(
        db: AsyncSession,
        *,
        station_id: UUID,
        start_time: datetime,
        end_time: datetime,
    ) -> StationEnergyTotal:
        assert (start_time, end_time) == (start, end)
        total_wh, count = energy_by_station[station_id]
        return StationEnergyTotal(
            station_id=station_id, total_energy_wh=total_wh, session_count=count
        )

    monkeypatch.setattr(
        charging_stations_repository, "list_active_stations", list_stations
    )
    monkeypatch.setattr(
        charging_sessions_service, "resolve_station_energy_total", energy_total
    )

    response = await charging_stations_service.list_station_energy_totals(
        fake_db_session(), start_time=start, end_time=end
    )

    assert [item.display_name for item in response.items] == [
        "B busy",
        "C medium",
        "A quiet",
    ]
    assert [item.total_energy_kwh for item in response.items] == [42.0, 1.5, 0.0]
    assert response.total_energy_kwh == pytest.approx(43.5)
    assert response.session_count == 4


@pytest.mark.asyncio
async def test_station_energy_totals_reject_a_naive_or_backward_window() -> None:
    """The window is validated even when there is no station to aggregate."""
    aware = datetime(2026, 9, 1, tzinfo=timezone.utc)

    with pytest.raises(ChargingStationReportRangeError, match="start_time"):
        await charging_stations_service.list_station_energy_totals(
            fake_db_session(), start_time=datetime(2026, 9, 1), end_time=aware
        )
    with pytest.raises(ChargingStationReportRangeError):
        await charging_stations_service.list_station_energy_totals(
            fake_db_session(), start_time=aware, end_time=aware
        )


@pytest.mark.asyncio
async def test_update_connector_status_raises_not_found_for_inactive_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """update_connector_status() raises when the repository finds no active connector."""

    async def no_update(db: AsyncSession, connector_id: UUID, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(ocpp_state_repository, "update_connector_status", no_update)

    with pytest.raises(ChargingConnectorNotFoundError):
        await ocpp_state_service.update_connector_status(
            fake_db_session(),
            connector_id=uuid4(),
            status=ChargingConnectorStatus.OCCUPIED,
            status_updated_at=datetime.now(timezone.utc),
        )


class _StatementRecorder:
    """Stand-in session that records each executed statement.

    Attributes:
        statements: Every statement passed to ``execute``, in order.
        rowcount: Row count reported for every statement.
    """

    def __init__(self, rowcount: int) -> None:
        self.statements: list[Any] = []
        self.rowcount = rowcount

    async def execute(self, statement: Any) -> SimpleNamespace:
        self.statements.append(statement)
        return SimpleNamespace(rowcount=self.rowcount)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_connector_status_update_keeps_updated_at_as_the_last_admin_edit() -> (
    None
):
    """A device-reported status sets updated_at to itself, never to "now" (F-C2)."""
    recorder = _StatementRecorder(rowcount=1)

    is_updated = await ocpp_state_repository.update_connector_status(
        recorder,  # type: ignore[arg-type]
        uuid4(),
        status=ChargingConnectorStatus.CHARGING,
        status_updated_at=datetime.now(timezone.utc),
    )

    sql = str(
        recorder.statements[0].compile(
            dialect=postgresql.dialect()  # type: ignore[no-untyped-call]
        )
    )
    assert is_updated is True
    assert "updated_at=charging_connectors.updated_at" in sql
    assert "charging_connectors.deleted_at IS NULL" in sql


@pytest.mark.asyncio
async def test_connector_status_update_reports_no_row_for_an_inactive_connector() -> (
    None
):
    """No matching active row means False, which the service turns into 404-style."""
    recorder = _StatementRecorder(rowcount=0)

    is_updated = await ocpp_state_repository.update_connector_status(
        recorder,  # type: ignore[arg-type]
        uuid4(),
        status=ChargingConnectorStatus.AVAILABLE,
        status_updated_at=datetime.now(timezone.utc),
    )

    assert is_updated is False
