"""PostgreSQL review tests for operations: ingestion, driving sessions, trips, inbox.

Covers the reviewed defects that need a real database (RV-OP2, RV-OP6, RV-OP7,
RV-OP8) and guards rules that hold today: one open driving session per driver
and per truck under concurrent check-ins, and an inbox that never shows or
changes another organization's alert.

Each `xfail(strict=True)` test asserts the correct behaviour that a reviewed
defect breaks today; once fixed it passes, strict mode reports it and the
marker must be removed. Every test builds its own temporary database through
the shared `temporary_database` fixture and is skipped unless
``RUN_DB_INTEGRATION=1``.
"""

import asyncio
import dataclasses
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.domains.drivers.repository as driver_repository
import app.domains.drivers.service as driver_service
import app.domains.drivers.trip_service as trip_service
import app.domains.identity.service as identity_service
import app.domains.notifications.repository as notification_repository
import app.domains.notifications.service as notifications_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
from app.domains.drivers.exceptions import (
    DrivingSessionConflictError,
    TripInProgressConflictError,
    TripStateConflictError,
)
from app.domains.drivers.models import DriverModel, DrivingSessionModel
from app.domains.drivers.schemas import (
    DrivingSessionCheckInRequest,
    DrivingSessionCheckOutRequest,
    TripPlanRequest,
    TripStartRequest,
)
from app.domains.drivers.types import CheckInMethod, DrivingSessionEndCause
from app.domains.identity.types import (
    OrganizationSettingsReference,
    Principal,
    UserRole,
)
from app.domains.notifications.exceptions import (
    NotificationNotFoundError,
    NotificationRecipientNotFoundError,
)
from app.domains.notifications.types import (
    NotificationListOrder,
    NotificationSeverity,
    NotificationType,
)
from app.domains.telematics.models import TelematicModel
from app.domains.telemetry.types import VehicleLiveStatusReference
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.types import VehicleStatus
from tests.principals import build_principal
from tests.test_postgres_integration import (
    _insert_vehicle_parents,
    _integration_driver,
    _integration_envelope,
    _provision_organization_and_user,
    _seed_vehicle_readings,
    temporary_database,  # noqa: F401  (pytest fixture, used by name)
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="Set RUN_DB_INTEGRATION=1 to run the PostgreSQL integration test",
)

# How long a concurrent transaction in these tests waits for a row lock before
# giving up: a fix that serializes the race with a lock must not hang the test.
LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '2s'"


