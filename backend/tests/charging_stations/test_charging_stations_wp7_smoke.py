"""Smoke tests of the charging network features completed in WP7.

Covers the list visibility and grant expiry (STN-01, STN-12), the delete rule
(STN-02), the connection read and the message-log redaction (STN-03), the
station status counts and the stale flag (STN-04), and the command checks and
cancel (STN-10).
"""

from datetime import date, datetime, timedelta, timezone
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
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
from app.domains.charging_stations.exceptions import (
    ChargingStationCommandConflictError,
    ChargingStationCommandInputError,
    ChargingStationOfflineError,
    ChargingTopologyConflictError,
)
from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingConnectorStateModel,
    ChargingLocationModel,
)
from app.domains.charging_stations.schemas import (
    ChargingStationCommandCreateRequest,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    LocationViewer,
    StationCommandOutcome,
    StationCommandType,
    StationListScope,
)
from app.domains.identity.exceptions import AccessDeniedError
from app.libs.common.clock import utc_now
from tests.builders import (
    build_charging_location_record,
    build_charging_station_record,
    build_charging_station_state_record,
    fake_db_session,
)
from tests.principals import (
    DEFAULT_ORGANIZATION_ID,
    build_internal_principal,
    build_principal,
)


def _sql(conditions: list[Any]) -> str:
    """Compile list conditions to PostgreSQL text so a bad clause fails the test."""
    statement = select(ChargingLocationModel.location_id).where(*conditions)
    return str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]


def test_location_list_conditions_follow_the_scope_and_filters() -> None:
    """STN-01/12: manage scope filters by owner; view scope adds public and granted."""
    managed = _sql(
        charging_stations_repository._location_list_conditions(
            organization_id=DEFAULT_ORGANIZATION_ID,
            viewer=None,
            owner_organization_id=None,
            is_public=None,
            status=None,
            search_text=None,
        )
    )
    visible = _sql(
        charging_stations_repository._location_list_conditions(
            organization_id=None,
            viewer=LocationViewer(organization_id=DEFAULT_ORGANIZATION_ID),
            owner_organization_id=None,
            is_public=False,
            status="ACTIVE",
            search_text="mine_%",
        )
    )
    everything = _sql(
        charging_stations_repository._location_list_conditions(
            organization_id=None,
            viewer=LocationViewer(sees_all=True),
            owner_organization_id=None,
            is_public=None,
            status=None,
            search_text=None,
        )
    )

    assert "charging_location_access" not in managed
    assert "charging_location_access" in visible
    assert "valid_until" in visible
    assert "ILIKE" in visible.upper()
    assert "charging_location_access" not in everything
    assert "organization_id" not in everything


@pytest.mark.asyncio
async def test_list_locations_passes_the_manage_or_the_view_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scope picks the repository reach: manage by owner, view by the viewer."""
    seen: list[dict[str, Any]] = []

    async def list_locations(db: AsyncSession, **kwargs: Any) -> list[Any]:
        seen.append(kwargs)
        return []

    async def count_locations(db: AsyncSession, **kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(
        charging_stations_repository, "list_charging_locations", list_locations
    )
    monkeypatch.setattr(
        charging_stations_repository, "count_locations", count_locations
    )
    customer = build_principal()

    await charging_stations_service.list_charging_locations(
        fake_db_session(), principal=customer
    )
    await charging_stations_service.list_charging_locations(
        fake_db_session(),
        principal=customer,
        scope=StationListScope.VISIBLE,
        search_text=" depot ",
    )

    assert seen[0]["organization_id"] == DEFAULT_ORGANIZATION_ID
    assert seen[0]["viewer"] is None
    assert seen[1]["organization_id"] is None
    assert seen[1]["viewer"] == LocationViewer(organization_id=DEFAULT_ORGANIZATION_ID)
    assert seen[1]["search_text"] == "depot"


@pytest.mark.asyncio
async def test_an_expired_grant_no_longer_opens_a_private_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CS-13: a grant past its last valid day gives no view, one ending today does."""
    today = charging_stations_service._today_in_report_zone()
    grants = {"expired": today - timedelta(days=1), "last_day": today}

    async def get_grant(
        db: AsyncSession, location_id: UUID, organization_id: UUID
    ) -> Any:
        return SimpleNamespace(valid_until=grants[current])

    monkeypatch.setattr(
        charging_stations_repository, "get_live_location_access", get_grant
    )
    private = build_charging_location_record(organization_id=uuid4())
    private.is_public = False

    results = {}
    for current in grants:
        results[current] = await charging_stations_service._can_reach_location(
            fake_db_session(), private, build_principal(), view=True
        )

    assert results == {"expired": False, "last_day": True}
    assert isinstance(today, date)


