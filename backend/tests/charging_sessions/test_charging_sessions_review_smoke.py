"""Review tests for the QR charging flow: access guards and known defects.

Guards (they pass today) pin who may read, stop or see the bill of a charging
session and that only an ACTIVE session can be stopped. Tests marked
``xfail(strict=True)`` describe the correct behaviour of a defect found in the
review (``REVIEW BL-n``); the fix makes them pass, which strict mode turns into
a failure until the marker is removed.

The scan is driven through ``_patch_scan`` of ``test_qr_start_flow_smoke``
(every other domain faked), with the real wallet rules put back where a test
is about them.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

import app.api.charging_session_flow as flow
import app.domains.billing.repository as billing_repository
import app.domains.billing.service as billing_service
import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
import app.domains.charging_stations.service as stations_service
from app.domains.billing.exceptions import InsufficientBalanceError
from app.domains.billing.models import WalletModel
from app.domains.charging_sessions.exceptions import (
    ChargingSessionNotFoundError,
    ChargingSessionStateError,
    ChargingSessionStopDeniedError,
)
from app.domains.charging_sessions.models import ChargingSessionModel
from app.domains.charging_sessions.schemas import ChargingSessionScanRequest
from app.domains.charging_sessions.types import SessionStatus
from app.domains.charging_stations.types import (
    StationCommandOutcome,
    StationCommandReference,
)
from app.domains.identity.types import UserRole
from app.libs.common.config import settings
from tests.builders import build_charging_session, fake_db_session
from tests.charging_sessions.test_qr_start_flow_smoke import _patch_scan
from tests.principals import build_principal

# The real wallet rules, captured before any test patches them.
REAL_RESOLVE_WALLET_STANDING = billing_service.resolve_wallet_standing
REAL_HAS_MINIMUM_BALANCE = billing_service.has_minimum_balance

DRIVER = build_principal(roles=frozenset({UserRole.DRIVER}), user_id=uuid4())
FLEET_MANAGER = build_principal(roles=frozenset({UserRole.FLEET_MANAGER}))


def _serve_session(
    monkeypatch: pytest.MonkeyPatch,
    session: ChargingSessionModel | None,
    lookups: list[dict[str, object]] | None = None,
) -> None:
    """Make the session repository return one session and record the filters.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        session: What every lookup returns (``None`` = out of reach).
        lookups: Receives the keyword filters of each lookup, when given.
    """

    async def get_by_id(db: object, session_id: UUID, **filters: object) -> Any:
        """Record the scope filters and return the session."""
        if lookups is not None:
            lookups.append(filters)
        return session

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)


def _record_commands(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the station command queue by a list.

    Args:
        monkeypatch: The pytest monkeypatch fixture.

    Returns:
        The keyword arguments of every queued command, in order.
    """
    queued: list[dict[str, Any]] = []

    async def queue(db: object, **kwargs: Any) -> StationCommandReference:
        """Record the command and answer it is pending."""
        queued.append(kwargs)
        return StationCommandReference(
            command_id=uuid4(),
            station_id=kwargs["station_id"],
            command_type=kwargs["command_type"],
            outcome=StationCommandOutcome.PENDING,
        )

    monkeypatch.setattr(stations_service, "queue_station_command", queue)
    return queued


# --- Stop (guards) --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SessionStatus.COMPLETED, SessionStatus.ABANDONED])
async def test_a_finished_session_cannot_be_stopped_even_by_its_starter(
    monkeypatch: pytest.MonkeyPatch, status: SessionStatus
) -> None:
    """Stopping a COMPLETED or ABANDONED session is a 409 and queues nothing."""
    session = build_charging_session(status=status)
    session.started_by = DRIVER.user_id
    _serve_session(monkeypatch, session)
    queued = _record_commands(monkeypatch)

    with pytest.raises(ChargingSessionStateError):
        await flow.stop_charging_session_endpoint(
            session.session_id, None, principal=DRIVER, db_session=fake_db_session()
        )

    assert queued == []


