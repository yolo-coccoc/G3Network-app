"""PostgreSQL tests of the real-firmware and orphan-session fixes (CO-16, CO-17, CE-28, CE-29).

They drive the OCPP 1.6J adapter's handlers directly (no WebSocket) against a
real database, so the session lookups, the row locks, the session-ended
billing hook and the station energy total run for real. They run only with
``RUN_DB_INTEGRATION=1`` on the temporary database of
``test_postgres_integration``.
"""

import os
from decimal import Decimal
from uuid import UUID

import pytest
from ocpp.v16 import call_result
from ocpp.v16.enums import AuthorizationStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.api.charging_session_flow as charging_session_flow
import app.domains.billing.router as billing_router
import app.domains.charging_sessions.report_service as session_report_service
import app.domains.charging_sessions.router as charging_sessions_router
import app.domains.charging_sessions.service as charging_sessions_service
from app.domains.billing.schemas import TariffCreateRequest, TariffVersionPublishRequest
from app.domains.charging_sessions.exceptions import ChargingSessionStateError
from app.domains.charging_sessions.schemas import (
    ChargingSessionForceCloseRequest,
    ChargingSessionScanRequest,
)
from app.domains.charging_sessions.types import STAFF_CLOSED_STOP_REASON, SessionStatus
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
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


async def _provision_priced_online_charger(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    identity: str,
) -> tuple[UUID, Principal]:
    """Provision a connected one-gun charger with a tariff, and a driver.

    Args:
        engine: Engine of the temporary database.
        session_factory: Factory for the service calls.
        identity: The charger's OCPP identity.

    Returns:
        The station ID and the driver who will scan it.
    """
    owner_id, location_id = await postgres_integration._provision_location(engine)
    station_id, _ = await postgres_integration._provision_station_at(
        engine, location_id, identity, ["Available"]
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE charging_station_state SET last_seen_at = now() "
                "WHERE station_id = :s"
            ),
            {"s": station_id},
        )
    (
        organization_id,
        user_id,
    ) = await postgres_integration._provision_organization_and_user(engine)
    admin = build_internal_principal()
    async with session_factory.begin() as db:
        tariff = await billing_router.create_tariff_endpoint(
            TariffCreateRequest(name="Default", organization_id=owner_id),
            principal=admin,
            db_session=db,
        )
        await billing_router.publish_tariff_version_endpoint(
            tariff.tariff_id,
            TariffVersionPublishRequest(
                price_per_kwh=4000,
                vat_rate_percent=Decimal(10),
                change_reason="Test price",
            ),
            principal=admin,
            db_session=db,
        )
    driver = build_principal(
        roles=frozenset({UserRole.DRIVER}),
        organization_id=organization_id,
        user_id=user_id,
    )
    return station_id, driver


async def _scan_and_start(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: OCPP16ChargePoint,
    identity: str,
    driver: Principal,
    *,
    meter_start: float,
) -> tuple[UUID, int]:
    """Scan the charger, then start the charge as a sloppy charger would.

    The charger upper-cases the idTag, sends a float ``meterStart`` and a
    timestamp without a timezone (CE-28, CO-17, CO-16).

    Returns:
        The session ID and the transaction ID the gateway gave.
    """
    async with session_factory.begin() as db:
        scan = await charging_session_flow.scan_charging_session_endpoint(
            ChargingSessionScanRequest(charger_code=identity, connector_number=1),
            principal=driver,
            db_session=db,
        )
    started = await adapter.on_start_transaction(
        connector_id=1,
        id_tag=scan.id_token.upper(),
        meter_start=meter_start,
        timestamp="2026-10-12T12:30:00",
    )
    assert started.id_tag_info["status"] == AuthorizationStatus.accepted
    return scan.session_id, started.transaction_id