def _session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Build a session factory on a fresh engine without a connection pool.

    Args:
        database_url: URL of the temporary database.

    Returns:
        The factory; dispose of its engine with `factory.kw["bind"].dispose()`.
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _dispose(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Dispose of the engine behind a session factory.

    Args:
        session_factory: A factory built by `_session_factory`.
    """
    await session_factory.kw["bind"].dispose()


def _driver_principal(driver: DriverModel, organization_id: UUID) -> Principal:
    """Build the DRIVER-only principal of a driver profile's membership.

    Args:
        driver: The driver profile.
        organization_id: The organization the membership belongs to.

    Returns:
        A principal whose membership is the profile's.
    """
    return dataclasses.replace(
        build_principal(
            roles=frozenset({UserRole.DRIVER}), organization_id=organization_id
        ),
        membership_id=driver.membership_id,
    )


async def _telematic_serial(db: AsyncSession, vehicle_id: UUID) -> str:
    """Read the serial of the device mounted on a truck.

    Args:
        db: Current session.
        vehicle_id: The truck.

    Returns:
        The device serial.
    """
    return (
        await db.execute(
            select(TelematicModel.telematic_serial).where(
                TelematicModel.vehicle_id == vehicle_id
            )
        )
    ).scalar_one()


async def _end_cause_of(db: AsyncSession, driving_session_id: UUID) -> str | None:
    """Read the stored end cause of a driving session.

    Args:
        db: Current session.
        driving_session_id: The session.

    Returns:
        The end cause value, or `None` while the session is open.
    """
    return (
        await db.execute(
            select(DrivingSessionModel.end_cause).where(
                DrivingSessionModel.driving_session_id == driving_session_id
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_repeated_reading_from_one_device_is_skipped_not_fatal(
    temporary_database: str,  # noqa: F811
) -> None:
    """A device that sends the same reading twice gets the second one skipped.

    A plain INSERT used to hit `uq_telemetry_telematic_recorded_at`, the worker
    re-raised and the ingestion process exited for every vehicle (RV-OP2).
    """
    session_factory = _session_factory(temporary_database)
    try:
        async with session_factory.begin() as db:
            truck = await _seed_vehicle_readings(db, [])
            serial = await _telematic_serial(db, truck.vehicle_id)
        recorded_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        async with session_factory.begin() as db:
            first = await telemetry_service.process_message(
                db, _integration_envelope(serial, recorded_at, 10.8, 106.7)
            )
        async with session_factory.begin() as db:
            second = await telemetry_service.process_message(
                db, _integration_envelope(serial, recorded_at, 10.8, 106.7)
            )

        assert first["processed"] == 1
        assert second["processed"] == 0
    finally:
        await _dispose(session_factory)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-OP6: auto-end reads movement by device recorded_at",
)
async def test_auto_end_keeps_a_moving_truck_whose_device_clock_runs_slow(
    temporary_database: str,  # noqa: F811
) -> None:
    """A truck that kept moving is never auto-ended, whatever its device clock says.

    The device clock is four hours slow: every moving sample's `recorded_at`
    falls before the check-in, so the sweep finds no movement and ends the
    shift while the truck drives.
    """
    session_factory = _session_factory(temporary_database)
    now = datetime.now(timezone.utc)
    started_at = now - timedelta(hours=3)
    try:
        async with session_factory.begin() as db:
            moving_samples: list[dict[str, object]] = []
            for minute in range(5, 180, 10):
                received_at = started_at + timedelta(minutes=minute)
                moving_samples.append(
                    {
                        "recorded_at": received_at - timedelta(hours=4),
                        "received_at": received_at,
                        "soc_percent": 70.0,
                        "speed_kmh": 50.0,
                    }
                )
            truck = await _seed_vehicle_readings(db, moving_samples)
            driver = await _integration_driver(
                db, license_number="LIC-SLOW", organization_id=truck.organization_id
            )
            await driver_repository.insert_session(
                db,
                organization_id=truck.organization_id,
                driver_id=driver.driver_id,
                vehicle_id=truck.vehicle_id,
                check_in_method=CheckInMethod.APP,
                check_in_location=None,
                started_at=started_at,
            )

        async with session_factory.begin() as db:
            sweep = await driver_service.end_idle_driving_sessions(db, now=now)

        assert (sweep.checked, sweep.ended) == (1, 0)
    finally:
        await _dispose(session_factory)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-OP7: the auto-end sweep overwrites a session closed meanwhile",
)
async def test_auto_end_sweep_keeps_a_check_out_committed_during_the_sweep(
    temporary_database: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check-out that commits while the sweep runs stays CHECKED_OUT.

    The sweep reads every open session at its start; the driver checks out
    (another transaction) before the sweep reaches the session. Ending it
    again would rewrite the cause and the end time. A fix that locks the
    session instead makes the check-out wait (it then times out here), which
    is also correct.
    """
    session_factory = _session_factory(temporary_database)
    now = datetime.now(timezone.utc)
    started_at = now - timedelta(hours=3)
    try:
        async with session_factory.begin() as db:
            truck = await _seed_vehicle_readings(
                db,
                [
                    {
                        "recorded_at": started_at + timedelta(minutes=minute),
                        "soc_percent": 70.0,
                        "speed_kmh": 0.0,
                    }
                    for minute in range(5, 180, 30)
                ],
            )
            driver = await _integration_driver(
                db, license_number="LIC-RACE", organization_id=truck.organization_id
            )
            open_session = await driver_repository.insert_session(
                db,
                organization_id=truck.organization_id,
                driver_id=driver.driver_id,
                vehicle_id=truck.vehicle_id,
                check_in_method=CheckInMethod.APP,
                check_in_location=None,
                started_at=started_at,
            )
        driver_principal = _driver_principal(driver, truck.organization_id)
        hook_fired: list[bool] = []
        checked_out: list[bool] = []
        original_settings = identity_service.resolve_organization_settings

        async def check_out_during_sweep(
            db_session: AsyncSession, organization_id: UUID
        ) -> OrganizationSettingsReference | None:
            """Commit the driver's check-out in another transaction, once."""
            if not hook_fired:
                hook_fired.append(True)
                try:
                    async with session_factory.begin() as other:
                        await other.execute(text(LOCK_TIMEOUT_SQL))
                        await driver_service.check_out_driver(
                            other,
                            DrivingSessionCheckOutRequest(),
                            principal=driver_principal,
                        )
                    checked_out.append(True)
                except DBAPIError:
                    checked_out.append(False)
            return await original_settings(db_session, organization_id)

        monkeypatch.setattr(
            identity_service, "resolve_organization_settings", check_out_during_sweep
        )
        async with session_factory.begin() as db:
            await driver_service.end_idle_driving_sessions(db, now=now)

        async with session_factory() as db:
            end_cause = await _end_cause_of(db, open_session.driving_session_id)
        if checked_out == [True]:
            assert end_cause == DrivingSessionEndCause.CHECKED_OUT.value
    finally:
        await _dispose(session_factory)


async def _two_checked_in_drivers(
    db: AsyncSession,
) -> tuple[UUID, DriverModel, DriverModel]:
    """Insert one organization with two trucks and two drivers, each checked in.

    Args:
        db: Session of the caller's transaction.

    Returns:
        ``(organization_id, first_driver, second_driver)``.
    """
    organization_id, vehicle_model_id = await _insert_vehicle_parents(
        db, nominal_battery_capacity_kwh=Decimal("200.0")
    )
    drivers: list[DriverModel] = []
    for index in range(2):
        truck = VehicleModel(
            vehicle_id=uuid4(),
            organization_id=organization_id,
            vehicle_model_id=vehicle_model_id,
            license_plate=f"IT-TRIP-{index}-{uuid4().hex[:6]}",
            vin=f"IT{uuid4().hex[:15]}".upper(),
            year=2026,
            status=VehicleStatus.ACTIVE,
        )
        db.add(truck)
        await db.flush()
        driver = await _integration_driver(
            db, license_number=f"LIC-TRIP-{index}", organization_id=organization_id
        )
        await driver_repository.insert_session(
            db,
            organization_id=organization_id,
            driver_id=driver.driver_id,
            vehicle_id=truck.vehicle_id,
            check_in_method=CheckInMethod.APP,
            check_in_location=None,
            started_at=datetime.now(timezone.utc),
        )
        drivers.append(driver)
    return organization_id, drivers[0], drivers[1]


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-OP7: two drivers can both start one unassigned planned trip",
)
async def test_a_trip_started_meanwhile_by_another_driver_cannot_be_started_again(
    temporary_database: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Of two drivers starting the same unassigned trip at once, only one wins.

    The second driver's start commits after the first driver's status check
    and before its write; the first must then get a conflict instead of
    silently taking the trip over.
    """
    session_factory = _session_factory(temporary_database)
    try:
        async with session_factory.begin() as db:
            organization_id, first, second = await _two_checked_in_drivers(db)
            manager = build_principal(
                roles=frozenset({UserRole.FLEET_MANAGER}),
                organization_id=organization_id,
            )
            planned = await trip_service.plan_trip(
                db,
                TripPlanRequest(
                    origin_name="Depot",
                    destination_name="Port",
                    planned_start_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ),
                principal=manager,
            )
        first_principal = _driver_principal(first, organization_id)
        second_principal = _driver_principal(second, organization_id)
        # Set before the inner start runs: the inner start reads the truck
        # through this same hook and must not start yet another transaction.
        hook_fired: list[bool] = []
        inner_started: list[bool] = []
        original_reading: Callable[..., Awaitable[VehicleLiveStatusReference | None]]
        original_reading = driver_service.read_recent_truck_reading

        async def start_meanwhile(
            db_session: AsyncSession, vehicle_id: UUID, *, now: datetime
        ) -> VehicleLiveStatusReference | None:
            """Let the second driver start and commit the trip, once."""
            if not hook_fired:
                hook_fired.append(True)
                try:
                    async with session_factory.begin() as other:
                        await other.execute(text(LOCK_TIMEOUT_SQL))
                        await trip_service.start_trip(
                            other,
                            planned.trip_id,
                            TripStartRequest(),
                            principal=second_principal,
                        )
                    inner_started.append(True)
                except DBAPIError:
                    inner_started.append(False)
            return await original_reading(db_session, vehicle_id, now=now)

        monkeypatch.setattr(
            driver_service, "read_recent_truck_reading", start_meanwhile
        )
        first_got_conflict = False
        try:
            async with session_factory.begin() as db:
                await trip_service.start_trip(
                    db, planned.trip_id, TripStartRequest(), principal=first_principal
                )
        except (TripStateConflictError, TripInProgressConflictError):
            first_got_conflict = True

        if inner_started == [True]:
            assert first_got_conflict
    finally:
        await _dispose(session_factory)


async def _window_distance_km(
    session_factory: async_sessionmaker[AsyncSession],
    odometer_readings: list[float | None],
) -> float:
    """Seed one reading per minute with the given odometers and fold the window.

    Args:
        session_factory: Factory on the temporary database.
        odometer_readings: Odometer (km) of each reading, ``None`` for a
            reading that carries none.

    Returns:
        The window's distance from `get_vehicle_window_summary`.
    """
    start = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    async with session_factory.begin() as db:
        truck = await _seed_vehicle_readings(
            db,
            [
                {
                    "recorded_at": start + timedelta(minutes=index),
                    "soc_percent": 80.0,
                    "odometer_km": odometer_km,
                }
                for index, odometer_km in enumerate(odometer_readings)
            ],
        )
        summary = await telemetry_repository.get_vehicle_window_summary(
            db,
            vehicle_id=truck.vehicle_id,
            start_time=start,
            end_time=start + timedelta(hours=1),
        )
    return summary.distance_km


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-OP8: lag() over a reading without odometer drops the distance",
)
async def test_distance_bridges_a_reading_without_odometer(
    temporary_database: str,  # noqa: F811
) -> None:
    """Odometer 100, (none), 110 km is a 10 km drive, not 0 km."""
    session_factory = _session_factory(temporary_database)
    try:
        distance_km = await _window_distance_km(session_factory, [100.0, None, 110.0])

        assert distance_km == pytest.approx(10.0)
    finally:
        await _dispose(session_factory)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-OP8: a single forward odometer glitch is counted as distance",
)
async def test_distance_ignores_a_single_odometer_glitch(
    temporary_database: str,  # noqa: F811
) -> None:
    """Odometer 100, 999999 (glitch), 105 km over two minutes is not ~1M km."""
    session_factory = _session_factory(temporary_database)
    try:
        distance_km = await _window_distance_km(
            session_factory, [100.0, 999_999.0, 105.0]
        )

        assert distance_km < 100.0
    finally:
        await _dispose(session_factory)


async def _check_in_by_portal(
    session_factory: async_sessionmaker[AsyncSession],
    manager: Principal,
    driver_id: UUID,
    vehicle_vin: str,
) -> Exception | None:
    """Check a driver in from the portal in a transaction of its own.

    Args:
        session_factory: Factory on the temporary database.
        manager: The fleet manager acting.
        driver_id: The driver checked in.
        vehicle_vin: The truck.

    Returns:
        `None` when the check-in committed, else the conflict it raised.
    """
    try:
        async with session_factory.begin() as db:
            await driver_service.check_in_driver(
                db,
                DrivingSessionCheckInRequest(
                    driver_id=driver_id,
                    vehicle_vin=vehicle_vin,
                    check_in_method=CheckInMethod.PORTAL,
                ),
                principal=manager,
            )
    except DrivingSessionConflictError as conflict:
        return conflict
    return None


@pytest.mark.asyncio
async def test_concurrent_check_ins_leave_one_open_session_per_driver_and_truck_guard(
    temporary_database: str,  # noqa: F811
) -> None:
    """Guard: racing check-ins never give a driver two trucks or a truck two drivers.

    Both pairs run at once in separate transactions; the partial unique
    indexes let exactly one of each pair commit and the other gets a 409.
    """
    session_factory = _session_factory(temporary_database)
    try:
        async with session_factory.begin() as db:
            organization_id, vehicle_model_id = await _insert_vehicle_parents(db)
            trucks: list[VehicleModel] = []
            for index in range(3):
                truck = VehicleModel(
                    vehicle_id=uuid4(),
                    organization_id=organization_id,
                    vehicle_model_id=vehicle_model_id,
                    license_plate=f"IT-RACE-{index}-{uuid4().hex[:6]}",
                    vin=f"IT{uuid4().hex[:15]}".upper(),
                    year=2026,
                    status=VehicleStatus.ACTIVE,
                )
                db.add(truck)
                trucks.append(truck)
            await db.flush()
            alice = await _integration_driver(
                db, license_number="LIC-RACE-A", organization_id=organization_id
            )
            bob = await _integration_driver(
                db, license_number="LIC-RACE-B", organization_id=organization_id
            )
            carol = await _integration_driver(
                db, license_number="LIC-RACE-C", organization_id=organization_id
            )
        manager = build_principal(
            roles=frozenset({UserRole.FLEET_MANAGER}), organization_id=organization_id
        )

        one_driver_two_trucks = await asyncio.gather(
            _check_in_by_portal(
                session_factory, manager, alice.driver_id, trucks[0].vin
            ),
            _check_in_by_portal(
                session_factory, manager, alice.driver_id, trucks[1].vin
            ),
        )
        two_drivers_one_truck = await asyncio.gather(
            _check_in_by_portal(session_factory, manager, bob.driver_id, trucks[2].vin),
            _check_in_by_portal(
                session_factory, manager, carol.driver_id, trucks[2].vin
            ),
        )

        async with session_factory() as db:
            open_sessions = (
                (
                    await db.execute(
                        select(DrivingSessionModel).where(
                            DrivingSessionModel.ended_at.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
        for outcomes in (one_driver_two_trucks, two_drivers_one_truck):
            assert sorted(outcome is None for outcome in outcomes) == [False, True]
        assert len(open_sessions) == 2
        assert len({row.driver_id for row in open_sessions}) == 2
        assert len({row.vehicle_id for row in open_sessions}) == 2
    finally:
        await _dispose(session_factory)


@pytest.mark.asyncio
async def test_inbox_never_shows_or_marks_another_organizations_alert_guard(
    temporary_database: str,  # noqa: F811
) -> None:
    """Guard: a person of organization A cannot see, read or mark B's alert.

    Neither as a DRIVER (inbox) nor as an ORG_ADMIN (organization list), by
    ID or in bulk; B's recipient keeps its unseen, unread state.
    """
    session_factory = _session_factory(temporary_database)
    try:
        organization_a, user_a = await _provision_organization_and_user(
            session_factory.kw["bind"]
        )
        organization_b, user_b = await _provision_organization_and_user(
            session_factory.kw["bind"]
        )
        async with session_factory.begin() as db:
            notification_record = await notification_repository.insert(
                db,
                organization_id=organization_b,
                notification_type=NotificationType.BATTERY_ALERT,
                severity=NotificationSeverity.WARNING,
                vehicle_id=None,
                subject_type=None,
                subject_id=None,
                title="Battery at 18%",
                body="Organization B's truck.",
                payload={"soc": 18.0},
            )
            await notifications_service.add_notification_recipients(
                db, notification_record.notification_id, [user_b]
            )
        notification_id = notification_record.notification_id
        driver_a = build_principal(
            roles=frozenset({UserRole.DRIVER}),
            organization_id=organization_a,
            user_id=user_a,
        )
        admin_a = build_principal(
            roles=frozenset({UserRole.ORG_ADMIN}),
            organization_id=organization_a,
            user_id=user_a,
        )

        async with session_factory.begin() as db:
            for principal in (driver_a, admin_a):
                with pytest.raises(NotificationRecipientNotFoundError):
                    await notifications_service.mark_notification_read(
                        db, notification_id, principal=principal
                    )
                with pytest.raises(NotificationNotFoundError):
                    await notifications_service.get_notification(
                        db, notification_id, principal=principal
                    )
            listed_ids: list[int] = []
            for principal, organization_filter, order in (
                (driver_a, None, NotificationListOrder.ASC),
                (driver_a, None, NotificationListOrder.DESC),
                (admin_a, None, NotificationListOrder.ASC),
                (admin_a, organization_b, NotificationListOrder.ASC),
            ):
                listed = await notifications_service.list_notifications(
                    db,
                    principal=principal,
                    after_id=0,
                    limit=50,
                    organization_id=organization_filter,
                    order=order,
                )
                listed_ids.extend(item.notification_id for item in listed.notifications)
            await notifications_service.mark_all_notifications_seen(
                db, principal=driver_a
            )
            await notifications_service.mark_all_notifications_read(
                db, principal=driver_a
            )

        async with session_factory() as db:
            recipient_b = await notification_repository.find_recipient(
                db, notification_id, user_b
            )
        assert notification_id not in listed_ids
        assert recipient_b is not None
        assert (recipient_b.seen_at, recipient_b.read_at) == (None, None)
    finally:
        await _dispose(session_factory)
