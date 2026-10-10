"""Smoke tests for billing's wiring to the end of a session and the API gates (WP9)."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

import app.api.billing_hooks as billing_hooks
import app.api.charging_session_flow as flow
import app.api.startup as startup
import app.domains.billing.router as billing_router
import app.domains.billing.service as billing_service
import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
import app.domains.charging_stations.service as stations_service
from app.api.main import app
from app.domains.billing.exceptions import NoTariffInForceError
from app.domains.charging_sessions.schemas import ChargingSessionScanRequest
from app.domains.charging_sessions.types import SessionEndedEvent, SessionStatus
from app.domains.charging_stations.types import ScanTargetReference
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import UserRole
from tests.billing.test_billing_services_smoke import FakeDb
from tests.builders import build_charging_session, fake_db_session
from tests.principals import build_internal_principal, build_principal

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def _event(status: SessionStatus, started_by: UUID | None) -> SessionEndedEvent:
    return SessionEndedEvent(
        session_id=uuid4(),
        status=status,
        started_by=started_by,
        meter_start_wh=Decimal(1000),
        meter_stop_wh=Decimal(2000),
        last_measured_wh=Decimal(1900),
    )


@pytest.mark.asyncio
async def test_the_hook_settles_a_completed_session_and_voids_an_abandoned_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMPLETED goes to settle_session_bill with the readings; ABANDONED voids."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def settle(db: object, **kwargs: Any) -> None:
        calls.append(("settle", kwargs))

    async def void(db: object, session_id: UUID) -> bool:
        calls.append(("void", {"session_id": session_id}))
        return True

    monkeypatch.setattr(billing_service, "settle_session_bill", settle)
    monkeypatch.setattr(billing_service, "void_session_bill", void)
    user_id = uuid4()
    completed = _event(SessionStatus.COMPLETED, user_id)
    abandoned = _event(SessionStatus.ABANDONED, None)

    await billing_hooks.bill_ended_session(fake_db_session(), completed)
    await billing_hooks.bill_ended_session(fake_db_session(), abandoned)

    assert calls[0] == (
        "settle",
        {
            "session_id": completed.session_id,
            "payer_user_id": user_id,
            "meter_start_wh": Decimal(1000),
            "meter_stop_wh": Decimal(2000),
            "last_measured_wh": Decimal(1900),
        },
    )
    assert calls[1] == ("void", {"session_id": abandoned.session_id})


def test_registering_the_hooks_twice_adds_billing_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every process calls the same start-up function; it is idempotent."""
    monkeypatch.setattr(charging_service, "_session_ended_hooks", [])

    startup.register_all_hooks()
    startup.register_all_hooks()

    assert charging_service._session_ended_hooks == [billing_hooks.bill_ended_session]


@pytest.mark.asyncio
async def test_completing_a_session_runs_the_hook_with_the_readings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """complete_session tells the hook the readings and the newest measurement."""
    session = build_charging_session(meter_start_wh=Decimal(1000))
    seen: list[SessionEndedEvent] = []

    async def get_by_transaction(
        db: object, station_id: UUID, transaction_id: str
    ) -> Any:
        return session

    async def last_measurement(db: object, session_id: UUID, **_: object) -> Decimal:
        return Decimal(1700)

    async def hook(db: object, event: SessionEndedEvent) -> None:
        seen.append(event)

    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )
    monkeypatch.setattr(
        charging_repository, "find_last_measurement_value", last_measurement
    )
    charging_service.register_session_ended_hook(hook)

    await charging_service.complete_session(
        FakeDb(),  # type: ignore[arg-type]
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=str(session.ocpp_transaction_id),
        ended_at=NOW,
        meter_stop_wh=Decimal(1750),
    )

    assert seen == [
        SessionEndedEvent(
            session_id=session.session_id,
            status=SessionStatus.COMPLETED,
            started_by=session.started_by,
            meter_start_wh=Decimal(1000),
            meter_stop_wh=Decimal(1750),
            last_measured_wh=Decimal(1700),
        )
    ]


