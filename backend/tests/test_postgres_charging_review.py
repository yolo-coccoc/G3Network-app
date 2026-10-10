"""PostgreSQL review tests of the OCPP command table and the session start.

Written by the 2026-10-10 security and correctness review of the charging
network, for the findings that only a real database shows: the command row
written after it was closed (RV-CS6), the start racing an abandon (RV-CS7) and a
retried 1.6J ``StartTransaction`` (RV-CS3). Each finding test asserts the
**correct** behaviour and is marked ``xfail(strict=True)`` while the defect
exists. Guard tests pin the command-table rules that already hold.

Like ``tests/test_postgres_integration.py`` (whose ``temporary_database``
fixture and provisioning helpers are reused), the module is skipped unless
``RUN_DB_INTEGRATION=1``. The 1.6J adapter's handlers are called directly with
a session factory on the temporary database, so no socket is opened.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from ocpp.v16.enums import AuthorizationStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
from app.domains.charging_sessions.exceptions import ChargingSessionTokenError
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from app.domains.charging_stations.types import (
    StationCommandOutcome,
    StationCommandType,
)
from tests.test_postgres_integration import (
    _provision_station,
    _scan,
    temporary_database,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="Set RUN_DB_INTEGRATION=1 to run the PostgreSQL integration test",
)

# Re-exported so pytest finds the fixture in this module.
__all__ = ["temporary_database"]


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the session factory the gateway would use.

    Args:
        engine: Engine on the temporary database.

    Returns:
        A factory whose sessions keep loaded values after commit.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _scalar(engine: AsyncEngine, sql: str, **parameters: object) -> object:
    """Run a one-value query.

    Args:
        engine: Engine on the temporary database.
        sql: The query.
        **parameters: Its bound parameters.

    Returns:
        The single value.
    """
    async with engine.connect() as connection:
        return (await connection.execute(text(sql), parameters)).scalar_one()


async def _station_id(engine: AsyncEngine, identity: str) -> UUID:
    """Read a charger's ID by its OCPP identity.

    Args:
        engine: Engine on the temporary database.
        identity: The OCPP identity.

    Returns:
        The station ID.
    """
    value = await _scalar(
        engine,
        "SELECT station_id FROM charging_stations WHERE ocpp_identity = :i",
        i=identity,
    )
    return UUID(str(value))


async def _gun_of(engine: AsyncEngine, station_id: UUID) -> tuple[UUID, UUID]:
    """Read the EVSE and connector of a one-gun charger.

    Args:
        engine: Engine on the temporary database.
        station_id: The charger.

    Returns:
        ``(evse_id, connector_id)``.
    """
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT e.evse_id, c.connector_id FROM charging_evses e "
                    "JOIN charging_connectors c ON c.evse_id = e.evse_id "
                    "WHERE e.station_id = :s"
                ),
                {"s": station_id},
            )
        ).one()
    return UUID(str(row.evse_id)), UUID(str(row.connector_id))


async def _insert_command(
    factory: async_sessionmaker[AsyncSession],
    station_id: UUID,
    *,
    ocpp_message_id: str | None,
) -> UUID:
    """Insert a RESET command, claimed when it has a message ID.

    Args:
        factory: Session factory on the temporary database.
        station_id: The charger.
        ocpp_message_id: The frame's message ID, ``None`` for a queued command.

    Returns:
        The command ID.
    """
    async with factory.begin() as db:
        command = await ocpp_state_repository.insert_station_command(
            db,
            station_id=station_id,
            command_type=StationCommandType.RESET,
            parameters={"reset_type": "Soft"},
            reason="Review test",
            ocpp_message_id=ocpp_message_id,
        )
    return command.command_id


async def _command_outcome(engine: AsyncEngine, command_id: UUID) -> tuple[str, str]:
    """Read a command's outcome and response status.

    Args:
        engine: Engine on the temporary database.
        command_id: The command.

    Returns:
        ``(outcome, response_status or "")``.
    """
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT outcome, response_status FROM charging_station_commands "
                    "WHERE command_id = :c"
                ),
                {"c": command_id},
            )
        ).one()
    return str(row.outcome), str(row.response_status or "")


# --- guards: the command table --------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_refuses_a_claimed_command_and_closes_a_queued_one(
    temporary_database: str,
) -> None:
    """Only a PENDING command with no message ID is cancelled; a claimed one stays."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    factory = _factory(engine)
    try:
        await _provision_station(engine, "REV-CANCEL", [1])
        station_id = await _station_id(engine, "REV-CANCEL")
        claimed_id = await _insert_command(factory, station_id, ocpp_message_id="m-1")
        queued_id = await _insert_command(factory, station_id, ocpp_message_id=None)

        async with factory.begin() as db:
            is_claimed_cancelled = await ocpp_state_repository.cancel_queued_command(
                db, claimed_id, response_status="Cancelled"
            )
            is_queued_cancelled = await ocpp_state_repository.cancel_queued_command(
                db, queued_id, response_status="Cancelled"
            )

        assert is_claimed_cancelled is False
        assert is_queued_cancelled is True
        assert await _command_outcome(engine, claimed_id) == ("PENDING", "")
        assert await _command_outcome(engine, queued_id) == ("NOT_SENT", "Cancelled")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_claim_takes_only_the_queued_commands_of_connected_chargers(
    temporary_database: str,
) -> None:
    """A gateway holding charger A never claims a command queued for charger B."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    factory = _factory(engine)
    try:
        await _provision_station(engine, "REV-A", [1])
        await _provision_station(engine, "REV-B", [1])
        station_a = await _station_id(engine, "REV-A")
        station_b = await _station_id(engine, "REV-B")
        command_a = await _insert_command(factory, station_a, ocpp_message_id=None)
        command_b = await _insert_command(factory, station_b, ocpp_message_id=None)

        async with factory.begin() as db:
            claimed = await ocpp_state_repository.claim_queued_commands(
                db, [station_a], limit=20
            )
            claimed_ids = [command.command_id for command in claimed]

        assert claimed_ids == [command_a]
        message_id = await _scalar(
            engine,
            "SELECT ocpp_message_id FROM charging_station_commands "
            "WHERE command_id = :c",
            c=command_b,
        )
        assert message_id is None
    finally:
        await engine.dispose()


# --- RV-CS6: a closed command rewritten by a late answer ---------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-CS6: set_command_answer/fail_command overwrite a closed command",
)
async def test_a_command_closed_by_the_sweep_is_not_rewritten_by_a_late_answer(
    temporary_database: str,
) -> None:
    """Once TIMEOUT, neither the charger's late Accepted nor an error changes the row."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    factory = _factory(engine)
    try:
        await _provision_station(engine, "REV-LATE", [1])
        station_id = await _station_id(engine, "REV-LATE")
        command_id = await _insert_command(factory, station_id, ocpp_message_id="m-1")
        async with factory.begin() as db:
            timed_out = await ocpp_state_repository.mark_stale_commands_timed_out(
                db, requested_before=datetime.now(timezone.utc) + timedelta(minutes=1)
            )
        assert timed_out == [command_id]

        async with factory.begin() as db:
            await ocpp_state_repository.set_command_answer(
                db,
                command_id,
                outcome=StationCommandOutcome.ACCEPTED,
                response_status="Accepted",
                answered_at=datetime.now(timezone.utc),
            )
            await ocpp_state_service.fail_command(
                db,
                command_id=command_id,
                outcome=StationCommandOutcome.ERROR,
                response_status="ConnectionClosed",
                answered_at=datetime.now(timezone.utc),
            )

        assert await _command_outcome(engine, command_id) == ("TIMEOUT", "")
    finally:
        await engine.dispose()


