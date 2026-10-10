"""Smoke tests for the QR charging flow: scan, stop, receipt, wallet minimum, sweep (WP8)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

import app.api.charging_session_flow as flow
import app.domains.billing.repository as billing_repository
import app.domains.billing.service as billing_service
import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
import app.domains.charging_stations.ocpp.command_loop as command_loop
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.repository as stations_repository
import app.domains.charging_stations.service as stations_service
import app.domains.drivers.service as driver_service
from app.domains.billing.exceptions import InsufficientBalanceError, WalletBlockedError
from app.domains.billing.types import TariffQuote
from app.domains.charging_sessions.exceptions import (
    ChargingConnectorBusyError,
    ChargingSessionAlreadyOpenError,
    ChargingSessionStateError,
    ChargingSessionStopDeniedError,
)
from app.domains.charging_sessions.schemas import (
    ChargingSessionResponse,
    ChargingSessionScanRequest,
    ChargingSessionStopRequest,
)
from app.domains.charging_sessions.types import PendingSessionReference, SessionStatus
from app.domains.charging_stations.exceptions import (
    ChargingLocationAccessDeniedError,
    ChargingStationNotFoundError,
    ChargingStationOfflineError,
    ChargingStationUnavailableError,
)
from app.domains.charging_stations.types import (
    ScanTargetReference,
    SessionPlaceReference,
    StationCommandOutcome,
    StationCommandReference,
    StationCommandType,
)
from app.domains.identity.types import UserRole
from app.libs.common.config import settings
from tests.builders import (
    build_charging_location_record,
    build_charging_session,
    build_charging_station_record,
    build_charging_station_state_record,
    fake_db_session,
)
from tests.fakes import FakeSessionFactory
from tests.principals import build_internal_principal, build_principal

STATION_ID, EVSE_ID, CONNECTOR_ID = uuid4(), uuid4(), uuid4()
DRIVER = build_principal(roles=frozenset({UserRole.DRIVER}))
TARIFF_VERSION_ID = uuid4()


# --- Wallet minimum (BL-14) --------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("minimum", "balance", "expected"),
    [
        (0, None, True),  # no wallet counts as zero, which meets a 0 minimum
        (0, Decimal(0), True),
        (0, Decimal(-1), False),  # a negative wallet must be topped up (BL-25)
        (50_000, None, False),  # no wallet counts as a zero balance
        (50_000, Decimal(49_999), False),
        (50_000, Decimal(50_000), True),
    ],
)
async def test_has_minimum_balance_follows_the_setting(
    monkeypatch: pytest.MonkeyPatch,
    minimum: int,
    balance: Decimal | None,
    expected: bool,
) -> None:
    """The rule reads BILLING_MIN_BALANCE_VND, never below zero (BL-25); no wallet
    is created."""

    async def find_wallet(db: object, user_id: UUID) -> Any:
        return (
            None
            if balance is None
            else SimpleNamespace(balance=balance, status="ACTIVE")
        )

    monkeypatch.setattr(settings, "BILLING_MIN_BALANCE_VND", Decimal(minimum))
    monkeypatch.setattr(billing_repository, "find_wallet_by_user_id", find_wallet)

    assert await billing_service.has_minimum_balance(fake_db_session(), uuid4()) is (
        expected
    )


# --- Scan target (charging_stations) ----------------------------------------


def _patch_target(
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_public: bool = True,
    station_status: str = "ACTIVE",
    is_online: bool = True,
    known: bool = True,
) -> None:
    location = build_charging_location_record()
    location.is_public = is_public
    station = build_charging_station_record(
        station_id=STATION_ID, location_id=location.location_id
    )
    station.status = station_status
    evse = SimpleNamespace(evse_id=EVSE_ID, status="ACTIVE", ocpp_evse_id=2)
    plug = SimpleNamespace(connector_id=CONNECTOR_ID)

    async def by_identity(db: object, code: str, **_: object) -> Any:
        return station if known and code == "OCPP-TEST-001" else None

    async def by_serial(db: object, code: str) -> Any:
        return None

    async def get_location(db: object, location_id: UUID, **_: object) -> Any:
        return location

    async def get_state(db: object, station_id: UUID) -> Any:
        return build_charging_station_state_record(
            station_id=station_id,
            last_seen_at=datetime.now(timezone.utc) if is_online else None,
        )

    async def get_evse(db: object, station_id: UUID, number: int, **_: object) -> Any:
        return evse if number == 2 else None

    async def list_plugs(db: object, **_: object) -> list[Any]:
        return [plug]

    async def list_by_station(db: object, station_id: UUID) -> list[Any]:
        return [(plug, 2, None)]

    async def no_grant(db: object, *_: object) -> None:
        return None

    monkeypatch.setattr(stations_repository, "get_station_by_identity", by_identity)
    monkeypatch.setattr(
        stations_repository, "find_live_station_by_registered_serial", by_serial
    )
    monkeypatch.setattr(stations_repository, "get_location_by_id", get_location)
    monkeypatch.setattr(stations_repository, "get_station_state", get_state)
    monkeypatch.setattr(stations_repository, "get_evse_by_identity", get_evse)
    monkeypatch.setattr(stations_repository, "list_charging_connectors", list_plugs)
    monkeypatch.setattr(
        stations_repository, "list_connectors_by_station_id", list_by_station
    )
    monkeypatch.setattr(stations_repository, "get_live_location_access", no_grant)


@pytest.mark.asyncio
async def test_resolve_scan_target_returns_the_charger_and_the_named_gun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public, active, connected charger resolves; a gun number picks its plug."""
    _patch_target(monkeypatch)

    target = await stations_service.resolve_scan_target(
        fake_db_session(),
        charger_code="OCPP-TEST-001",
        gun_number=2,
        principal=DRIVER,
    )

    assert (target.station_id, target.evse_id, target.connector_id) == (
        STATION_ID,
        EVSE_ID,
        CONNECTOR_ID,
    )
    assert target.connector_ids == (CONNECTOR_ID,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "gun_number", "error"),
    [
        ({"known": False}, None, ChargingStationNotFoundError),
        ({"is_public": False}, None, ChargingLocationAccessDeniedError),
        ({"station_status": "INACTIVE"}, None, ChargingStationUnavailableError),
        ({"is_online": False}, None, ChargingStationOfflineError),
        ({}, 9, ChargingStationNotFoundError),
    ],
)
async def test_resolve_scan_target_refuses_what_cannot_start_a_charge(
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, Any],
    gun_number: int | None,
    error: type[Exception],
) -> None:
    """Unknown, private, out-of-service, offline charger or unknown gun is refused."""
    _patch_target(monkeypatch, **options)

    with pytest.raises(error):
        await stations_service.resolve_scan_target(
            fake_db_session(),
            charger_code="OCPP-TEST-001",
            gun_number=gun_number,
            principal=DRIVER,
        )


