"""PostgreSQL review tests for billing and the QR start (RV-BL2, RV-BL3, RV-BL9).

RV-BL2 and RV-BL3 need two real transactions interleaving; RV-BL9 needs the
real conditional UPDATE, the session-ended hook and the command table. They run
only with ``RUN_DB_INTEGRATION=1`` on a temporary database built by the
``temporary_database`` fixture of ``test_postgres_integration``. Each test
asserts the correct behaviour; while a defect was open its test was marked
``xfail(strict=True)``, and the marker came off with the fix.

To interleave deterministically, the first transaction stays open while the
second one runs as a task; the test waits until that task has finished or is
blocked on a row lock (seen in ``pg_stat_activity``) before committing the
first.
"""

import asyncio
import os
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.api.charging_session_flow as charging_session_flow
import app.domains.billing.router as billing_router
import app.domains.billing.topup_service as topup_service
from app.domains.billing.schemas import (
    PaymentResponse,
    TariffCreateRequest,
    TariffVersionPublishRequest,
    TopUpRequest,
)
from app.domains.billing.types import (
    BankNotification,
    BankNotificationResult,
    PaymentStatus,
)
from app.domains.charging_sessions.exceptions import ChargingSessionAlreadyOpenError
from app.domains.charging_sessions.schemas import ChargingSessionScanRequest
from app.domains.charging_sessions.types import SessionStatus
from app.domains.identity.types import Principal, UserRole
from app.libs.common.config import settings
from tests import test_postgres_integration as postgres_integration
from tests.principals import build_internal_principal, build_principal

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="Set RUN_DB_INTEGRATION=1 to run the PostgreSQL integration test",
)

# The temporary, migrated database of the main integration test module.
temporary_database = postgres_integration.temporary_database

# How long a test waits for the second transaction to block or finish.
_INTERLEAVE_TIMEOUT_SECONDS = 10.0