@pytest.mark.asyncio
async def test_a_failing_hook_never_undoes_the_end_of_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A billing fault is logged and isolated; the stop message still completes."""
    session = build_charging_session(meter_start_wh=Decimal(1000))

    async def get_by_transaction(
        db: object, station_id: UUID, transaction_id: str
    ) -> Any:
        return session

    async def last_measurement(db: object, session_id: UUID, **_: object) -> None:
        return None

    async def broken_hook(db: object, event: SessionEndedEvent) -> None:
        raise RuntimeError("billing is down")

    monkeypatch.setattr(
        charging_repository, "get_session_by_transaction", get_by_transaction
    )
    monkeypatch.setattr(
        charging_repository, "find_last_measurement_value", last_measurement
    )
    charging_service.register_session_ended_hook(broken_hook)

    result = await charging_service.complete_session(
        FakeDb(),  # type: ignore[arg-type]
        station_id=session.station_id,
        evse_id=session.evse_id,
        connector_id=session.connector_id,
        transaction_id=str(session.ocpp_transaction_id),
        ended_at=NOW,
        meter_stop_wh=Decimal(1750),
    )

    assert result.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_scan_for_a_charger_with_no_price_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO_TARIFF stops the scan before the session row and the command exist."""
    created: list[str] = []
    driver = build_principal(roles=frozenset({UserRole.DRIVER}))
    station_id = uuid4()

    async def resolve_target(db: object, **kwargs: Any) -> ScanTargetReference:
        return ScanTargetReference(
            station_id=station_id,
            location_id=uuid4(),
            location_name="Depot",
            evse_id=None,
            connector_id=None,
            gun_number=None,
            connector_ids=(),
        )

    async def no_open(db: object, user_id: UUID) -> bool:
        return False

    async def standing(db: object, user_id: UUID) -> Any:
        return type("W", (), {"balance": Decimal(0), "is_blocked": False})()

    async def minimum(db: object, user_id: UUID) -> bool:
        return True

    async def no_price(db: object, station_id: UUID, at: datetime) -> None:
        raise NoTariffInForceError("NO_TARIFF: no price is set for this charger")

    async def create_pending(db: object, **kwargs: Any) -> None:
        created.append("session")

    async def queue(db: object, **kwargs: Any) -> None:
        created.append("command")

    monkeypatch.setattr(stations_service, "resolve_scan_target", resolve_target)
    monkeypatch.setattr(charging_service, "has_open_session_by_user", no_open)

    async def lock_wallet(db: object, user_id: UUID) -> None:
        return None

    monkeypatch.setattr(billing_service, "lock_wallet_for_charge", lock_wallet)
    monkeypatch.setattr(billing_service, "resolve_wallet_standing", standing)
    monkeypatch.setattr(billing_service, "has_minimum_balance", minimum)
    monkeypatch.setattr(billing_service, "resolve_tariff_for_station", no_price)
    monkeypatch.setattr(charging_service, "create_pending_session", create_pending)
    monkeypatch.setattr(stations_service, "queue_station_command", queue)

    with pytest.raises(NoTariffInForceError, match="NO_TARIFF"):
        await flow.scan_charging_session_endpoint(
            ChargingSessionScanRequest(charger_code="X"),
            principal=driver,
            db_session=fake_db_session(),
        )

    assert created == []


def test_every_billing_endpoint_is_registered_and_the_webhook_is_the_only_open_one() -> (
    None
):
    """The router is mounted, and only the bank webhook skips the bearer token."""
    operations = {
        (method.upper(), path): operation
        for path, methods in app.openapi()["paths"].items()
        for method, operation in methods.items()
    }
    expected = {
        ("POST", "/api/v1/tariffs"),
        ("GET", "/api/v1/tariffs"),
        ("GET", "/api/v1/tariffs/in-force"),
        ("POST", "/api/v1/tariffs/{tariff_id}/versions"),
        ("GET", "/api/v1/wallets/me"),
        ("GET", "/api/v1/wallets/me/transactions"),
        ("POST", "/api/v1/wallets/me/top-ups"),
        ("GET", "/api/v1/wallets/{user_id}"),
        ("POST", "/api/v1/wallets/{user_id}/adjustments"),
        ("GET", "/api/v1/payments/{payment_id}"),
        ("POST", "/api/v1/payments/vietqr/notifications"),
        ("GET", "/api/v1/charging-session-bills"),
        ("GET", "/api/v1/charging-sessions/{session_id}/bill"),
    }

    assert expected <= set(operations)
    assert not operations[("POST", "/api/v1/payments/vietqr/notifications")].get(
        "security"
    )
    assert operations[("POST", "/api/v1/tariffs")].get("security")


async def _is_allowed(gate: Any, principal: Any) -> bool:
    """Tell whether a role gate lets a principal through."""
    try:
        await gate(principal=principal)
    except AccessDeniedError:
        return False
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "writes_tariffs", "reads_tariffs", "uses_wallet", "tops_up"),
    [
        (UserRole.DRIVER, False, False, True, True),
        (UserRole.OPERATIONS, False, True, False, False),
        (UserRole.ACCOUNTANT, False, False, True, False),
        (UserRole.ORG_ADMIN, True, True, True, True),
        (UserRole.HEAD_ADMIN, True, True, True, True),
    ],
)
async def test_the_role_gates_follow_the_feature_catalog(
    role: UserRole,
    writes_tariffs: bool,
    reads_tariffs: bool,
    uses_wallet: bool,
    tops_up: bool,
) -> None:
    """Drivers use their wallet, operations read tariffs, owners write them."""
    principal = build_principal(roles=frozenset({role}))

    assert await _is_allowed(billing_router.TARIFF_WRITERS, principal) is writes_tariffs
    assert await _is_allowed(billing_router.TARIFF_READERS, principal) is reads_tariffs
    assert await _is_allowed(billing_router.OWN_WALLET_USERS, principal) is uses_wallet
    assert await _is_allowed(billing_router.TOP_UP_USERS, principal) is tops_up


@pytest.mark.asyncio
async def test_staff_gates_need_an_internal_organization() -> None:
    """A customer's ORG_ADMIN cannot read other people's wallets or review bills."""
    customer_admin = build_principal(roles=frozenset({UserRole.ORG_ADMIN}))
    internal_staff = build_internal_principal(roles=frozenset({UserRole.ACCOUNTANT}))

    for gate in (billing_router.WALLET_STAFF, billing_router.BILL_REVIEWERS):
        assert not await _is_allowed(gate, customer_admin)
        assert await _is_allowed(gate, internal_staff)
    assert not await _is_allowed(billing_router.BANK_SIMULATORS, internal_staff)
