"""Review smoke tests of the charging-network service and request schemas.

Written by the 2026-10-10 security and correctness review. Guard tests pin
behaviour the review verified (organization scoping of commands, the cancel
rule, the unlock rule). One test per finding asserts the **correct** behaviour
and is marked ``xfail(strict=True)`` while the defect exists, with the review
ID (``CS-n``) in the reason, so the fix forces the marker off.

No database is used: the repositories are replaced with fakes that answer for
one charger and record the writes.
"""

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_session_repository
import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
from app.domains.charging_sessions.types import SessionStatus
from app.domains.charging_stations.exceptions import (
    ChargingStationCommandConflictError,
    ChargingStationCommandInputError,
    ChargingStationNotFoundError,
)
from app.domains.charging_stations.schemas import (
    ChargingConnectorCreateRequest,
    ChargingEvseCreateRequest,
    ChargingStationCommandCreateRequest,
)
from app.domains.charging_stations.types import (
    ConnectorStandard,
    StationCommandOutcome,
    StationCommandType,
)
from app.domains.identity.types import UserRole
from app.libs.common.clock import utc_now
from app.libs.common.errors import ConflictError, InvalidInputError, NotFoundError
from tests.builders import (
    build_charging_location_record,
    build_charging_session,
    build_charging_station_record,
    build_charging_station_state_record,
    fake_db_session,
)
from tests.principals import (
    DEFAULT_ORGANIZATION_ID,
    OTHER_ORGANIZATION_ID,
    build_internal_principal,
    build_principal,
)


def _command_row(station_id: UUID, **overrides: Any) -> SimpleNamespace:
    """Build a command row as the repository returns it.

    Args:
        station_id: The charger the command belongs to.
        **overrides: Columns to change.

    Returns:
        The row.
    """
    values: dict[str, Any] = {
        "command_id": uuid4(),
        "station_id": station_id,
        "evse_id": None,
        "session_id": None,
        "command_type": StationCommandType.RESET.value,
        "parameters": None,
        "requested_by": None,
        "reason": "Hung charger",
        "requested_at": utc_now(),
        "ocpp_message_id": None,
        "outcome": StationCommandOutcome.PENDING.value,
        "response_status": None,
        "answered_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _charger(
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner_organization_id: UUID = DEFAULT_ORGANIZATION_ID,
) -> tuple[Any, list[dict[str, Any]]]:
    """Make the repositories answer for one connected charger and record inserts.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        owner_organization_id: The organization owning the charger's location.

    Returns:
        ``(station, inserted)``: the charger and the commands queued for it.
    """
    location = build_charging_location_record(organization_id=owner_organization_id)
    location.is_public = True
    station = build_charging_station_record(location_id=location.location_id)
    state = build_charging_station_state_record(
        station_id=station.station_id, last_seen_at=utc_now()
    )
    inserted: list[dict[str, Any]] = []

    async def get_station(db: AsyncSession, station_id: UUID) -> Any:
        return station if station_id == station.station_id else None

    async def get_location(db: AsyncSession, location_id: UUID, **_: Any) -> Any:
        return location if location_id == location.location_id else None

    async def get_state(db: AsyncSession, station_id: UUID) -> Any:
        return state

    async def insert_command(db: AsyncSession, **kwargs: Any) -> Any:
        columns = {key: value for key, value in kwargs.items() if key != "station_id"}
        row = _command_row(kwargs["station_id"], **columns)
        inserted.append({"row": row, **kwargs})
        return row

    async def get_command(db: AsyncSession, command_id: UUID) -> Any:
        for entry in inserted:
            if entry["row"].command_id == command_id:
                return entry["row"]
        return None

    monkeypatch.setattr(charging_stations_repository, "get_station_by_id", get_station)
    monkeypatch.setattr(
        charging_stations_repository, "get_location_by_id", get_location
    )
    monkeypatch.setattr(charging_stations_repository, "get_station_state", get_state)
    monkeypatch.setattr(ocpp_state_repository, "insert_station_command", insert_command)
    monkeypatch.setattr(ocpp_state_repository, "get_station_command_by_id", get_command)
    return station, inserted