@pytest.mark.asyncio
async def test_deleting_a_connector_with_a_running_charge_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STN-02: a gun cannot leave the system while a session is charging on it."""
    connector = ChargingConnectorModel(connector_id=uuid4(), evse_id=uuid4())

    async def get_connector(db: AsyncSession, connector_id: UUID) -> Any:
        return connector

    async def always_in_reach(*args: Any, **kwargs: Any) -> Any:
        return object()

    async def has_active(db: AsyncSession, connector_id: UUID) -> bool:
        return True

    monkeypatch.setattr(
        charging_stations_repository, "get_connector_by_id", get_connector
    )
    monkeypatch.setattr(
        charging_stations_service, "_get_evse_in_reach", always_in_reach
    )
    monkeypatch.setattr(
        charging_sessions_service, "has_active_session_on_connector", has_active
    )

    with pytest.raises(ChargingTopologyConflictError):
        await charging_stations_service.soft_delete_charging_connector(
            fake_db_session(), connector.connector_id, principal=build_principal()
        )


def _station_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: Any,
    connectors: list[Any] | None = None,
) -> Any:
    """Make the repository answer for one charger without a database."""
    station = build_charging_station_record()
    location = build_charging_location_record()

    async def get_station(db: AsyncSession, station_id: UUID) -> Any:
        return station

    async def get_location(db: AsyncSession, location_id: UUID, **_: Any) -> Any:
        return location

    async def get_state(db: AsyncSession, station_id: UUID) -> Any:
        return state

    async def list_connectors(db: AsyncSession, station_id: UUID) -> list[Any]:
        return connectors or []

    monkeypatch.setattr(charging_stations_repository, "get_station_by_id", get_station)
    monkeypatch.setattr(
        charging_stations_repository, "get_location_by_id", get_location
    )
    monkeypatch.setattr(charging_stations_repository, "get_station_state", get_state)
    monkeypatch.setattr(
        charging_stations_repository, "list_connectors_by_station_id", list_connectors
    )
    return station


@pytest.mark.asyncio
async def test_connection_read_derives_online_and_compares_the_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STN-03: last_seen decides is_online; a different reported serial is flagged."""
    state = build_charging_station_state_record(last_seen_at=utc_now())
    state.serial_number = "OTHER-UNIT"
    state.ocpp_protocol_version = "ocpp1.6"
    station = _station_fixture(monkeypatch, state=state)

    response = await charging_stations_service.get_charging_station_connection(
        fake_db_session(), station.station_id, principal=build_internal_principal()
    )

    assert response.is_online is True
    assert response.ocpp_protocol_version == "ocpp1.6"
    assert response.registered_serial_number == station.registered_serial_number
    assert response.is_serial_matching is False


@pytest.mark.asyncio
async def test_message_log_is_for_staff_and_hides_the_frame_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STN-03/15: a customer is refused; staff get no frame text unless they ask."""
    station = _station_fixture(monkeypatch, state=None)
    message = SimpleNamespace(
        message_id=uuid4(),
        occurred_at=utc_now(),
        direction="CP_TO_CSMS",
        ocpp_subprotocol="ocpp1.6",
        action="Authorize",
        ocpp_message_id="m1",
        raw_frame='[2,"m1","Authorize",{"idTag":"SECRET"}]',
    )

    async def list_messages(db: AsyncSession, **kwargs: Any) -> list[Any]:
        return [message]

    async def count_messages(db: AsyncSession, **kwargs: Any) -> int:
        return 1

    monkeypatch.setattr(ocpp_state_repository, "list_ocpp_messages", list_messages)
    monkeypatch.setattr(ocpp_state_repository, "count_ocpp_messages", count_messages)

    with pytest.raises(AccessDeniedError):
        await charging_stations_service.list_charging_station_ocpp_messages(
            fake_db_session(), station.station_id, principal=build_principal()
        )
    staff = build_internal_principal()
    hidden = await charging_stations_service.list_charging_station_ocpp_messages(
        fake_db_session(), station.station_id, principal=staff
    )
    shown = await charging_stations_service.list_charging_station_ocpp_messages(
        fake_db_session(), station.station_id, principal=staff, include_raw_frame=True
    )

    assert hidden.items[0].raw_frame is None
    assert hidden.items[0].action == "Authorize"
    assert shown.items[0].raw_frame is not None


@pytest.mark.asyncio
async def test_status_counts_guns_and_flags_them_stale_when_the_charger_is_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STN-04: counts per status are derived; an offline charger's guns are stale."""
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    guns: list[Any] = []
    for status in (ChargingConnectorStatus.AVAILABLE.value, None):
        gun = ChargingConnectorModel(
            connector_id=uuid4(),
            evse_id=uuid4(),
            ocpp_connector_id=1,
            standard="IEC_62196_T2_COMBO",
            max_power_kw=Decimal("60.00"),
        )
        gun_state = ChargingConnectorStateModel(
            connector_id=gun.connector_id, status=status
        )
        guns.append((gun, len(guns) + 1, gun_state))
    station = _station_fixture(
        monkeypatch,
        state=build_charging_station_state_record(last_seen_at=old),
        connectors=guns,
    )

    response = await charging_stations_service.get_charging_station_status(
        fake_db_session(), station.station_id, principal=build_internal_principal()
    )

    assert response.is_online is False
    assert response.connector_count == 2
    assert response.available_connector_count == 1
    assert response.status_counts == {"Available": 1, "Unknown": 1}
    assert all(item.is_status_stale for item in response.connectors)