@pytest.mark.asyncio
async def test_a_customer_manager_cannot_stop_a_drivers_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the person who scanned (or our staff) stops; a manager reads only."""
    session = build_charging_session()
    session.organization_id = FLEET_MANAGER.organization_id
    _serve_session(monkeypatch, session)
    queued = _record_commands(monkeypatch)

    with pytest.raises(ChargingSessionStopDeniedError):
        await flow.stop_charging_session_endpoint(
            session.session_id,
            None,
            principal=FLEET_MANAGER,
            db_session=fake_db_session(),
        )

    assert queued == []


# --- Reads (IDOR guards) ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_driver_reads_only_sessions_they_started_in_their_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DRIVER-only caller is scoped to their organization and their own scans.

    The repository answers "no such row" for anything outside the filters it is
    given, so the filters are the access rule: a session out of reach is a 404.
    """
    lookups: list[dict[str, object]] = []
    _serve_session(monkeypatch, None, lookups)

    for principal in (DRIVER, FLEET_MANAGER):
        with pytest.raises(ChargingSessionNotFoundError):
            await charging_service.get_charging_session(
                fake_db_session(), uuid4(), principal=principal
            )

    assert lookups == [
        {"organization_id": DRIVER.organization_id, "started_by": DRIVER.user_id},
        {"organization_id": FLEET_MANAGER.organization_id, "started_by": None},
    ]


@pytest.mark.asyncio
async def test_the_bill_and_receipt_of_a_session_out_of_reach_are_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another person's session hides its bill and receipt; billing is not asked."""
    _serve_session(monkeypatch, None)
    billing_calls: list[UUID] = []

    async def bill(db: object, session_id: UUID) -> Any:
        """Record a bill read that must not happen."""
        billing_calls.append(session_id)
        raise AssertionError("the bill was read for a session out of reach")

    monkeypatch.setattr(billing_service, "get_session_bill_response", bill)
    monkeypatch.setattr(billing_service, "find_session_bill_reference", bill)

    with pytest.raises(ChargingSessionNotFoundError):
        await flow.get_charging_session_bill_endpoint(
            uuid4(), principal=DRIVER, db_session=fake_db_session()
        )
    with pytest.raises(ChargingSessionNotFoundError):
        await flow.get_charging_session_receipt_endpoint(
            uuid4(), principal=DRIVER, db_session=fake_db_session()
        )

    assert billing_calls == []


# --- Defects found in the review (strict xfail) -------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-BL1: BILLING_MIN_BALANCE_VND=0 lets a negative wallet charge",
)
async def test_a_scan_is_refused_while_the_wallet_is_negative_even_with_no_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BL-14: below the minimum (never less than zero) no new charge starts."""
    record = _patch_scan(monkeypatch)
    monkeypatch.setattr(
        billing_service, "resolve_wallet_standing", REAL_RESOLVE_WALLET_STANDING
    )
    monkeypatch.setattr(
        billing_service, "has_minimum_balance", REAL_HAS_MINIMUM_BALANCE
    )
    monkeypatch.setattr(settings, "BILLING_MIN_BALANCE_VND", Decimal(0))

    async def negative_wallet(db: object, user_id: UUID, **_: object) -> WalletModel:
        """Return a wallet left at -5,000,000 VND by a long charge."""
        return WalletModel(
            wallet_id=uuid4(),
            user_id=user_id,
            balance=Decimal(-5_000_000),
            currency="VND",
            status="ACTIVE",
        )

    monkeypatch.setattr(billing_repository, "find_wallet_by_user_id", negative_wallet)

    with pytest.raises(InsufficientBalanceError):
        await flow.scan_charging_session_endpoint(
            ChargingSessionScanRequest(
                charger_code="OCPP-TEST-001", connector_number=1
            ),
            principal=DRIVER,
            db_session=fake_db_session(),
        )

    assert "pending" not in record and "command" not in record


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-BL9: a PENDING scan cannot be cancelled by the person who made it",
)
async def test_the_person_who_scanned_can_end_their_pending_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ending a PENDING scan is accepted (stop or a cancel endpoint; adapt here)."""
    session = build_charging_session(status=SessionStatus.PENDING)
    session.started_by = DRIVER.user_id
    session.organization_id = DRIVER.organization_id
    _serve_session(monkeypatch, session)
    _record_commands(monkeypatch)
    abandoned: list[UUID] = []

    async def abandon(db: object, session_id: UUID) -> bool:
        """Record the abandon the cancel should cause."""
        abandoned.append(session_id)
        return True

    monkeypatch.setattr(charging_service, "abandon_pending_session", abandon)

    await flow.stop_charging_session_endpoint(
        session.session_id, None, principal=DRIVER, db_session=fake_db_session()
    )