# --- guards ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_of_one_organization_cannot_command_another_ones_charger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public charger of org B is visible to org A, yet A's command is a 404."""
    station, inserted = _charger(
        monkeypatch, owner_organization_id=OTHER_ORGANIZATION_ID
    )
    principal = build_principal(roles=frozenset({UserRole.ORG_ADMIN}))

    with pytest.raises(ChargingStationNotFoundError):
        await charging_stations_service.create_charging_station_command(
            fake_db_session(),
            station.station_id,
            ChargingStationCommandCreateRequest(
                command_type=StationCommandType.RESET, reason="Not mine"
            ),
            principal=principal,
        )

    assert inserted == []


@pytest.mark.asyncio
async def test_customer_cannot_read_or_cancel_another_organizations_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading or cancelling a command of another organization's charger is a 404."""
    station, _ = _charger(monkeypatch, owner_organization_id=OTHER_ORGANIZATION_ID)
    cancels: list[UUID] = []

    async def cancel(db: AsyncSession, command_id: UUID, **_: Any) -> bool:
        cancels.append(command_id)
        return True

    monkeypatch.setattr(ocpp_state_repository, "cancel_queued_command", cancel)

    with pytest.raises(ChargingStationNotFoundError):
        await charging_stations_service.cancel_charging_station_command(
            fake_db_session(),
            station.station_id,
            uuid4(),
            principal=build_principal(),
        )

    assert cancels == []


@pytest.mark.asyncio
async def test_cancel_of_a_command_no_longer_queued_is_refused_with_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command already claimed or closed cannot be cancelled."""
    station, _ = _charger(monkeypatch)
    claimed = _command_row(station.station_id, ocpp_message_id="msg-1")

    async def get_command(db: AsyncSession, command_id: UUID) -> Any:
        return claimed

    async def cancel(db: AsyncSession, command_id: UUID, **_: Any) -> bool:
        return False

    monkeypatch.setattr(ocpp_state_repository, "get_station_command_by_id", get_command)
    monkeypatch.setattr(ocpp_state_repository, "cancel_queued_command", cancel)

    with pytest.raises(ChargingStationCommandConflictError):
        await charging_stations_service.cancel_charging_station_command(
            fake_db_session(),
            station.station_id,
            claimed.command_id,
            principal=build_principal(),
        )


@pytest.mark.asyncio
async def test_cancel_of_a_command_of_another_charger_is_a_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command ID of charger X addressed through charger Y does not exist."""
    station, _ = _charger(monkeypatch)
    foreign = _command_row(uuid4())

    async def get_command(db: AsyncSession, command_id: UUID) -> Any:
        return foreign

    monkeypatch.setattr(ocpp_state_repository, "get_station_command_by_id", get_command)

    with pytest.raises(ChargingStationNotFoundError):
        await charging_stations_service.cancel_charging_station_command(
            fake_db_session(),
            station.station_id,
            foreign.command_id,
            principal=build_principal(),
        )