@pytest.mark.parametrize(
    ("command_type", "parameters"),
    [
        (StationCommandType.RESET, {"reset_type": "Explode"}),
        (StationCommandType.RESET, {"unexpected": 1}),
        (StationCommandType.CHANGE_AVAILABILITY, {"availability": "Maybe"}),
        (StationCommandType.CHANGE_CONFIGURATION, {"key": "HeartbeatInterval"}),
        (StationCommandType.TRIGGER_MESSAGE, {"requested_message": "Nope"}),
        (StationCommandType.REMOTE_STOP, {"id_token": "x"}),
    ],
)
def test_command_parameters_are_checked_per_type(
    command_type: StationCommandType, parameters: dict[str, object]
) -> None:
    """STN-10: an unknown key or a value outside the type's range is a 400."""
    with pytest.raises(ChargingStationCommandInputError):
        charging_stations_service._validate_command_parameters(
            command_type, evse_id=uuid4(), session_id=uuid4(), parameters=parameters
        )


def test_command_parameters_get_their_defaults() -> None:
    """A reset without a type is a soft reset; a plain remote stop stores nothing."""
    reset = charging_stations_service._validate_command_parameters(
        StationCommandType.RESET, evse_id=None, session_id=None, parameters=None
    )
    stop = charging_stations_service._validate_command_parameters(
        StationCommandType.REMOTE_STOP,
        evse_id=None,
        session_id=uuid4(),
        parameters=None,
    )

    assert reset == {"reset_type": "Soft"}
    assert stop is None


@pytest.mark.asyncio
async def test_a_manual_command_needs_a_reason_and_a_connected_charger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STN-10: no reason is a 400, a charger that is not connected is a 409."""
    offline = _station_fixture(monkeypatch, state=None)
    principal = build_internal_principal()

    with pytest.raises(ChargingStationCommandInputError):
        await charging_stations_service.create_charging_station_command(
            fake_db_session(),
            offline.station_id,
            ChargingStationCommandCreateRequest(
                command_type=StationCommandType.RESET, reason=None
            ),
            principal=principal,
        )
    with pytest.raises(ChargingStationOfflineError):
        await charging_stations_service.create_charging_station_command(
            fake_db_session(),
            offline.station_id,
            ChargingStationCommandCreateRequest(
                command_type=StationCommandType.RESET, reason="Hung charger"
            ),
            principal=principal,
        )


@pytest.mark.asyncio
async def test_only_a_command_still_in_the_queue_can_be_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STN-10: the conditional update decides; a sent command answers 409."""
    station = _station_fixture(monkeypatch, state=None)
    command = SimpleNamespace(
        command_id=uuid4(),
        station_id=station.station_id,
        evse_id=None,
        session_id=None,
        command_type="RESET",
        parameters=None,
        requested_by=None,
        reason="Hung charger",
        requested_at=utc_now(),
        ocpp_message_id=None,
        outcome=StationCommandOutcome.PENDING.value,
        response_status=None,
        answered_at=None,
    )
    can_cancel = {"value": True}

    async def get_command(db: AsyncSession, command_id: UUID) -> Any:
        return command

    async def cancel(db: AsyncSession, command_id: UUID, **kwargs: Any) -> bool:
        if can_cancel["value"]:
            command.outcome = StationCommandOutcome.NOT_SENT.value
            command.response_status = kwargs["response_status"]
        return can_cancel["value"]

    monkeypatch.setattr(ocpp_state_repository, "get_station_command_by_id", get_command)
    monkeypatch.setattr(ocpp_state_repository, "cancel_queued_command", cancel)
    session = fake_db_session()
    principal = build_internal_principal()

    cancelled = await charging_stations_service.cancel_charging_station_command(
        session, station.station_id, command.command_id, principal=principal
    )
    can_cancel["value"] = False
    with pytest.raises(ChargingStationCommandConflictError):
        await charging_stations_service.cancel_charging_station_command(
            session, station.station_id, command.command_id, principal=principal
        )

    assert cancelled.outcome is StationCommandOutcome.NOT_SENT
    assert cancelled.response_status == "Cancelled"
