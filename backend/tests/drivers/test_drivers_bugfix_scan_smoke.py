"""Smoke tests for the drivers fixes of the backend bug-fix scan (A2, A4-A7).

Check-in reach and fleet limit for managed check-ins, the DRIVER role at
check-in and its revocation, a planned trip on another organization's truck,
and the actor of a trip closed by a truck transfer.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.repository as driver_repository
import app.domains.drivers.service as driver_service
import app.domains.drivers.trip_service as trip_service
import app.domains.identity.member_service as identity_member_service
import app.domains.identity.repository as identity_repository
import app.domains.identity.service as identity_service
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.domains.drivers.exceptions import (
    DriverRoleMissingError,
    DriverVehicleNotFoundError,
    DrivingSessionNotFoundError,
    TripOrganizationMismatchError,
)
from app.domains.drivers.models import DriverModel, DrivingSessionModel, TripModel
from app.domains.drivers.schemas import (
    DrivingSessionCheckInRequest,
    DrivingSessionCheckOutRequest,
    TripStartRequest,
)
from app.domains.drivers.types import (
    CheckInMethod,
    CheckInWarning,
    DrivingSessionEndCause,
    TripStatus,
)
from app.domains.identity.models import MembershipModel
from app.domains.identity.types import Principal, UserRole
from app.domains.vehicles.types import VehicleReference
from app.libs.common.clock import utc_now
from tests.builders import (
    build_driver_record,
    build_driving_session_record,
    build_person_reference,
    fake_db_session,
)
from tests.principals import (
    DEFAULT_ORGANIZATION_ID,
    OTHER_ORGANIZATION_ID,
    build_principal,
)


@pytest.fixture(autouse=True)
def stub_shared_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the lookups every check-in makes: an eligible person holding DRIVER,
    no open session, no running trip, no T-Box reading."""

    async def resolve_person(db: AsyncSession, membership_id: UUID) -> object:
        return build_person_reference(membership_id=membership_id)

    async def holds_role(db: AsyncSession, membership_id: UUID, role: UserRole) -> bool:
        return True

    async def no_record(db: AsyncSession, key: UUID) -> None:
        return None

    async def insert_session(db: AsyncSession, **kwargs: Any) -> DrivingSessionModel:
        return build_driving_session_record(
            driver_id=kwargs["driver_id"], vehicle_id=kwargs["vehicle_id"]
        )

    monkeypatch.setattr(
        identity_service, "resolve_membership_person_reference", resolve_person
    )
    monkeypatch.setattr(identity_service, "membership_holds_role", holds_role)
    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", no_record)
    monkeypatch.setattr(driver_repository, "find_open_session_by_vehicle", no_record)
    monkeypatch.setattr(
        driver_repository, "find_in_progress_trip_by_session", no_record
    )
    monkeypatch.setattr(driver_repository, "insert_session", insert_session)
    monkeypatch.setattr(telemetry_service, "resolve_vehicle_live_status", no_record)


def _patch_truck(
    monkeypatch: pytest.MonkeyPatch, organization_id: UUID
) -> VehicleReference:
    """Make every truck code resolve to one truck of `organization_id`."""
    truck = VehicleReference(
        vehicle_id=uuid4(),
        vin="1HGBH41JXMN109186",
        organization_id=organization_id,
        battery_capacity_kwh=100.0,
    )

    async def resolve_truck(db: AsyncSession, key: object) -> VehicleReference:
        return truck

    monkeypatch.setattr(
        vehicle_service, "resolve_vehicle_reference_by_code", resolve_truck
    )
    monkeypatch.setattr(
        vehicle_service, "resolve_vehicle_reference_by_id", resolve_truck
    )
    return truck


def _patch_own_profile(
    monkeypatch: pytest.MonkeyPatch, principal: Principal
) -> DriverModel:
    """Give the caller a driver profile of their own membership."""
    driver = build_driver_record(membership_id=principal.membership_id)

    async def find_driver(db: AsyncSession, membership_id: UUID) -> DriverModel | None:
        return driver if membership_id == principal.membership_id else None

    monkeypatch.setattr(driver_repository, "find_by_membership_id", find_driver)
    return driver