@pytest.mark.asyncio
async def test_unlock_while_a_charge_runs_on_that_gun_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNLOCK_CONNECTOR is a 409 while the gun has an ACTIVE session."""
    station, inserted = _charger(monkeypatch)
    evse = SimpleNamespace(evse_id=uuid4(), station_id=station.station_id)

    async def get_evse(db: AsyncSession, evse_id: UUID) -> Any:
        return evse

    async def list_connectors(db: AsyncSession, **_: Any) -> list[Any]:
        return [SimpleNamespace(connector_id=uuid4())]

    async def has_active(db: AsyncSession, connector_id: UUID) -> bool:
        return True

    monkeypatch.setattr(charging_stations_repository, "get_evse_by_id", get_evse)
    monkeypatch.setattr(
        charging_stations_repository, "list_charging_connectors", list_connectors
    )
    monkeypatch.setattr(
        charging_sessions_service, "has_active_session_on_connector", has_active
    )

    with pytest.raises(ChargingStationCommandConflictError):
        await charging_stations_service.create_charging_station_command(
            fake_db_session(),
            station.station_id,
            ChargingStationCommandCreateRequest(
                command_type=StationCommandType.UNLOCK_CONNECTOR,
                evse_id=evse.evse_id,
                reason="Cable stuck",
            ),
            principal=build_internal_principal(),
        )

    assert inserted == []


# --- RV-CS8: a command may name another charger's session -------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-CS8: a command accepts a session_id of another charger",
)
@pytest.mark.parametrize(
    ("command_type", "session_status"),
    [
        (StationCommandType.REMOTE_START, SessionStatus.PENDING),
        (StationCommandType.REMOTE_STOP, SessionStatus.ACTIVE),
    ],
)
async def test_command_refuses_a_session_of_another_charger(
    monkeypatch: pytest.MonkeyPatch,
    command_type: StationCommandType,
    session_status: SessionStatus,
) -> None:
    """Naming another charger's session is refused and nothing is queued."""
    station, inserted = _charger(monkeypatch)
    foreign_session = build_charging_session(status=session_status)
    assert foreign_session.station_id != station.station_id

    async def get_session(db: AsyncSession, session_id: UUID) -> Any:
        return foreign_session if session_id == foreign_session.session_id else None

    monkeypatch.setattr(charging_session_repository, "get_session_by_id", get_session)

    with pytest.raises((InvalidInputError, NotFoundError, ConflictError)):
        await charging_stations_service.create_charging_station_command(
            fake_db_session(),
            station.station_id,
            ChargingStationCommandCreateRequest(
                command_type=command_type,
                session_id=foreign_session.session_id,
                reason="Operator action",
            ),
            principal=build_principal(),
        )

    assert inserted == []


# --- RV-CS11: the manual remote start token ---------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="RV-CS11: REMOTE_START id_token allows 36 chars, 1.6J idTag max 20",
)
def test_manual_remote_start_token_longer_than_a_1_6_id_tag_is_refused() -> None:
    """A token the 1.6J schema would reject is refused when the command is queued."""
    with pytest.raises(ChargingStationCommandInputError):
        charging_stations_service._validate_command_parameters(
            StationCommandType.REMOTE_START,
            evse_id=None,
            session_id=None,
            parameters={"id_token": "T" * 21},
        )


# --- RV-CS12: unbounded integers into Integer columns -----------------------------


@pytest.mark.xfail(
    strict=True,
    reason="RV-CS12: ocpp_evse_id above int32 passes validation (500)",
)
def test_evse_number_beyond_the_integer_column_is_refused_by_the_schema() -> None:
    """An EVSE number the Integer column cannot hold is a 422, not a 500."""
    with pytest.raises(ValidationError):
        ChargingEvseCreateRequest(ocpp_evse_id=2**31, emi3_evse_id="VN*G3N*E0001")


@pytest.mark.xfail(
    strict=True,
    reason="RV-CS12: connector numbers and ratings above int32 pass validation",
)
@pytest.mark.parametrize(
    "field_name", ["ocpp_connector_id", "max_voltage_v", "max_current_a"]
)
def test_connector_integers_beyond_the_integer_column_are_refused(
    field_name: str,
) -> None:
    """Each integer field of a gun is bounded to what its column stores."""
    values: dict[str, Any] = {
        "ocpp_connector_id": 1,
        "standard": ConnectorStandard.IEC_62196_T2_COMBO,
        "max_power_kw": 120,
        "max_voltage_v": 1000,
        "max_current_a": 250,
    }
    values[field_name] = 2**31

    with pytest.raises(ValidationError):
        ChargingConnectorCreateRequest(**values)