# --- RV-CS7: activation racing an abandon -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-CS7: activation overwrites a concurrent ABANDONED (lost update)",
)
async def test_a_start_and_an_abandon_of_one_scan_never_both_win(
    temporary_database: str,
) -> None:
    """Either the start wins and nothing was abandoned, or the abandon wins.

    The start reads the PENDING row; before it commits, another transaction
    abandons the scan (the failed-start path or the pending sweep) and commits.
    The outcome must be consistent: an ACTIVE session whose abandon (and bill
    void) also went through is the defect.
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    try:
        await _provision_station(engine, "REV-RACE", [1])
        station_id = await _station_id(engine, "REV-RACE")
        evse_id, connector_id = await _gun_of(engine, station_id)
        session_id = await _scan(engine, "REV-RACE", "RACE-TOKEN-1")

        async def abandon() -> bool:
            async with AsyncSession(engine) as db, db.begin():
                return await charging_sessions_service.abandon_pending_session(
                    db, session_id
                )

        is_start_refused = False
        starter = AsyncSession(engine, expire_on_commit=False)
        try:
            await starter.begin()
            try:
                await charging_sessions_service.activate_pending_session(
                    starter,
                    station_id=station_id,
                    evse_id=evse_id,
                    connector_id=connector_id,
                    id_token="RACE-TOKEN-1",
                    transaction_id="901",
                    started_at=datetime.now(timezone.utc),
                    meter_start_wh=Decimal(1000),
                )
            except ChargingSessionTokenError:
                is_start_refused = True
            abandon_task = asyncio.create_task(abandon())
            # Let the abandon run (or block on a row lock, once the fix exists).
            await asyncio.sleep(0.5)
            if is_start_refused:
                await starter.rollback()
            else:
                try:
                    await starter.commit()
                except ChargingSessionTokenError:
                    is_start_refused = True
            is_abandoned = await asyncio.wait_for(abandon_task, timeout=15)
        finally:
            await starter.close()

        final_status = await _scalar(
            engine,
            "SELECT status FROM charging_sessions WHERE session_id = :s",
            s=session_id,
        )
        if final_status == "ACTIVE":
            assert is_abandoned is False
        else:
            assert final_status == "ABANDONED"
            assert is_abandoned is True
    finally:
        await engine.dispose()


# --- RV-CS3: a retried StartTransaction ------------------------------------------


@pytest.mark.asyncio
async def test_a_retried_1_6_start_transaction_gets_the_same_transaction_id(
    temporary_database: str,
) -> None:
    """A start resent after a lost answer is accepted with the ID already given.

    Otherwise the charger adopts transactionId 0, its StopTransaction finds
    nothing, and the session (and the person's one open charge) stays ACTIVE.
    """
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    try:
        await _provision_station(engine, "REV-RETRY", [1])
        session_id = await _scan(engine, "REV-RETRY", "RETRY-TOKEN1")
        charge_point = OCPP16ChargePoint(
            "REV-RETRY",
            object(),  # type: ignore[arg-type]
            _factory(engine),
        )
        start = {
            "connector_id": 1,
            "id_tag": "RETRY-TOKEN1",
            "meter_start": 1000,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        first = await charge_point.on_start_transaction(**start)
        retried = await charge_point.on_start_transaction(**start)
        await charge_point.on_stop_transaction(
            meter_stop=21000,
            timestamp=datetime.now(timezone.utc).isoformat(),
            transaction_id=retried.transaction_id,
            reason="EVDisconnected",
        )

        assert first.id_tag_info["status"] == AuthorizationStatus.accepted
        assert retried.id_tag_info["status"] == AuthorizationStatus.accepted
        assert retried.transaction_id == first.transaction_id
        final_status = await _scalar(
            engine,
            "SELECT status FROM charging_sessions WHERE session_id = :s",
            s=session_id,
        )
        assert final_status == "COMPLETED"
    finally:
        await engine.dispose()