# --- The scan endpoint -------------------------------------------------------


def _patch_scan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    gun: bool = True,
    busy_connectors: frozenset[UUID] = frozenset(),
    has_open_session: bool = False,
    is_blocked: bool = False,
    has_minimum: bool = True,
) -> dict[str, Any]:
    record: dict[str, Any] = {}
    other_connector = uuid4()

    async def resolve_target(db: object, **kwargs: Any) -> ScanTargetReference:
        return ScanTargetReference(
            station_id=STATION_ID,
            location_id=uuid4(),
            location_name="Depot",
            evse_id=EVSE_ID if gun else None,
            connector_id=CONNECTOR_ID if gun else None,
            gun_number=1 if gun else None,
            connector_ids=(CONNECTOR_ID, other_connector),
        )

    async def has_active(db: object, connector_id: UUID) -> bool:
        return connector_id in busy_connectors

    async def has_open(db: object, user_id: UUID) -> bool:
        return has_open_session

    async def standing(db: object, user_id: UUID) -> Any:
        return SimpleNamespace(balance=Decimal(10), is_blocked=is_blocked)

    async def minimum(db: object, user_id: UUID) -> bool:
        return has_minimum

    async def open_vehicle(db: object, membership_id: UUID) -> UUID:
        vehicle_id = uuid4()
        record["vehicle_id"] = vehicle_id
        return vehicle_id

    async def create_pending(db: object, **kwargs: Any) -> PendingSessionReference:
        record["pending"] = kwargs
        return PendingSessionReference(
            session_id=uuid4(), station_id=STATION_ID, id_token="TOKEN-1"
        )

    async def resolve_tariff(db: object, station_id: UUID, at: datetime) -> TariffQuote:
        record["quote_for"] = (station_id, at)
        return TariffQuote(
            tariff_id=uuid4(),
            tariff_version_id=TARIFF_VERSION_ID,
            version_no=2,
            tariff_name="Standard",
            organization_id=uuid4(),
            currency="VND",
            price_per_kwh=Decimal(4500),
            normal_price_per_kwh=Decimal(4500),
            vat_rate_percent=Decimal(10),
            time_periods=None,
            at=at,
        )

    async def create_bill(db: object, **kwargs: Any) -> None:
        record["bill"] = kwargs

    async def lock_wallet(db: object, user_id: UUID) -> None:
        record["wallet_locked_for"] = user_id

    async def queue(db: object, **kwargs: Any) -> StationCommandReference:
        record["command"] = kwargs
        return StationCommandReference(
            command_id=uuid4(),
            station_id=STATION_ID,
            command_type=kwargs["command_type"],
            outcome=StationCommandOutcome.PENDING,
        )

    monkeypatch.setattr(stations_service, "resolve_scan_target", resolve_target)
    monkeypatch.setattr(charging_service, "has_active_session_on_connector", has_active)
    monkeypatch.setattr(charging_service, "has_open_session_by_user", has_open)
    monkeypatch.setattr(billing_service, "lock_wallet_for_charge", lock_wallet)
    monkeypatch.setattr(billing_service, "resolve_wallet_standing", standing)
    monkeypatch.setattr(billing_service, "has_minimum_balance", minimum)
    monkeypatch.setattr(
        driver_service, "find_open_vehicle_id_by_membership", open_vehicle
    )
    monkeypatch.setattr(charging_service, "create_pending_session", create_pending)
    monkeypatch.setattr(billing_service, "resolve_tariff_for_station", resolve_tariff)
    monkeypatch.setattr(billing_service, "create_quoted_bill", create_bill)
    monkeypatch.setattr(stations_service, "queue_station_command", queue)
    monkeypatch.setattr(settings, "BILLING_MIN_BALANCE_VND", Decimal(50_000))
    return record