def _patch_other_driver(monkeypatch: pytest.MonkeyPatch) -> DriverModel:
    """Make a driver of another membership resolve; the caller has no profile."""
    driver = build_driver_record()

    async def no_profile(db: AsyncSession, membership_id: UUID) -> None:
        return None

    async def get_driver(
        db: AsyncSession, driver_id: UUID, **_scope: object
    ) -> DriverModel:
        return driver

    monkeypatch.setattr(driver_repository, "find_by_membership_id", no_profile)
    monkeypatch.setattr(driver_repository, "get_by_id", get_driver)
    return driver


@pytest.mark.asyncio
async def test_portal_self_check_in_to_another_organizations_truck_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A2: a manager's own PORTAL check-in stays in their reach; their QR one may borrow."""
    manager = build_principal(roles=frozenset({UserRole.ORG_ADMIN, UserRole.DRIVER}))
    _patch_own_profile(monkeypatch, manager)
    _patch_truck(monkeypatch, OTHER_ORGANIZATION_ID)

    with pytest.raises(DriverVehicleNotFoundError):
        await driver_service.check_in_driver(
            fake_db_session(),
            DrivingSessionCheckInRequest(
                vehicle_code="51C-123.45", check_in_method=CheckInMethod.PORTAL
            ),
            principal=manager,
        )

    # By QR the borrowed-truck rule (DR-11) still applies: it only warns.
    response = await driver_service.check_in_driver(
        fake_db_session(),
        DrivingSessionCheckInRequest(
            vehicle_code="51C-123.45",
            check_in_method=CheckInMethod.QR,
            latitude=10.76,
            longitude=106.66,
        ),
        principal=manager,
    )
    assert CheckInWarning.OTHER_ORGANIZATION in response.warnings


@pytest.mark.asyncio
async def test_fleet_limited_manager_cannot_check_a_driver_in_or_out_outside_their_fleets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A4: a truck outside the fleet limit is not found; its open session neither."""
    manager = build_principal(roles=frozenset({UserRole.FLEET_MANAGER}))
    driver = _patch_other_driver(monkeypatch)
    truck = _patch_truck(monkeypatch, DEFAULT_ORGANIZATION_ID)
    other_fleet_truck_ids = frozenset({uuid4()})

    with pytest.raises(DriverVehicleNotFoundError):
        await driver_service.check_in_driver(
            fake_db_session(),
            DrivingSessionCheckInRequest(
                driver_id=driver.driver_id,
                vehicle_code=truck.vin,
                check_in_method=CheckInMethod.PORTAL,
            ),
            principal=manager,
            visible_vehicle_ids=other_fleet_truck_ids,
        )

    open_session = build_driving_session_record(
        driver_id=driver.driver_id, vehicle_id=truck.vehicle_id
    )

    async def find_open(db: AsyncSession, key: UUID) -> DrivingSessionModel:
        return open_session

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", find_open)
    with pytest.raises(DrivingSessionNotFoundError):
        await driver_service.check_out_driver(
            fake_db_session(),
            DrivingSessionCheckOutRequest(driver_id=driver.driver_id),
            principal=manager,
            visible_vehicle_ids=other_fleet_truck_ids,
        )


@pytest.mark.asyncio
async def test_check_in_is_refused_without_the_driver_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A5: a profile whose membership lost DRIVER cannot check in."""
    driver_principal = build_principal(roles=frozenset({UserRole.DRIVER}))
    _patch_own_profile(monkeypatch, driver_principal)
    _patch_truck(monkeypatch, DEFAULT_ORGANIZATION_ID)

    async def lacks_role(db: AsyncSession, membership_id: UUID, role: UserRole) -> bool:
        return False

    monkeypatch.setattr(identity_service, "membership_holds_role", lacks_role)
    with pytest.raises(DriverRoleMissingError):
        await driver_service.check_in_driver(
            fake_db_session(),
            DrivingSessionCheckInRequest(
                vehicle_code="51C-123.45",
                check_in_method=CheckInMethod.APP,
                latitude=10.76,
                longitude=106.66,
            ),
            principal=driver_principal,
        )


