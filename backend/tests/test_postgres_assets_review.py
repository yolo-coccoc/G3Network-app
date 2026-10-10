"""PostgreSQL review tests of the assets group: history, uniqueness, races.

Run only with ``RUN_DB_INTEGRATION=1``; each test gets its own temporary
database from the ``temporary_database`` fixture of
``tests/test_postgres_integration.py`` (created, migrated, dropped).

Guard tests (pass today): the history trigger records the actor and reason
that ``set_change_context`` set for a tracked vehicle update, and a later
transaction on the same pooled connection does not inherit them; a
soft-deleted truck's VIN and plate can be registered again; a customer of
another organization gets "not found" for a truck on the real repository.

Tests marked ``xfail(strict=True)`` assert the correct behaviour for review
findings RV-AS3 (concurrent transfers), RV-AS4 (a battery installation dated
before its previous removal) and RV-AS6 (a VIN that differs only in letter
case); each fails today and the fix forces the marker off.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.api.vehicle_transfer as vehicle_transfer
import app.domains.batteries.service as battery_service
import app.domains.vehicles.service as vehicle_service
from app.domains.batteries.schemas import (
    BatteryCreateRequest,
    BatteryInstallRequest,
    BatteryModelCreateRequest,
)
from app.domains.batteries.types import BatteryChemistry
from app.domains.identity.types import Principal
from app.domains.vehicles.exceptions import VehicleConflictError, VehicleNotFoundError
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.schemas import (
    VehicleCreateRequest,
    VehicleOwnershipTransferRequest,
    VehicleResponse,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.errors import ConflictError, DomainError, InvalidInputError
from app.libs.db.history import UNSPECIFIED_CHANGE_REASON
from tests.principals import ACTOR_USER_ID, build_internal_principal, build_principal
from tests.test_postgres_integration import (
    _insert_vehicle_parents,
    _integration_organization,
    temporary_database,  # noqa: F401 - the fixture is used by name below
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="Set RUN_DB_INTEGRATION=1 to run the PostgreSQL integration test",
)


def _session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the session factory every test uses (no expiry on commit)."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _random_vin() -> str:
    """Return a random, upper-case 17-character VIN."""
    return f"RV{uuid4().hex[:15]}".upper()


async def _create_vehicle(
    db: AsyncSession,
    principal: Principal,
    *,
    vehicle_model_id: UUID,
    vin: str,
    license_plate: str,
    acquired_at: datetime | None = None,
    organization_id: UUID | None = None,
) -> VehicleResponse:
    """Create a truck through the vehicles service.

    Args:
        db: Session of the caller's transaction.
        principal: The caller.
        vehicle_model_id: An existing catalog model.
        vin: The VIN.
        license_plate: The plate.
        acquired_at: The handover date; the request time when omitted.
        organization_id: The owner named by internal staff, if any.

    Returns:
        The created truck.
    """
    return await vehicle_service.create_vehicle(
        db,
        VehicleCreateRequest(
            organization_id=organization_id,
            license_plate=license_plate,
            vin=vin,
            vehicle_model_id=vehicle_model_id,
            year=2026,
            acquired_at=acquired_at,
        ),
        principal=principal,
    )


async def _create_battery(
    db: AsyncSession, staff: Principal, *, organization_id: UUID, acquired_at: datetime
) -> UUID:
    """Register a battery model and one battery of ``organization_id``.

    Args:
        db: Session of the caller's transaction.
        staff: An internal caller (battery writes are staff only).
        organization_id: The pack's owner.
        acquired_at: When the owner took the pack.

    Returns:
        The new battery's ID (in stock, not fitted).
    """
    battery_model = await battery_service.create_battery_model(
        db,
        BatteryModelCreateRequest(
            manufacturer="CATL",
            model_name=f"LFP-{uuid4().hex[:8]}",
            chemistry=BatteryChemistry.LFP,
        ),
    )
    battery = await battery_service.create_battery(
        db,
        BatteryCreateRequest(
            serial_number=f"REV-{uuid4().hex[:10]}",
            battery_model_id=battery_model.battery_model_id,
            organization_id=organization_id,
            acquired_at=acquired_at,
        ),
        principal=staff,
    )
    return battery.battery_id


@pytest.mark.asyncio
async def test_history_trigger_records_context_and_pooled_connection_forgets_it(
    temporary_database: str,  # noqa: F811
) -> None:
    """A tracked vehicle update records the caller and the typed reason; the
    next transaction on the same pooled connection has no context, so its
    change is recorded with no actor and the fallback reason (DM-29)."""
    engine = create_async_engine(temporary_database, pool_size=1, max_overflow=0)
    session_factory = _session_factory(engine)
    try:
        async with session_factory.begin() as db:
            organization_id, vehicle_model_id = await _insert_vehicle_parents(db)
        customer = build_principal(organization_id=organization_id)
        async with session_factory.begin() as db:
            vehicle = await _create_vehicle(
                db,
                customer,
                vehicle_model_id=vehicle_model_id,
                vin=_random_vin(),
                license_plate="REV-HIS-01",
            )

        async with session_factory.begin() as db:
            first_backend_pid = (
                await db.execute(text("SELECT pg_backend_pid()"))
            ).scalar()
            await vehicle_service.update_vehicle(
                db,
                vehicle.vehicle_id,
                VehicleUpdateRequest(
                    status=VehicleStatus.INACTIVE, status_reason="Brake repair"
                ),
                principal=customer,
            )

        async with session_factory.begin() as db:
            second_backend_pid = (
                await db.execute(text("SELECT pg_backend_pid()"))
            ).scalar()
            leftover_reason = (
                await db.execute(
                    text("SELECT current_setting('app.change_reason', true)")
                )
            ).scalar()
            vehicle_record = await db.get(VehicleModel, vehicle.vehicle_id)
            assert vehicle_record is not None
            vehicle_record.year = 2024
            await db.flush()

        async with session_factory() as db:
            history_rows = (
                await db.execute(
                    text(
                        "SELECT status, year, changed_by, change_reason "
                        "FROM vehicle_history WHERE vehicle_id = :vehicle_id "
                        "ORDER BY history_id"
                    ),
                    {"vehicle_id": vehicle.vehicle_id},
                )
            ).all()

        assert first_backend_pid == second_backend_pid
        assert leftover_reason in (None, "")
        assert [
            (row.status, row.year, row.changed_by, row.change_reason)
            for row in history_rows
        ] == [
            ("ACTIVE", 2026, ACTOR_USER_ID, "Brake repair"),
            ("INACTIVE", 2026, None, UNSPECIFIED_CHANGE_REASON),
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_soft_deleted_vehicle_frees_its_vin_and_plate_for_a_new_truck(
    temporary_database: str,  # noqa: F811
) -> None:
    """Uniqueness is among live trucks only: after a soft delete the same VIN
    and plate can be registered again, as a new row."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = _session_factory(engine)
    vin = _random_vin()
    try:
        async with session_factory.begin() as db:
            organization_id, vehicle_model_id = await _insert_vehicle_parents(db)
        customer = build_principal(organization_id=organization_id)
        async with session_factory.begin() as db:
            first_vehicle = await _create_vehicle(
                db,
                customer,
                vehicle_model_id=vehicle_model_id,
                vin=vin,
                license_plate="REV-DEL-01",
            )
        async with session_factory.begin() as db:
            await vehicle_service.soft_delete_vehicle(
                db, first_vehicle.vehicle_id, principal=customer, reason="Scrapped"
            )
        async with session_factory.begin() as db:
            second_vehicle = await _create_vehicle(
                db,
                customer,
                vehicle_model_id=vehicle_model_id,
                vin=vin,
                license_plate="REV-DEL-01",
            )

        assert second_vehicle.vehicle_id != first_vehicle.vehicle_id
        assert second_vehicle.vin == vin
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_customer_gets_not_found_for_another_organizations_vehicle_on_postgres(
    temporary_database: str,  # noqa: F811
) -> None:
    """On the real repository a customer of organization B cannot read, edit
    or delete A's truck; internal staff can read it."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = _session_factory(engine)
    try:
        async with session_factory.begin() as db:
            owner_id, vehicle_model_id = await _insert_vehicle_parents(db)
            stranger_id = await _integration_organization(db)
        async with session_factory.begin() as db:
            vehicle = await _create_vehicle(
                db,
                build_principal(organization_id=owner_id),
                vehicle_model_id=vehicle_model_id,
                vin=_random_vin(),
                license_plate="REV-ISO-01",
            )
        stranger = build_principal(organization_id=stranger_id)

        async with session_factory() as db:
            with pytest.raises(VehicleNotFoundError):
                await vehicle_service.get_vehicle(
                    db, vehicle.vehicle_id, principal=stranger
                )
            with pytest.raises(VehicleNotFoundError):
                await vehicle_service.update_vehicle(
                    db,
                    vehicle.vehicle_id,
                    VehicleUpdateRequest(year=2020),
                    principal=stranger,
                )
            with pytest.raises(VehicleNotFoundError):
                await vehicle_service.soft_delete_vehicle(
                    db, vehicle.vehicle_id, principal=stranger
                )
            staff_view = await vehicle_service.get_vehicle(
                db, vehicle.vehicle_id, principal=build_internal_principal()
            )

        assert staff_view.organization_id == owner_id
        assert staff_view.year == 2026
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RV-AS3: concurrent transfers overwrite each other (no row lock)",
)
async def test_concurrent_transfers_leave_consistent_owner_periods_and_battery(
    temporary_database: str,  # noqa: F811
) -> None:
    """Two staff transfer the same truck at once (A to B dated yesterday, A to
    C dated the day before). Whatever wins, every ownership period has a
    positive length and the seller's battery ends with the truck's owner."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = _session_factory(engine)
    staff = build_internal_principal()
    now = datetime.now(timezone.utc)
    try:
        async with session_factory.begin() as db:
            seller_id, vehicle_model_id = await _insert_vehicle_parents(db)
            first_buyer_id = await _integration_organization(db)
            second_buyer_id = await _integration_organization(db)
        async with session_factory.begin() as db:
            vehicle = await _create_vehicle(
                db,
                staff,
                vehicle_model_id=vehicle_model_id,
                vin=_random_vin(),
                license_plate="REV-RACE-1",
                acquired_at=now - timedelta(days=90),
                organization_id=seller_id,
            )
            battery_id = await _create_battery(
                db,
                staff,
                organization_id=seller_id,
                acquired_at=now - timedelta(days=90),
            )
            await battery_service.install_battery(
                db,
                battery_id,
                BatteryInstallRequest(
                    vehicle_id=vehicle.vehicle_id,
                    installed_at=now - timedelta(days=90),
                ),
                principal=staff,
            )

        async with session_factory() as first_db, session_factory() as second_db:
            await vehicle_transfer.transfer_vehicle_ownership_endpoint(
                vehicle_id=vehicle.vehicle_id,
                vehicle_ownership_transfer_request=VehicleOwnershipTransferRequest(
                    organization_id=first_buyer_id,
                    acquired_at=now - timedelta(days=1),
                    reason="Sold to the first buyer",
                ),
                principal=staff,
                db_session=first_db,
            )
            # The second transfer reads the truck before the first commits,
            # then waits on the row lock of its own UPDATE.
            second_transfer = asyncio.create_task(
                vehicle_transfer.transfer_vehicle_ownership_endpoint(
                    vehicle_id=vehicle.vehicle_id,
                    vehicle_ownership_transfer_request=VehicleOwnershipTransferRequest(
                        organization_id=second_buyer_id,
                        acquired_at=now - timedelta(days=2),
                        reason="Sold to the second buyer",
                    ),
                    principal=staff,
                    db_session=second_db,
                )
            )
            await asyncio.sleep(1.0)
            await first_db.commit()
            try:
                await asyncio.wait_for(second_transfer, timeout=15)
                await second_db.commit()
            except DomainError:
                await second_db.rollback()

        async with session_factory() as db:
            periods = (
                await db.execute(
                    text(
                        "SELECT owned_from, owned_until FROM vehicle_ownership_periods "
                        "WHERE vehicle_id = :vehicle_id"
                    ),
                    {"vehicle_id": vehicle.vehicle_id},
                )
            ).all()
            owners = (
                await db.execute(
                    text(
                        "SELECT v.organization_id AS vehicle_owner, "
                        "b.organization_id AS battery_owner "
                        "FROM vehicles v JOIN batteries b ON b.vehicle_id = v.vehicle_id "
                        "WHERE v.vehicle_id = :vehicle_id"
                    ),
                    {"vehicle_id": vehicle.vehicle_id},
                )
            ).one()

        assert all(
            row.owned_until is None or row.owned_until > row.owned_from
            for row in periods
        )
        assert owners.battery_owner == owners.vehicle_owner
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-AS4: installed_at may predate the pack's previous removal",
)
async def test_battery_install_dated_before_its_previous_removal_is_refused(
    temporary_database: str,  # noqa: F811
) -> None:
    """A pack fitted to truck X ten days ago and removed today cannot be
    recorded as fitted to truck Y five days ago: the installation periods of
    one pack never overlap."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = _session_factory(engine)
    staff = build_internal_principal()
    now = datetime.now(timezone.utc)
    try:
        async with session_factory.begin() as db:
            owner_id, vehicle_model_id = await _insert_vehicle_parents(db)
        async with session_factory.begin() as db:
            first_truck = await _create_vehicle(
                db,
                staff,
                vehicle_model_id=vehicle_model_id,
                vin=_random_vin(),
                license_plate="REV-BAT-01",
                acquired_at=now - timedelta(days=60),
                organization_id=owner_id,
            )
            second_truck = await _create_vehicle(
                db,
                staff,
                vehicle_model_id=vehicle_model_id,
                vin=_random_vin(),
                license_plate="REV-BAT-02",
                acquired_at=now - timedelta(days=60),
                organization_id=owner_id,
            )
            battery_id = await _create_battery(
                db,
                staff,
                organization_id=owner_id,
                acquired_at=now - timedelta(days=60),
            )
        async with session_factory.begin() as db:
            await battery_service.install_battery(
                db,
                battery_id,
                BatteryInstallRequest(
                    vehicle_id=first_truck.vehicle_id,
                    installed_at=now - timedelta(days=10),
                ),
                principal=staff,
            )
        async with session_factory.begin() as db:
            await battery_service.remove_battery_from_vehicle(
                db, battery_id, principal=staff
            )

        async with session_factory() as db:
            with pytest.raises((InvalidInputError, ConflictError)):
                await battery_service.install_battery(
                    db,
                    battery_id,
                    BatteryInstallRequest(
                        vehicle_id=second_truck.vehicle_id,
                        installed_at=now - timedelta(days=5),
                    ),
                    principal=staff,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="RV-AS6: a VIN differing only in letter case is a second truck",
)
async def test_vin_differing_only_in_letter_case_is_a_conflict(
    temporary_database: str,  # noqa: F811
) -> None:
    """``lj1...`` and ``LJ1...`` are the same chassis number, so registering
    the lower-case form next to a live upper-case one is a conflict."""
    engine = create_async_engine(temporary_database, poolclass=NullPool)
    session_factory = _session_factory(engine)
    vin = _random_vin()
    try:
        async with session_factory.begin() as db:
            organization_id, vehicle_model_id = await _insert_vehicle_parents(db)
        customer = build_principal(organization_id=organization_id)
        async with session_factory.begin() as db:
            await _create_vehicle(
                db,
                customer,
                vehicle_model_id=vehicle_model_id,
                vin=vin,
                license_plate="REV-CASE-1",
            )

        async with session_factory() as db:
            with pytest.raises(VehicleConflictError):
                await _create_vehicle(
                    db,
                    customer,
                    vehicle_model_id=vehicle_model_id,
                    vin=vin.lower(),
                    license_plate="REV-CASE-2",
                )
    finally:
        await engine.dispose()