@pytest.mark.asyncio
async def test_scan_creates_the_pending_session_and_queues_the_remote_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The payer is the caller; the truck comes from the driving session; the token is sent."""
    record = _patch_scan(monkeypatch)

    response = await flow.scan_charging_session_endpoint(
        ChargingSessionScanRequest(charger_code="OCPP-TEST-001", connector_number=1),
        principal=DRIVER,
        db_session=fake_db_session(),
    )

    assert response.status is SessionStatus.PENDING
    assert response.id_token == "TOKEN-1"
    # The price of the scan's hour is frozen on a QUOTED bill (PAY-10).
    assert record["quote_for"][0] == STATION_ID
    assert record["bill"]["session_id"] == response.session_id
    assert "wallet_locked_for" in record  # the person's scans run one at a time
    assert record["bill"]["quote"].tariff_version_id == TARIFF_VERSION_ID
    assert (response.price_per_kwh, response.vat_rate_percent) == (4500, Decimal(10))
    assert record["pending"] == {
        "station_id": STATION_ID,
        "organization_id": DRIVER.organization_id,
        "started_by": DRIVER.user_id,
        "vehicle_id": record["vehicle_id"],
    }
    command = record["command"]
    assert command["command_type"] is StationCommandType.REMOTE_START
    assert command["session_id"] == response.session_id
    assert command["evse_id"] == EVSE_ID
    assert command["requested_by"] == DRIVER.user_id


@pytest.mark.asyncio
async def test_scan_without_a_gun_is_refused_only_when_every_gun_is_charging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The driver may still pick a free gun on the screen (CO-14)."""
    _patch_scan(monkeypatch, gun=False)
    request = ChargingSessionScanRequest(charger_code="OCPP-TEST-001")
    await flow.scan_charging_session_endpoint(
        request, principal=DRIVER, db_session=fake_db_session()
    )

    async def all_busy(db: object, connector_id: UUID) -> bool:
        return True

    monkeypatch.setattr(charging_service, "has_active_session_on_connector", all_busy)
    with pytest.raises(ChargingConnectorBusyError):
        await flow.scan_charging_session_endpoint(
            request, principal=DRIVER, db_session=fake_db_session()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"busy_connectors": frozenset({CONNECTOR_ID})}, ChargingConnectorBusyError),
        ({"has_open_session": True}, ChargingSessionAlreadyOpenError),
        ({"is_blocked": True}, WalletBlockedError),
        ({"has_minimum": False}, InsufficientBalanceError),
    ],
)
async def test_scan_refuses_in_order_and_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], error: type[Exception]
) -> None:
    """A busy gun, an open charge, a blocked or low wallet stop the scan before any write."""
    record = _patch_scan(monkeypatch, **options)

    with pytest.raises(error) as raised:
        await flow.scan_charging_session_endpoint(
            ChargingSessionScanRequest(
                charger_code="OCPP-TEST-001", connector_number=1
            ),
            principal=DRIVER,
            db_session=fake_db_session(),
        )

    assert "pending" not in record and "command" not in record
    if error is InsufficientBalanceError:
        assert str(raised.value).startswith("INSUFFICIENT_BALANCE")