@pytest.mark.asyncio
async def test_revoking_the_driver_role_ends_the_open_session_but_other_roles_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A5: revoke_role runs the hook; DRIVER ends the session DRIVER_REMOVED."""
    admin = build_principal()
    driver = _patch_other_driver(monkeypatch)
    shift = build_driving_session_record(driver_id=driver.driver_id, vehicle_id=uuid4())
    closed: list[DrivingSessionEndCause] = []

    async def find_driver(db: AsyncSession, membership_id: UUID) -> DriverModel:
        return driver

    async def find_open(db: AsyncSession, key: UUID) -> DrivingSessionModel:
        return shift

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        closed.append(kwargs["end_cause"])
        return session_record

    async def get_membership(db: AsyncSession, membership_id: UUID) -> MembershipModel:
        return MembershipModel(
            membership_id=membership_id, organization_id=DEFAULT_ORGANIZATION_ID
        )

    async def find_assignment(db: AsyncSession, **_kwargs: object) -> object:
        return object()

    async def revoke_assignment(
        db: AsyncSession, *_args: object, **_kw: object
    ) -> None:
        return None

    monkeypatch.setattr(driver_repository, "find_by_membership_id", find_driver)
    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", find_open)
    monkeypatch.setattr(driver_repository, "close_session", close_session)
    monkeypatch.setattr(identity_repository, "get_membership", get_membership)
    monkeypatch.setattr(
        identity_repository, "find_active_role_assignment", find_assignment
    )
    monkeypatch.setattr(
        identity_repository, "revoke_role_assignment", revoke_assignment
    )
    monkeypatch.setattr(identity_member_service, "_role_revoked_hooks", [])
    identity_service.register_role_revoked_hook(driver_service.handle_role_revoked)

    for role in (UserRole.DISPATCHER, UserRole.DRIVER):
        await identity_member_service.revoke_role(
            fake_db_session(), admin, driver.membership_id, role
        )

    assert closed == [DrivingSessionEndCause.DRIVER_REMOVED]


@pytest.mark.asyncio
async def test_planned_trip_cannot_start_on_a_truck_of_another_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A6 (D2): the session's organization must be the trip's, else 409."""
    driver_principal = build_principal(roles=frozenset({UserRole.DRIVER}))
    driver = _patch_own_profile(monkeypatch, driver_principal)
    trip = TripModel(
        trip_id=uuid4(),
        organization_id=DEFAULT_ORGANIZATION_ID,
        planned_driver_id=driver.driver_id,
        status=TripStatus.PLANNED.value,
    )
    shift = build_driving_session_record(driver_id=driver.driver_id, vehicle_id=uuid4())
    shift.organization_id = OTHER_ORGANIZATION_ID

    async def get_trip(db: AsyncSession, trip_id: UUID, **_scope: object) -> TripModel:
        return trip

    async def find_open(db: AsyncSession, key: UUID) -> DrivingSessionModel:
        return shift

    monkeypatch.setattr(driver_repository, "get_trip_by_id", get_trip)
    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", find_open)

    with pytest.raises(TripOrganizationMismatchError):
        await trip_service.start_trip(
            fake_db_session(),
            trip.trip_id,
            TripStartRequest(),
            principal=driver_principal,
        )


@pytest.mark.asyncio
async def test_ownership_change_records_the_staff_member_on_the_closed_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A7: the trip closed by a truck transfer names who recorded the sale."""
    shift = build_driving_session_record(driver_id=uuid4(), vehicle_id=uuid4())
    trip = TripModel(
        trip_id=uuid4(),
        status=TripStatus.IN_PROGRESS.value,
        started_at=utc_now() - timedelta(hours=1),
    )
    actors: list[object] = []
    staff_user_id = uuid4()

    async def find_open(db: AsyncSession, key: UUID) -> DrivingSessionModel:
        return shift

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        return session_record

    async def running_trip(db: AsyncSession, session_id: UUID) -> TripModel:
        return trip

    async def update_trip(
        db: AsyncSession, record: TripModel, values: dict[str, Any], **kwargs: Any
    ) -> TripModel:
        actors.append(kwargs["changed_by"])
        return record

    monkeypatch.setattr(driver_repository, "find_open_session_by_vehicle", find_open)
    monkeypatch.setattr(driver_repository, "close_session", close_session)
    monkeypatch.setattr(
        driver_repository, "find_in_progress_trip_by_session", running_trip
    )
    monkeypatch.setattr(driver_repository, "update_trip_fields", update_trip)

    ended_id = await driver_service.end_open_session_on_ownership_change(
        fake_db_session(),
        shift.vehicle_id,
        ended_at=utc_now(),
        changed_by=staff_user_id,
    )

    assert ended_id == shift.driving_session_id
    assert actors == [staff_user_id]