async def _wait_until_done_or_blocked(
    engine: AsyncEngine, task: "asyncio.Task[Any]"
) -> None:
    """Wait until a task has finished or another backend waits on a lock.

    Args:
        engine: Engine of the temporary database (a separate connection polls).
        task: The second transaction, running concurrently.

    Side Effects:
        Polls ``pg_stat_activity``; returns after the timeout at the latest, so
        a fixed version that neither blocks nor finishes quickly cannot hang.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _INTERLEAVE_TIMEOUT_SECONDS
    while not task.done() and loop.time() < deadline:
        async with engine.connect() as connection:
            waiting = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_reading_an_expired_top_up_never_fails_a_payment_credited_meanwhile(
    temporary_database: str,
) -> None:
    """A poll of an expired code racing the late bank notification keeps SUCCEEDED.

    The webhook credits the top-up and holds the payment lock; the app's poll
    reads the payment (still PENDING to it), waits for the lock, and must then
    see the committed SUCCEEDED, not overwrite it with FAILED.
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    _, user_id = await postgres_integration._provision_organization_and_user(engine)
    driver = build_principal(roles=frozenset({UserRole.DRIVER}), user_id=user_id)
    try:
        async with session_factory.begin() as db:
            top_up = await topup_service.create_top_up(
                db, TopUpRequest(amount=200_000), principal=driver
            )
        # The code expired an hour ago; the money still arrives (BL-23).
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE payments SET created_at = now() - interval '1 hour' "
                    "WHERE payment_id = :p"
                ),
                {"p": top_up.payment_id},
            )
        notification = BankNotification(
            bank_transaction_id="FT-LATE-1",
            amount=200_000,
            content=top_up.transfer_code,
        )

        async def poll_payment() -> PaymentResponse:
            """Read the payment the way the app's poll does, in its own transaction."""
            async with session_factory.begin() as db:
                return await topup_service.get_payment(
                    db, top_up.payment_id, principal=driver
                )

        async with session_factory() as webhook_db:
            async with webhook_db.begin():
                credited = await topup_service.process_bank_notification(
                    webhook_db, notification
                )
                poll = asyncio.create_task(poll_payment())
                await _wait_until_done_or_blocked(engine, poll)
        seen = await poll
        async with engine.connect() as connection:
            stored_status, top_up_lines = (
                await connection.execute(
                    text(
                        "SELECT p.status, (SELECT count(*) FROM wallet_transactions w "
                        "WHERE w.payment_id = p.payment_id) "
                        "FROM payments p WHERE p.payment_id = :p"
                    ),
                    {"p": top_up.payment_id},
                )
            ).one()
    finally:
        await engine.dispose()

    assert credited.result is BankNotificationResult.CREDITED
    assert top_up_lines == 1
    assert stored_status == PaymentStatus.SUCCEEDED.value
    assert seen.status is PaymentStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_two_concurrent_scans_by_one_person_open_only_one_charge(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person scanning two chargers at the same moment gets one open session.

    The first scan's transaction is still open when the second scan runs; the
    second must be refused (409, or the database's unique index) once the
    first commits, whichever way the fix serializes them.
    """
    monkeypatch.setattr(settings, "BILLING_MIN_BALANCE_VND", Decimal(0))
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    owner_id, location_id = await postgres_integration._provision_location(engine)
    for identity in ("REV-SCAN-A", "REV-SCAN-B"):
        await postgres_integration._provision_station_at(
            engine, location_id, identity, ["Available"]
        )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE charging_station_state SET last_seen_at = now()")
        )
    (
        organization_id,
        user_id,
    ) = await postgres_integration._provision_organization_and_user(engine)
    driver = build_principal(
        roles=frozenset({UserRole.DRIVER}),
        organization_id=organization_id,
        user_id=user_id,
    )
    admin = build_internal_principal()

    async def scan(db: AsyncSession, identity: str, principal: Principal) -> None:
        """Scan a charger's QR code (no gun named) inside the given transaction."""
        await charging_session_flow.scan_charging_session_endpoint(
            ChargingSessionScanRequest(charger_code=identity),
            principal=principal,
            db_session=db,
        )

    async def second_scan() -> str:
        """Run the second scan in its own transaction and say how it ended."""
        try:
            async with session_factory.begin() as db:
                await scan(db, "REV-SCAN-B", driver)
        except (ChargingSessionAlreadyOpenError, IntegrityError):
            return "refused"
        return "created"

    try:
        async with session_factory.begin() as db:
            tariff = await billing_router.create_tariff_endpoint(
                TariffCreateRequest(name="Default", organization_id=owner_id),
                principal=admin,
                db_session=db,
            )
            await billing_router.publish_tariff_version_endpoint(
                tariff.tariff_id,
                TariffVersionPublishRequest(
                    price_per_kwh=4500,
                    vat_rate_percent=Decimal(10),
                    change_reason="Review price",
                ),
                principal=admin,
                db_session=db,
            )
        async with session_factory() as first_db:
            async with first_db.begin():
                await scan(first_db, "REV-SCAN-A", driver)
                second = asyncio.create_task(second_scan())
                await _wait_until_done_or_blocked(engine, second)
        second_outcome = await second
        async with engine.connect() as connection:
            open_sessions = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM charging_sessions WHERE started_by = :u "
                        "AND status IN ('PENDING', 'ACTIVE')"
                    ),
                    {"u": user_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert open_sessions == 1
    assert second_outcome == "refused"


@pytest.mark.asyncio
async def test_stopping_a_pending_scan_abandons_it_and_frees_the_person(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping a scan the charger never started cancels it at once (RV-BL9).

    The session ends ``ABANDONED``, its quoted bill is voided, no
    ``REMOTE_STOP`` is queued, and the same person can scan again right away
    instead of waiting for the pending window to run out.
    """
    from app.api.startup import register_all_hooks

    register_all_hooks()
    monkeypatch.setattr(settings, "BILLING_MIN_BALANCE_VND", Decimal(0))
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    owner_id, location_id = await postgres_integration._provision_location(engine)
    await postgres_integration._provision_station_at(
        engine, location_id, "REV-CANCEL-A", ["Available"]
    )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE charging_station_state SET last_seen_at = now()")
        )
    (
        organization_id,
        user_id,
    ) = await postgres_integration._provision_organization_and_user(engine)
    driver = build_principal(
        roles=frozenset({UserRole.DRIVER}),
        organization_id=organization_id,
        user_id=user_id,
    )
    admin = build_internal_principal()
    scan_request = ChargingSessionScanRequest(charger_code="REV-CANCEL-A")
    try:
        async with session_factory.begin() as db:
            tariff = await billing_router.create_tariff_endpoint(
                TariffCreateRequest(name="Default", organization_id=owner_id),
                principal=admin,
                db_session=db,
            )
            await billing_router.publish_tariff_version_endpoint(
                tariff.tariff_id,
                TariffVersionPublishRequest(
                    price_per_kwh=4500,
                    vat_rate_percent=Decimal(10),
                    change_reason="Review price",
                ),
                principal=admin,
                db_session=db,
            )
        async with session_factory.begin() as db:
            scan = await charging_session_flow.scan_charging_session_endpoint(
                scan_request, principal=driver, db_session=db
            )
        async with session_factory.begin() as db:
            stopped = await charging_session_flow.stop_charging_session_endpoint(
                scan.session_id, None, principal=driver, db_session=db
            )
        async with engine.connect() as connection:
            session_status, bill_status, remote_stops = (
                await connection.execute(
                    text(
                        "SELECT s.status, b.status, (SELECT count(*) FROM "
                        "charging_station_commands c WHERE c.session_id = "
                        "s.session_id AND c.command_type = 'REMOTE_STOP') "
                        "FROM charging_sessions s JOIN charging_session_bills b "
                        "USING (session_id) WHERE s.session_id = :s"
                    ),
                    {"s": scan.session_id},
                )
            ).one()
        async with session_factory.begin() as db:
            rescan = await charging_session_flow.scan_charging_session_endpoint(
                scan_request, principal=driver, db_session=db
            )
    finally:
        await engine.dispose()

    assert stopped.command_id is None
    assert stopped.status is SessionStatus.ABANDONED
    assert (session_status, bill_status, remote_stops) == ("ABANDONED", "VOID", 0)
    assert rescan.status is SessionStatus.PENDING


@pytest.mark.asyncio
async def test_staff_retry_bills_a_completed_session_whose_hook_failed(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bill left QUOTED by a failed hook is billed by the staff retry (D2).

    No session-ended hook is registered here, which is what a failed hook
    leaves behind: the session is COMPLETED and its bill still QUOTED. The
    retry bills it, debits the wallet once, records the staff member in the
    bill's history, and a second retry changes nothing.
    """
    monkeypatch.setattr(settings, "BILLING_MIN_BALANCE_VND", Decimal(-1_000_000))
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    owner_id, location_id = await postgres_integration._provision_location(engine)
    await postgres_integration._provision_station_at(
        engine, location_id, "REV-RETRY-A", ["Available"]
    )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE charging_station_state SET last_seen_at = now()")
        )
    (
        organization_id,
        user_id,
    ) = await postgres_integration._provision_organization_and_user(engine)
    (
        staff_organization_id,
        staff_user_id,
    ) = await postgres_integration._provision_organization_and_user(engine)
    driver = build_principal(
        roles=frozenset({UserRole.DRIVER}),
        organization_id=organization_id,
        user_id=user_id,
    )
    staff = build_principal(
        roles=frozenset({UserRole.HEAD_ADMIN}),
        organization_id=staff_organization_id,
        user_id=staff_user_id,
        is_internal=True,
    )
    try:
        async with session_factory.begin() as db:
            tariff = await billing_router.create_tariff_endpoint(
                TariffCreateRequest(name="Default", organization_id=owner_id),
                principal=staff,
                db_session=db,
            )
            await billing_router.publish_tariff_version_endpoint(
                tariff.tariff_id,
                TariffVersionPublishRequest(
                    price_per_kwh=4500,
                    vat_rate_percent=Decimal(10),
                    change_reason="Review price",
                ),
                principal=staff,
                db_session=db,
            )
        async with session_factory.begin() as db:
            scan = await charging_session_flow.scan_charging_session_endpoint(
                ChargingSessionScanRequest(charger_code="REV-RETRY-A"),
                principal=driver,
                db_session=db,
            )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE charging_sessions s SET status = 'COMPLETED', "
                    "ocpp_transaction_id = 'RETRY-1', evse_id = e.evse_id, "
                    "connector_id = c.connector_id, "
                    "started_at = now() - interval '1 hour', ended_at = now(), "
                    "meter_start_wh = 1000, meter_stop_wh = 3000 "
                    "FROM charging_evses e JOIN charging_connectors c "
                    "USING (evse_id) "
                    "WHERE s.session_id = :s AND e.station_id = s.station_id"
                ),
                {"s": scan.session_id},
            )
        async with session_factory.begin() as db:
            first = await charging_session_flow.settle_charging_session_bill_endpoint(
                scan.session_id, principal=staff, db_session=db
            )
        async with session_factory.begin() as db:
            second = await charging_session_flow.settle_charging_session_bill_endpoint(
                scan.session_id, principal=staff, db_session=db
            )
        async with engine.connect() as connection:
            debits = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM wallet_transactions t JOIN wallets w "
                        "USING (wallet_id) WHERE w.user_id = :u"
                    ),
                    {"u": user_id},
                )
            ).scalar_one()
            history_actor = (
                await connection.execute(
                    text(
                        "SELECT changed_by FROM charging_session_bill_history "
                        "WHERE session_id = :s"
                    ),
                    {"s": scan.session_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert first.status == "BILLED"
    assert first.total_amount == 9900
    assert second.status == "BILLED"
    assert debits == 1
    assert history_actor == staff_user_id