# --- Stop and receipt ----------------------------------------------------------


def _patch_session_lookup(monkeypatch: pytest.MonkeyPatch, session: Any) -> None:
    async def get_by_id(db: object, session_id: UUID, **_: object) -> Any:
        return session

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)


@pytest.mark.asyncio
async def test_stop_queues_a_remote_stop_for_the_person_who_started_the_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner stops with the fixed app reason; the session stays ACTIVE."""
    session = build_charging_session()
    session.started_by = DRIVER.user_id
    _patch_session_lookup(monkeypatch, session)
    queued: dict[str, Any] = {}

    async def queue(db: object, **kwargs: Any) -> StationCommandReference:
        queued.update(kwargs)
        return StationCommandReference(
            command_id=uuid4(),
            station_id=session.station_id,
            command_type=kwargs["command_type"],
            outcome=StationCommandOutcome.PENDING,
        )

    monkeypatch.setattr(stations_service, "queue_station_command", queue)

    response = await flow.stop_charging_session_endpoint(
        session.session_id, None, principal=DRIVER, db_session=fake_db_session()
    )

    assert response.status is SessionStatus.ACTIVE
    assert queued["command_type"] is StationCommandType.REMOTE_STOP
    assert (queued["session_id"], queued["evse_id"]) == (
        session.session_id,
        session.evse_id,
    )
    assert queued["reason"] == "Stopped from the app"


@pytest.mark.asyncio
async def test_stop_is_refused_to_a_stranger_and_for_a_session_that_is_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the starter or internal staff stop, and only an ACTIVE session."""
    session = build_charging_session()
    session.organization_id = DRIVER.organization_id
    _patch_session_lookup(monkeypatch, session)

    with pytest.raises(ChargingSessionStopDeniedError):
        await charging_service.authorize_session_stop(
            fake_db_session(), session.session_id, principal=DRIVER
        )

    staff = build_internal_principal()
    assert (
        await charging_service.authorize_session_stop(
            fake_db_session(), session.session_id, principal=staff
        )
    ).session_id == session.session_id

    session.status = SessionStatus.PENDING
    with pytest.raises(ChargingSessionStateError):
        await charging_service.authorize_session_stop(
            fake_db_session(), session.session_id, principal=staff
        )
    assert ChargingSessionStopRequest(reason="Driver left").reason == "Driver left"


