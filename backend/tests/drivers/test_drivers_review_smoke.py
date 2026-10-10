"""Review smoke tests for driver rules: licence date and a driver's data reach (RV-OP12).

The `xfail(strict=True)` test asserts the correct behaviour that a reviewed
defect breaks today; once fixed it passes, strict mode reports it and the
marker must be removed. The other tests guard rules that already hold: a
DRIVER-only caller reads, starts and finishes only their own trips.

RV-OP9 (the check-in location guard) waits for an owner decision and has no
test here. Nothing touches a database: repositories are monkeypatched.
"""

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.repository as driver_repository
import app.domains.drivers.service as driver_service
import app.domains.drivers.trip_service as trip_service
from app.domains.drivers.exceptions import TripNotFoundError
from app.domains.drivers.models import DriverModel, DrivingSessionModel, TripModel
from app.domains.drivers.schemas import TripStartRequest
from app.domains.drivers.types import TripStatus
from app.domains.identity.types import UserRole
from tests.builders import (
    build_driver_record,
    build_driving_session_record,
    fake_db_session,
)
from tests.principals import build_principal


def _freeze_driver_clock(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    """Make the drivers service read a fixed current time.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        now: The UTC time `utc_now()` returns.
    """
    monkeypatch.setattr(driver_service, "utc_now", lambda: now)


@pytest.mark.xfail(
    strict=True,
    reason="RV-OP12: licence expiry is compared with the UTC date",
)
def test_licence_expired_yesterday_in_vietnam_is_expired_before_7am_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At 03:00 on 11 October in Vietnam a licence that ended on 10 October is expired.

    It is still 20:00 on 10 October in UTC; the printed expiry is a local
    calendar date (APP_REPORT_TIMEZONE), so the check must use the local day.
    """
    _freeze_driver_clock(
        monkeypatch, datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
    )

    assert driver_service._is_license_expired(date(2026, 10, 10)) is True


def test_licence_is_valid_on_its_last_local_day_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: on the expiry day itself (10:00 local) the licence is still valid."""
    _freeze_driver_clock(monkeypatch, datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc))

    assert driver_service._is_license_expired(date(2026, 10, 10)) is False


def _planned_trip(*, planned_driver_id: UUID | None) -> TripModel:
    """Build a PLANNED trip of the default organization.

    Args:
        planned_driver_id: The driver the plan names, if any.

    Returns:
        An unsaved ORM trip.
    """
    now = datetime.now(timezone.utc)
    return TripModel(
        trip_id=uuid4(),
        organization_id=uuid4(),
        status=TripStatus.PLANNED.value,
        planned_driver_id=planned_driver_id,
        created_at=now,
        updated_at=now,
    )


def _patch_own_driver(monkeypatch: pytest.MonkeyPatch, driver: DriverModel) -> None:
    """Make every membership resolve to the caller's own driver profile.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        driver: The caller's profile.
    """

    async def find_driver(db: AsyncSession, membership_id: UUID) -> DriverModel:
        return driver

    monkeypatch.setattr(driver_repository, "find_by_membership_id", find_driver)


@pytest.mark.asyncio
async def test_driver_cannot_read_a_trip_planned_for_another_driver_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: a DRIVER's trip lookup is scoped to their own profile, so another's is 404."""
    own_driver = build_driver_record()
    other_trip = _planned_trip(planned_driver_id=uuid4())
    _patch_own_driver(monkeypatch, own_driver)
    lookups: list[dict[str, Any]] = []

    async def get_trip(
        db: AsyncSession, trip_id: UUID, **scope: Any
    ) -> TripModel | None:
        lookups.append(scope)
        if scope.get("driver_id") == other_trip.planned_driver_id:
            return other_trip
        return None

    monkeypatch.setattr(driver_repository, "get_trip_by_id", get_trip)
    driver = build_principal(roles=frozenset({UserRole.DRIVER}))

    with pytest.raises(TripNotFoundError):
        await trip_service.get_trip(
            fake_db_session(), other_trip.trip_id, principal=driver
        )
    assert lookups == [{"driver_id": own_driver.driver_id}]


@pytest.mark.asyncio
async def test_driver_trip_list_ignores_a_filter_naming_another_driver_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: a DRIVER's list is always their own trips, whatever `driver_id` says."""
    own_driver = build_driver_record()
    _patch_own_driver(monkeypatch, own_driver)
    list_scopes: list[dict[str, Any]] = []

    async def list_trips(db: AsyncSession, **scope: Any) -> list[TripModel]:
        list_scopes.append(scope)
        return []

    async def count_trips(db: AsyncSession, **scope: Any) -> int:
        return 0

    monkeypatch.setattr(driver_repository, "list_trips", list_trips)
    monkeypatch.setattr(driver_repository, "count_trips", count_trips)
    driver = build_principal(roles=frozenset({UserRole.DRIVER}))

    await trip_service.list_trips(
        fake_db_session(), principal=driver, driver_id=uuid4()
    )

    assert list_scopes[0]["driver_id"] == own_driver.driver_id
    assert list_scopes[0]["organization_id"] is None


@pytest.mark.asyncio
async def test_driver_cannot_start_a_trip_planned_for_another_driver_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: a plan naming another driver is not the caller's trip (404)."""
    own_driver = build_driver_record()
    other_trip = _planned_trip(planned_driver_id=uuid4())
    _patch_own_driver(monkeypatch, own_driver)

    async def get_trip(db: AsyncSession, trip_id: UUID, **scope: Any) -> TripModel:
        return other_trip

    monkeypatch.setattr(driver_repository, "get_trip_by_id", get_trip)
    driver = build_principal(roles=frozenset({UserRole.DRIVER}))

    with pytest.raises(TripNotFoundError):
        await trip_service.start_trip(
            fake_db_session(), other_trip.trip_id, TripStartRequest(), principal=driver
        )


@pytest.mark.asyncio
async def test_driver_cannot_finish_a_trip_running_in_another_drivers_shift_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: only the driver of the trip's session may finish it (404 otherwise)."""
    own_driver = build_driver_record()
    other_shift = build_driving_session_record(driver_id=uuid4(), vehicle_id=uuid4())
    running_trip = _planned_trip(planned_driver_id=None)
    running_trip.status = TripStatus.IN_PROGRESS.value
    running_trip.driving_session_id = other_shift.driving_session_id
    _patch_own_driver(monkeypatch, own_driver)

    async def get_trip(db: AsyncSession, trip_id: UUID, **scope: Any) -> TripModel:
        return running_trip

    async def get_session(db: AsyncSession, session_id: UUID) -> DrivingSessionModel:
        return other_shift

    monkeypatch.setattr(driver_repository, "get_trip_by_id", get_trip)
    monkeypatch.setattr(driver_repository, "get_session_by_id", get_session)
    driver = build_principal(roles=frozenset({UserRole.DRIVER}))

    with pytest.raises(TripNotFoundError):
        await trip_service.finish_trip(
            fake_db_session(), running_trip.trip_id, principal=driver
        )
    assert running_trip.status == TripStatus.IN_PROGRESS.value