@pytest.mark.asyncio
async def test_force_close_bills_the_last_reading_and_a_late_stop_changes_nothing(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CE-29 end to end: orphan → force-close → BILLED from the last reading.

    The charger then reconnects and sends its stop after all: it is confirmed
    (CO-16) and the closed session and its bill stay as they were. The driver
    no longer has a charge open, so the next scan is not blocked by it (only
    by the wallet, which the bill took below zero, BL-25).
    """
    from app.api.startup import register_all_hooks

    register_all_hooks()
    monkeypatch.setattr(settings, "BILLING_MIN_BALANCE_VND", Decimal(0))
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    identity = "TD-FORCE-1"
    try:
        station_id, driver = await _provision_priced_online_charger(
            engine, session_factory, identity
        )
        adapter = OCPP16ChargePoint(
            identity,
            object(),  # type: ignore[arg-type]
            session_factory,
            station_id=station_id,
        )
        session_id, transaction_id = await _scan_and_start(
            session_factory, adapter, identity, driver, meter_start=10_000.0
        )
        await adapter.on_meter_values(
            connector_id=1,
            transaction_id=transaction_id,
            meter_value=[
                {
                    "timestamp": "2026-10-12T12:40:00Z",
                    "sampled_value": [{"value": "15", "unit": "kWh"}],
                }
            ],
        )
        # The charger loses power: no StopTransaction. Staff close it.
        async with session_factory.begin() as db:
            closed = (
                await charging_sessions_router.force_close_charging_session_endpoint(
                    session_id,
                    ChargingSessionForceCloseRequest(reason="Charger lost power"),
                    principal=build_internal_principal(),
                    db=db,
                )
            )
        # A second force-close is refused: the session is final.
        with pytest.raises(ChargingSessionStateError):
            async with session_factory.begin() as db:
                await charging_sessions_service.force_close_session(
                    db,
                    session_id,
                    principal=build_internal_principal(),
                    reason="again",
                )
        # The charger comes back and sends its stop after all.
        late_stop = await adapter.on_stop_transaction(
            meter_stop=16_000,
            timestamp="2026-10-12T12:45:00Z",
            transaction_id=transaction_id,
            reason="PowerLoss",
        )
        async with engine.connect() as connection:
            session_row = (
                await connection.execute(
                    text(
                        "SELECT status, stop_reason, meter_stop_wh, started_at "
                        "FROM charging_sessions WHERE session_id = :s"
                    ),
                    {"s": session_id},
                )
            ).one()
            bill_row = (
                await connection.execute(
                    text(
                        "SELECT status, energy_wh, energy_source FROM "
                        "charging_session_bills WHERE session_id = :s"
                    ),
                    {"s": session_id},
                )
            ).one()
        async with session_factory.begin() as db:
            has_open_charge = await charging_sessions_service.has_open_session_by_user(
                db, driver.user_id
            )
    finally:
        await engine.dispose()

    assert closed.status is SessionStatus.COMPLETED
    assert closed.stop_reason == STAFF_CLOSED_STOP_REASON
    assert late_stop == call_result.StopTransaction()
    status, stop_reason, meter_stop_wh, started_at = session_row
    assert (status, stop_reason, meter_stop_wh) == (
        "COMPLETED",
        STAFF_CLOSED_STOP_REASON,
        None,
    )
    # The naive start time was read as UTC (CO-16).
    assert started_at.isoformat() == "2026-10-12T12:30:00+00:00"
    assert (bill_row.status, bill_row.energy_wh, bill_row.energy_source) == (
        "BILLED",
        Decimal(5000),
        "LAST_MEASUREMENT",
    )
    assert has_open_charge is False


@pytest.mark.asyncio
async def test_meter_going_backwards_is_left_out_of_the_station_total(
    temporary_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stop reading below the start reading no longer breaks the report.

    The session completes and its bill waits for review; the station total
    leaves it out instead of summing a negative energy (the API answered 500).
    """
    from datetime import datetime, timedelta, timezone

    from app.api.startup import register_all_hooks

    register_all_hooks()
    monkeypatch.setattr(settings, "BILLING_MIN_BALANCE_VND", Decimal(0))
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    identity = "TD-BACKWARDS-1"
    try:
        station_id, driver = await _provision_priced_online_charger(
            engine, session_factory, identity
        )
        adapter = OCPP16ChargePoint(
            identity,
            object(),  # type: ignore[arg-type]
            session_factory,
            station_id=station_id,
        )
        session_id, transaction_id = await _scan_and_start(
            session_factory, adapter, identity, driver, meter_start=5000
        )
        await adapter.on_stop_transaction(
            meter_stop=4000,
            timestamp="2026-10-12T13:00:00Z",
            transaction_id=transaction_id,
        )
        now = datetime.now(timezone.utc)
        async with session_factory.begin() as db:
            total = await session_report_service.resolve_station_energy_total(
                db,
                station_id=station_id,
                start_time=now - timedelta(days=30),
                end_time=now + timedelta(days=30),
            )
        async with engine.connect() as connection:
            bill_status = (
                await connection.execute(
                    text(
                        "SELECT status FROM charging_session_bills "
                        "WHERE session_id = :s"
                    ),
                    {"s": session_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert (total.total_energy_wh, total.session_count) == (Decimal(0), 0)
    assert bill_status == "ON_HOLD"