@pytest.mark.asyncio
async def test_receipt_shows_the_place_and_energy_and_leaves_the_bill_null_until_wp9(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A COMPLETED session has a receipt; an unfinished one does not (CHG-03)."""
    session = build_charging_session(
        status=SessionStatus.COMPLETED,
        meter_start_wh=Decimal(1000),
        meter_stop_wh=Decimal(61000),
    )
    session.started_by = DRIVER.user_id
    session.organization_id = DRIVER.organization_id
    _patch_session_lookup(monkeypatch, session)

    async def measurement(db: object, *_: object, **__: object) -> None:
        return None

    async def place(
        db: object, station_id: UUID, evse_id: UUID | None
    ) -> SessionPlaceReference:
        return SessionPlaceReference(
            station_id=station_id,
            location_id=uuid4(),
            location_name="Depot",
            location_address="Km 1",
            physical_reference="Tru 1",
            gun_number=2,
            connector_standard="GBT_DC",
        )

    async def no_bill(db: object, session_id: UUID) -> None:
        return None

    for name in (
        "find_first_measurement_value",
        "find_last_measurement_value",
        "find_max_measurement_value",
    ):
        monkeypatch.setattr(charging_repository, name, measurement)
    monkeypatch.setattr(stations_service, "resolve_session_place_reference", place)
    monkeypatch.setattr(billing_service, "find_session_bill_reference", no_bill)

    receipt = await flow.get_charging_session_receipt_endpoint(
        session.session_id, principal=DRIVER, db_session=fake_db_session()
    )

    assert receipt.energy_delivered_wh == Decimal(60000)
    assert (receipt.location_name, receipt.gun_number) == ("Depot", 2)
    assert receipt.bill_status is None and receipt.total_amount is None

    session.status = SessionStatus.ACTIVE
    with pytest.raises(ChargingSessionStateError):
        await flow.get_charging_session_receipt_endpoint(
            session.session_id, principal=DRIVER, db_session=fake_db_session()
        )


# --- Expiry and failed starts (CE-10) ----------------------------------------


@pytest.mark.asyncio
async def test_sweep_abandons_scans_older_than_the_pending_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway sweep passes now minus the configured timeout."""
    asked: list[datetime] = []

    async def abandon_before(db: object, created_before: datetime) -> list[UUID]:
        asked.append(created_before)
        return [uuid4(), uuid4()]

    monkeypatch.setattr(
        charging_repository, "abandon_pending_sessions_created_before", abandon_before
    )
    monkeypatch.setattr(settings, "CHARGING_PENDING_SESSION_TIMEOUT_SECONDS", 300.0)

    abandoned = await command_loop.sweep_expired_pending_sessions(
        FakeSessionFactory()  # type: ignore[arg-type]
    )

    expected = datetime.now(timezone.utc) - timedelta(seconds=300)
    assert abandoned == 2
    assert abs((asked[0] - expected).total_seconds()) < 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_type", "outcome", "is_abandoned"),
    [
        ("REMOTE_START", StationCommandOutcome.REJECTED, True),
        ("REMOTE_START", StationCommandOutcome.NOT_SENT, True),
        ("REMOTE_START", StationCommandOutcome.TIMEOUT, True),
        ("REMOTE_START", StationCommandOutcome.ACCEPTED, False),
        ("REMOTE_STOP", StationCommandOutcome.REJECTED, False),
    ],
)
async def test_a_failed_remote_start_abandons_its_pending_session(
    monkeypatch: pytest.MonkeyPatch,
    command_type: str,
    outcome: StationCommandOutcome,
    is_abandoned: bool,
) -> None:
    """Only a remote start that did not go through ends its session."""
    session_id = uuid4()
    abandoned: list[UUID] = []

    async def get_command(db: object, command_id: UUID) -> Any:
        return SimpleNamespace(command_type=command_type, session_id=session_id)

    async def abandon(db: object, the_session_id: UUID) -> bool:
        abandoned.append(the_session_id)
        return True

    monkeypatch.setattr(ocpp_state_repository, "get_station_command_by_id", get_command)
    monkeypatch.setattr(charging_repository, "abandon_pending_session", abandon)

    await command_loop.abandon_session_of_failed_start(
        fake_db_session(), uuid4(), outcome
    )

    assert abandoned == ([session_id] if is_abandoned else [])


@pytest.mark.asyncio
async def test_a_token_is_valid_for_authorize_only_when_a_live_session_holds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorize looks for a PENDING (inside the window) or ACTIVE session of the charger."""
    counts = iter([1, 0])
    windows: list[datetime] = []

    async def count_with_token(
        db: object,
        station_id: UUID,
        id_token: str,
        *,
        pending_created_after: datetime,
    ) -> int:
        windows.append(pending_created_after)
        return next(counts)

    monkeypatch.setattr(
        charging_repository, "count_sessions_with_token", count_with_token
    )

    assert await charging_service.is_start_token_valid(
        fake_db_session(), station_id=STATION_ID, id_token="TOKEN-1"
    )
    assert not await charging_service.is_start_token_valid(
        fake_db_session(), station_id=STATION_ID, id_token="OTHER"
    )
    assert not await charging_service.is_start_token_valid(
        fake_db_session(), station_id=STATION_ID, id_token=""
    )
    assert len(windows) == 2


def test_the_response_schema_carries_the_derived_energy_of_a_finished_session() -> None:
    """History lists show a finished session's kWh without another query (CHG-02)."""
    session = build_charging_session(
        status=SessionStatus.COMPLETED,
        meter_start_wh=Decimal(10),
        meter_stop_wh=Decimal(25),
    )

    assert ChargingSessionResponse.model_validate(session).energy_delivered_wh == 15
