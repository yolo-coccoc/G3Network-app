"""Smoke tests for the charging_stations service: nearby search and connector status (F-D1, F-C2)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
from app.domains.charging_stations.exceptions import ChargingConnectorNotFoundError
from app.domains.charging_stations.models import ChargingStationModel
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
)
from app.libs.common.config import settings
from tests.builders import build_charging_station_record, fake_db_session


def test_to_nearby_charging_station_response_decodes_location_and_distance() -> None:
    """to_nearby_charging_station_response() decodes lat/lon and carries distance_km (F-D1)."""
    station = build_charging_station_record()

    response = charging_stations_service.to_nearby_charging_station_response(
        station, connector_count=4, distance_km=2.5
    )

    assert response.latitude == pytest.approx(10.762622)
    assert response.longitude == pytest.approx(106.660172)
    assert response.connector_count == 4
    assert response.distance_km == pytest.approx(2.5)
    assert response.power_rating_kw == pytest.approx(120.0)


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

    monkeypatch.setattr(
        charging_stations_repository, "list_nearby_stations", list_nearby
    )
    monkeypatch.setattr(
        charging_stations_repository, "count_nearby_stations", count_nearby
    )
    monkeypatch.setattr(
        charging_stations_repository, "count_connectors_by_station_id", connector_count
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
    assert response.page == settings.API_DEFAULT_PAGE
    # normalize_page_window floors a non-positive page size at 1.
    assert response.page_size == 1
    assert len(response.items) == 1
    assert response.items[0].distance_km == pytest.approx(1.5)
    assert response.items[0].connector_count == 2


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
