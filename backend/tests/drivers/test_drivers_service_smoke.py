"""Smoke tests for the drivers service: profiles and driving sessions (F-E4)."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.repository as driver_repository
import app.domains.drivers.service as driver_service
import app.domains.identity.service as identity_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.drivers.exceptions import (
    DriverConflictError,
    DriverLicenseExpiredError,
    DriverMembershipNotFoundError,
    DriverNotEligibleError,
    DrivingSessionNotFoundError,
)
from app.domains.drivers.models import DriverModel, DrivingSessionModel
from app.domains.drivers.schemas import (
    DriverCreateRequest,
    DrivingSessionCheckInRequest,
    DrivingSessionCheckOutRequest,
)
from app.domains.drivers.types import (
    CheckInMethod,
    DriverStatus,
    DrivingSessionEndCause,
    LicenseClass,
)
from app.domains.identity.types import MembershipPersonReference, MembershipStatus
from app.domains.vehicles.types import VehicleReference
from app.libs.common.clock import utc_now
from tests.builders import (
    build_driver_record,
    build_driving_session_record,
    build_person_reference,
    fake_db_session,
)


def _create_request(*, expires_on: date | None = None) -> DriverCreateRequest:
    """Build a valid create request, with a licence valid for a year by default."""
    return DriverCreateRequest(
        membership_id=uuid4(),
        license_number="LICENSE-001",
        license_class=LicenseClass.CE,
        license_expires_on=expires_on or utc_now().date() + timedelta(days=365),
    )


def _patch_person(
    monkeypatch: pytest.MonkeyPatch, person: MembershipPersonReference | None
) -> None:
    """Make the identity service resolve every membership to `person`."""

    async def resolve_person(
        db: AsyncSession, membership_id: UUID
    ) -> MembershipPersonReference | None:
        return person

    monkeypatch.setattr(
        identity_service, "resolve_membership_person_reference", resolve_person
    )


def _patch_no_open_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make both open-session lookups find nothing."""

    async def no_session(db: AsyncSession, key: UUID) -> None:
        return None

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", no_session)
    monkeypatch.setattr(driver_repository, "find_open_session_by_vehicle", no_session)


@pytest.mark.asyncio
async def test_create_driver_returns_response_with_the_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_driver() builds the response from the profile and the user's data (DR-09)."""
    record = build_driver_record()
    _patch_person(monkeypatch, build_person_reference())
    _patch_no_open_session(monkeypatch)

    async def no_profile(db: AsyncSession, membership_id: UUID) -> None:
        return None

    async def insert_driver(db: AsyncSession, values: dict[str, Any]) -> DriverModel:
        return record

    monkeypatch.setattr(driver_repository, "find_by_membership_id", no_profile)
    monkeypatch.setattr(driver_repository, "insert", insert_driver)

    response = await driver_service.create_driver(fake_db_session(), _create_request())

    assert response.driver_id == record.driver_id
    assert response.full_name == "Test Driver"
    assert response.is_license_expired is False
    assert response.current_vehicle_id is None


@pytest.mark.asyncio
async def test_create_driver_rejects_second_profile_unknown_membership_and_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One profile per membership; the membership must exist; the licence be valid."""
    record = build_driver_record()

    async def existing_profile(db: AsyncSession, membership_id: UUID) -> DriverModel:
        return record

    monkeypatch.setattr(driver_repository, "find_by_membership_id", existing_profile)

    _patch_person(monkeypatch, None)
    with pytest.raises(DriverMembershipNotFoundError):
        await driver_service.create_driver(fake_db_session(), _create_request())

    _patch_person(monkeypatch, build_person_reference())
    with pytest.raises(DriverLicenseExpiredError):
        await driver_service.create_driver(
            fake_db_session(),
            _create_request(expires_on=utc_now().date() - timedelta(days=1)),
        )
    with pytest.raises(DriverConflictError):
        await driver_service.create_driver(fake_db_session(), _create_request())


@pytest.mark.asyncio
async def test_soft_delete_driver_ends_the_open_session_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """soft_delete_driver() ends the open session DRIVER_REMOVED, then deletes (DR-10)."""
    record = build_driver_record()
    open_session = build_driving_session_record(
        driver_id=record.driver_id, vehicle_id=uuid4()
    )
    closed_causes: list[DrivingSessionEndCause] = []

    async def find_open(db: AsyncSession, driver_id: UUID) -> DrivingSessionModel:
        return open_session

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        closed_causes.append(kwargs["end_cause"])
        return session_record

    async def soft_delete(
        db: AsyncSession, driver_id: UUID, *, status_reason: str
    ) -> DriverModel:
        return record

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", find_open)
    monkeypatch.setattr(driver_repository, "close_session", close_session)
    monkeypatch.setattr(driver_repository, "soft_delete", soft_delete)

    deletion_response = await driver_service.soft_delete_driver(
        fake_db_session(), record.driver_id
    )

    assert deletion_response == {"message": "Driver deleted successfully"}
    assert closed_causes == [DrivingSessionEndCause.DRIVER_REMOVED]


def _check_in_request(driver_id: UUID) -> DrivingSessionCheckInRequest:
    """Build a PORTAL check-in request for a fixed VIN."""
    return DrivingSessionCheckInRequest(
        driver_id=driver_id,
        vehicle_vin="1HGBH41JXMN109186",
        check_in_method=CheckInMethod.PORTAL,
    )


def _patch_vehicle(monkeypatch: pytest.MonkeyPatch) -> VehicleReference:
    """Make the vehicles service resolve any VIN or ID to one truck."""
    vehicle = VehicleReference(
        vehicle_id=uuid4(),
        vin="1HGBH41JXMN109186",
        organization_id=uuid4(),
        battery_capacity_kwh=None,
    )

    async def resolve_vehicle(db: AsyncSession, key: object) -> VehicleReference:
        return vehicle

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vehicle
    )
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_vehicle
    )
    return vehicle


@pytest.mark.asyncio
async def test_check_in_takes_over_a_truck_and_ends_the_drivers_other_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check-in ends the driver's other session (OTHER_TRUCK) and the truck's (TAKEN_OVER)."""
    driver = build_driver_record()
    vehicle = _patch_vehicle(monkeypatch)
    _patch_person(monkeypatch, build_person_reference())
    own_other_session = build_driving_session_record(
        driver_id=driver.driver_id, vehicle_id=uuid4()
    )
    truck_session = build_driving_session_record(
        driver_id=uuid4(), vehicle_id=vehicle.vehicle_id
    )
    causes: dict[UUID, DrivingSessionEndCause] = {}

    async def get_driver(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver

    async def find_by_driver(db: AsyncSession, driver_id: UUID) -> DrivingSessionModel:
        return own_other_session

    async def find_by_vehicle(
        db: AsyncSession, vehicle_id: UUID
    ) -> DrivingSessionModel:
        return truck_session

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        causes[session_record.driving_session_id] = kwargs["end_cause"]
        session_record.end_cause = kwargs["end_cause"].value
        session_record.ended_at = kwargs["ended_at"]
        return session_record

    async def insert_session(db: AsyncSession, **kwargs: Any) -> DrivingSessionModel:
        assert kwargs["organization_id"] == vehicle.organization_id
        return build_driving_session_record(
            driver_id=kwargs["driver_id"], vehicle_id=kwargs["vehicle_id"]
        )

    monkeypatch.setattr(driver_repository, "get_by_id", get_driver)
    monkeypatch.setattr(
        driver_repository, "find_open_session_by_driver", find_by_driver
    )
    monkeypatch.setattr(
        driver_repository, "find_open_session_by_vehicle", find_by_vehicle
    )
    monkeypatch.setattr(driver_repository, "close_session", close_session)
    monkeypatch.setattr(driver_repository, "insert_session", insert_session)

    response = await driver_service.check_in_driver(
        fake_db_session(), _check_in_request(driver.driver_id)
    )

    assert response.ended_at is None
    assert causes == {
        own_other_session.driving_session_id: DrivingSessionEndCause.OTHER_TRUCK,
        truck_session.driving_session_id: DrivingSessionEndCause.TAKEN_OVER,
    }
    assert len(response.ended_sessions) == 2


@pytest.mark.asyncio
async def test_check_in_rejects_an_inactive_driver_and_an_inactive_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-10: the profile, the licence, the membership and the user must all be fine."""
    driver = build_driver_record()
    _patch_vehicle(monkeypatch)

    async def get_driver(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver

    monkeypatch.setattr(driver_repository, "get_by_id", get_driver)

    _patch_person(monkeypatch, build_person_reference())
    driver.status = DriverStatus.INACTIVE
    with pytest.raises(DriverNotEligibleError):
        await driver_service.check_in_driver(
            fake_db_session(), _check_in_request(driver.driver_id)
        )

    driver.status = DriverStatus.ACTIVE
    _patch_person(
        monkeypatch, build_person_reference(membership_status=MembershipStatus.LOCKED)
    )
    with pytest.raises(DriverNotEligibleError):
        await driver_service.check_in_driver(
            fake_db_session(), _check_in_request(driver.driver_id)
        )


@pytest.mark.asyncio
async def test_check_out_closes_the_session_and_rejects_a_driver_not_checked_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """check_out_driver() ends CHECKED_OUT; without an open session it is a 404."""
    _patch_vehicle(monkeypatch)
    driver_id = uuid4()
    _patch_no_open_session(monkeypatch)
    with pytest.raises(DrivingSessionNotFoundError):
        await driver_service.check_out_driver(
            fake_db_session(), DrivingSessionCheckOutRequest(driver_id=driver_id)
        )

    open_session = build_driving_session_record(driver_id=driver_id, vehicle_id=uuid4())

    async def find_open(db: AsyncSession, key: UUID) -> DrivingSessionModel:
        return open_session

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        session_record.ended_at = kwargs["ended_at"]
        session_record.end_cause = kwargs["end_cause"].value
        return session_record

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", find_open)
    monkeypatch.setattr(driver_repository, "close_session", close_session)

    response = await driver_service.check_out_driver(
        fake_db_session(), DrivingSessionCheckOutRequest(driver_id=driver_id)
    )

    assert response.end_cause == DrivingSessionEndCause.CHECKED_OUT
