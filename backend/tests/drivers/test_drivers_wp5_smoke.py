"""Smoke tests for WP5: check-in rules, auto-end, own summary, trips, DR-10 (F-E4, F-A9)."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.repository as driver_repository
import app.domains.drivers.service as driver_service
import app.domains.drivers.trip_service as trip_service
import app.domains.identity.member_service as identity_member_service
import app.domains.identity.service as identity_service
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.domains.drivers.exceptions import (
    DriverCheckInLocationRequiredError,
    DriverMembershipLockedError,
    DriverRoleMissingError,
    DriverTooFarFromVehicleError,
    TripInProgressConflictError,
    TripNotCheckedInError,
    TripStateConflictError,
)
from app.domains.drivers.models import DriverModel, DrivingSessionModel, TripModel
from app.domains.drivers.schemas import (
    DriverCreateRequest,
    DriverUpdateRequest,
    DrivingSessionCheckInRequest,
    PersonalTripStartRequest,
    TripPlanRequest,
)
from app.domains.drivers.types import (
    CheckInMethod,
    CheckInWarning,
    DriverStatus,
    DrivingSessionEndCause,
    LicenseClass,
    TripStatus,
)
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import (
    MembershipEndKind,
    MembershipStatus,
    OrganizationSettingsReference,
    UserRole,
)
from app.domains.telemetry.types import VehicleLiveStatusReference
from app.domains.vehicles.types import VehicleReference
from app.libs.common.clock import utc_now
from tests.builders import (
    build_driver_record,
    build_driving_session_record,
    build_person_reference,
    fake_db_session,
)
from tests.principals import build_internal_principal, build_principal

TRUCK_LAT = 10.7600
TRUCK_LON = 106.6600


def _reading(
    vehicle_id: UUID,
    *,
    age_minutes: float = 0,
    odometer_km: float | None = 1000.0,
    soc_percent: float | None = 80.0,
    latitude: float = TRUCK_LAT,
    longitude: float = TRUCK_LON,
) -> VehicleLiveStatusReference:
    """Build a T-Box reading received `age_minutes` ago."""
    received_at = utc_now() - timedelta(minutes=age_minutes)
    return VehicleLiveStatusReference(
        vehicle_id=vehicle_id,
        latitude=latitude,
        longitude=longitude,
        recorded_at=received_at,
        received_at=received_at,
        is_online=True,
        signal_strength_dbm=None,
        odometer_km=odometer_km,
        soc_percent=soc_percent,
    )


@pytest.fixture(autouse=True)
def stub_shared_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the lookups that every service call makes on the fake session."""

    async def no_trip(db: AsyncSession, driving_session_id: UUID) -> None:
        return None

    async def holds_role(db: AsyncSession, membership_id: UUID, role: UserRole) -> bool:
        return True

    async def no_other_person(
        db: AsyncSession, license_number: str, user_id: UUID
    ) -> bool:
        return False

    monkeypatch.setattr(driver_repository, "find_in_progress_trip_by_session", no_trip)
    monkeypatch.setattr(identity_service, "membership_holds_role", holds_role)
    monkeypatch.setattr(
        driver_repository, "exists_license_on_other_person", no_other_person
    )


def _patch_driver_and_person(
    monkeypatch: pytest.MonkeyPatch,
    driver: DriverModel,
    *,
    membership_status: MembershipStatus = MembershipStatus.ACTIVE,
) -> None:
    """Make the driver, its person and the caller's own profile resolve."""

    async def get_driver(
        db: AsyncSession, driver_id: UUID, **_scope: object
    ) -> DriverModel:
        return driver

    async def find_driver(db: AsyncSession, membership_id: UUID) -> DriverModel:
        return driver

    async def resolve_person(db: AsyncSession, membership_id: UUID) -> object:
        return build_person_reference(membership_status=membership_status)

    monkeypatch.setattr(driver_repository, "get_by_id", get_driver)
    monkeypatch.setattr(driver_repository, "find_by_membership_id", find_driver)
    monkeypatch.setattr(
        identity_service, "resolve_membership_person_reference", resolve_person
    )


def _patch_truck(monkeypatch: pytest.MonkeyPatch) -> VehicleReference:
    """Make the vehicles service resolve any code or ID to one truck."""
    truck = VehicleReference(
        vehicle_id=uuid4(),
        vin="1HGBH41JXMN109186",
        organization_id=uuid4(),
        battery_capacity_kwh=100.0,
    )

    async def resolve_truck(db: AsyncSession, key: object) -> VehicleReference:
        return truck

    for name in (
        "resolve_vehicle_reference_by_code",
        "resolve_vehicle_reference_by_id",
        "resolve_vehicle_reference_by_vin",
    ):
        monkeypatch.setattr(vehicle_service, name, resolve_truck)
    return truck


def _patch_session_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[DrivingSessionModel, DrivingSessionEndCause]]:
    """Record every session the service closes and make the open lookups empty."""
    closed: list[tuple[DrivingSessionModel, DrivingSessionEndCause]] = []

    async def no_session(db: AsyncSession, key: UUID) -> None:
        return None

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        session_record.ended_at = kwargs["ended_at"]
        session_record.end_cause = kwargs["end_cause"].value
        closed.append((session_record, kwargs["end_cause"]))
        return session_record

    async def insert_session(db: AsyncSession, **kwargs: Any) -> DrivingSessionModel:
        return build_driving_session_record(
            driver_id=kwargs["driver_id"], vehicle_id=kwargs["vehicle_id"]
        )

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", no_session)
    monkeypatch.setattr(driver_repository, "find_open_session_by_vehicle", no_session)
    monkeypatch.setattr(driver_repository, "close_session", close_session)
    monkeypatch.setattr(driver_repository, "insert_session", insert_session)
    return closed


@pytest.mark.asyncio
async def test_qr_check_in_is_refused_far_from_the_truck_and_warns_without_a_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-07: a far phone is refused, no recent truck position or another organization only warns."""
    driver = build_driver_record()
    truck = _patch_truck(monkeypatch)
    _patch_driver_and_person(monkeypatch, driver)
    _patch_session_writes(monkeypatch)
    principal = build_internal_principal()

    def request(latitude: float | None, longitude: float | None) -> object:
        return DrivingSessionCheckInRequest(
            driver_id=driver.driver_id,
            vehicle_code="51C-123.45",
            check_in_method=CheckInMethod.QR,
            latitude=latitude,
            longitude=longitude,
        )

    # No phone position on a QR check-in is an input error.
    with pytest.raises(DriverCheckInLocationRequiredError):
        await driver_service.check_in_driver(
            fake_db_session(),
            request(None, None),  # type: ignore[arg-type]
            principal=principal,
        )

    far_reading = _reading(truck.vehicle_id, latitude=TRUCK_LAT + 0.05)

    async def far_status(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleLiveStatusReference:
        return far_reading

    monkeypatch.setattr(telemetry_service, "resolve_vehicle_live_status", far_status)
    with pytest.raises(DriverTooFarFromVehicleError):
        await driver_service.check_in_driver(
            fake_db_session(),
            request(TRUCK_LAT, TRUCK_LON),  # type: ignore[arg-type]
            principal=principal,
        )

    async def stale_status(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleLiveStatusReference:
        return _reading(vehicle_id, age_minutes=600)

    monkeypatch.setattr(telemetry_service, "resolve_vehicle_live_status", stale_status)
    response = await driver_service.check_in_driver(
        fake_db_session(),
        request(TRUCK_LAT, TRUCK_LON),  # type: ignore[arg-type]
        principal=principal,
    )
    assert CheckInWarning.NO_RECENT_TRUCK_POSITION in response.warnings
    assert CheckInWarning.OTHER_ORGANIZATION in response.warnings


@pytest.mark.asyncio
async def test_check_in_near_the_truck_succeeds_and_a_driver_cannot_use_portal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A phone next to the truck passes; only a manager checks in from the portal."""
    driver = build_driver_record()
    truck = _patch_truck(monkeypatch)
    _patch_driver_and_person(monkeypatch, driver)
    _patch_session_writes(monkeypatch)

    async def near_status(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleLiveStatusReference:
        return _reading(truck.vehicle_id)

    monkeypatch.setattr(telemetry_service, "resolve_vehicle_live_status", near_status)
    response = await driver_service.check_in_driver(
        fake_db_session(),
        DrivingSessionCheckInRequest(
            vehicle_vin=truck.vin,
            check_in_method=CheckInMethod.APP,
            latitude=TRUCK_LAT + 0.0001,
            longitude=TRUCK_LON,
        ),
        principal=build_internal_principal(),
    )
    assert response.vehicle_vin == truck.vin
    assert CheckInWarning.NO_RECENT_TRUCK_POSITION not in response.warnings

    with pytest.raises(AccessDeniedError):
        await driver_service.check_in_driver(
            fake_db_session(),
            DrivingSessionCheckInRequest(
                vehicle_vin=truck.vin, check_in_method=CheckInMethod.PORTAL
            ),
            principal=build_principal(roles=frozenset({UserRole.DRIVER})),
        )


@pytest.mark.asyncio
async def test_auto_end_ends_only_idle_sessions_with_proof_the_truck_stood_still(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-07: ended at the countdown start; no telemetry or none after it is skipped."""
    now = utc_now()
    idle = build_driving_session_record(driver_id=uuid4(), vehicle_id=uuid4())
    idle.started_at = now - timedelta(hours=5)
    moving = build_driving_session_record(driver_id=uuid4(), vehicle_id=uuid4())
    moving.started_at = now - timedelta(hours=5)
    no_box = build_driving_session_record(driver_id=uuid4(), vehicle_id=uuid4())
    no_box.started_at = now - timedelta(hours=5)
    fresh = build_driving_session_record(driver_id=uuid4(), vehicle_id=uuid4())
    fresh.started_at = now - timedelta(minutes=10)
    last_moved_at = now - timedelta(hours=3)
    closed: list[tuple[DrivingSessionModel, DrivingSessionEndCause]] = []

    async def list_open(db: AsyncSession) -> list[DrivingSessionModel]:
        return [idle, moving, no_box, fresh]

    async def settings_of(
        db: AsyncSession, organization_id: UUID
    ) -> OrganizationSettingsReference:
        return OrganizationSettingsReference(
            organization_id=organization_id,
            telemetry_interval_seconds=10,
            driving_session_auto_end_minutes=120,
        )

    async def last_telemetry(db: AsyncSession, vehicle_id: UUID) -> datetime | None:
        return None if vehicle_id == no_box.vehicle_id else now

    async def last_movement(
        db: AsyncSession, vehicle_id: UUID, **_kwargs: object
    ) -> datetime | None:
        if vehicle_id == idle.vehicle_id:
            return last_moved_at
        if vehicle_id == moving.vehicle_id:
            return now - timedelta(minutes=5)
        return None

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        session_record.ended_at = kwargs["ended_at"]
        closed.append((session_record, kwargs["end_cause"]))
        return session_record

    monkeypatch.setattr(driver_repository, "list_open_sessions", list_open)
    monkeypatch.setattr(identity_service, "resolve_organization_settings", settings_of)
    monkeypatch.setattr(telemetry_service, "resolve_last_telemetry_at", last_telemetry)
    monkeypatch.setattr(telemetry_service, "resolve_last_movement_at", last_movement)
    monkeypatch.setattr(driver_repository, "close_session", close_session)

    result = await driver_service.end_idle_driving_sessions(fake_db_session(), now=now)

    assert (result.checked, result.ended, result.skipped) == (4, 1, 1)
    assert closed == [(idle, DrivingSessionEndCause.AUTO_ENDED)]
    assert idle.ended_at == last_moved_at


@pytest.mark.asyncio
async def test_own_driving_summary_totals_per_day_and_week_without_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-11: sessions, distance from the odometer, totals per day and week; no places."""
    driver = build_driver_record()
    _patch_driver_and_person(monkeypatch, driver)
    first = build_driving_session_record(driver_id=driver.driver_id, vehicle_id=uuid4())
    first.started_at = utc_now() - timedelta(hours=5)
    first.ended_at = first.started_at + timedelta(hours=2)
    second = build_driving_session_record(
        driver_id=driver.driver_id, vehicle_id=uuid4()
    )
    second.started_at = first.started_at + timedelta(minutes=1)
    second.ended_at = second.started_at + timedelta(hours=1)

    async def sessions_in_range(
        db: AsyncSession, **_kwargs: object
    ) -> list[DrivingSessionModel]:
        return [second, first]

    async def distance(db: AsyncSession, vehicle_id: UUID, **_kwargs: object) -> float:
        return 40.0

    async def summary(db: AsyncSession, vehicle_id: UUID) -> object:
        class _Summary:
            license_plate = "51C-123.45"

        return _Summary()

    monkeypatch.setattr(driver_repository, "list_sessions_in_range", sessions_in_range)
    monkeypatch.setattr(telemetry_service, "resolve_distance_km_in_window", distance)
    monkeypatch.setattr(vehicle_service, "resolve_vehicle_summary_by_id", summary)

    response = await driver_service.get_own_driving_summary(
        fake_db_session(), principal=build_principal(roles=frozenset({UserRole.DRIVER}))
    )

    assert [item.duration_minutes for item in response.sessions] == [60, 120]
    assert response.sessions[0].license_plate == "51C-123.45"
    assert sum(total.driving_minutes for total in response.totals_per_week) == 180
    assert sum(total.distance_km for total in response.totals_per_day) == 80.0
    assert not hasattr(response.sessions[0], "route")


@pytest.mark.asyncio
async def test_trip_personal_start_finish_computes_distance_energy_and_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-12: Start needs a shift; Finish records the end readings; figures are computed."""
    driver = build_driver_record()
    truck = _patch_truck(monkeypatch)
    _patch_driver_and_person(monkeypatch, driver)
    shift = build_driving_session_record(
        driver_id=driver.driver_id, vehicle_id=truck.vehicle_id
    )
    principal = build_principal(roles=frozenset({UserRole.DRIVER}))
    stored: dict[str, TripModel] = {}
    readings = [_reading(truck.vehicle_id), _reading(truck.vehicle_id)]
    readings[1] = _reading(truck.vehicle_id, odometer_km=1050.0, soc_percent=70.0)

    async def open_session(db: AsyncSession, key: UUID) -> DrivingSessionModel | None:
        return shift

    async def insert_trip(db: AsyncSession, values: dict[str, Any]) -> TripModel:
        now = utc_now()
        trip = TripModel(trip_id=uuid4(), created_at=now, updated_at=now, **values)
        stored["trip"] = trip
        return trip

    async def get_trip(db: AsyncSession, trip_id: UUID, **_scope: object) -> TripModel:
        return stored["trip"]

    async def get_session(db: AsyncSession, session_id: UUID) -> DrivingSessionModel:
        return shift

    async def update_trip(
        db: AsyncSession, trip: TripModel, values: dict[str, Any], **_kwargs: object
    ) -> TripModel:
        for name, value in values.items():
            setattr(trip, name, value)
        return trip

    async def live_status(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleLiveStatusReference:
        return readings.pop(0)

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", open_session)
    monkeypatch.setattr(driver_repository, "insert_trip", insert_trip)
    monkeypatch.setattr(driver_repository, "get_trip_by_id", get_trip)
    monkeypatch.setattr(driver_repository, "get_session_by_id", get_session)
    monkeypatch.setattr(driver_repository, "update_trip_fields", update_trip)
    monkeypatch.setattr(telemetry_service, "resolve_vehicle_live_status", live_status)

    started = await trip_service.start_personal_trip(
        fake_db_session(),
        PersonalTripStartRequest(origin_name="Depot", destination_name="Port"),
        principal=principal,
    )
    assert started.status == TripStatus.IN_PROGRESS
    assert started.start_odometer_km == 1000.0

    finished = await trip_service.finish_trip(
        fake_db_session(), started.trip_id, principal=principal
    )
    assert finished.status == TripStatus.COMPLETED
    assert finished.distance_km == 50.0
    assert finished.energy_kwh == 10.0
    assert finished.kwh_per_km == 0.2
    assert finished.vehicle_differs_from_plan is False

    with pytest.raises(TripStateConflictError):
        await trip_service.finish_trip(
            fake_db_session(), started.trip_id, principal=principal
        )


@pytest.mark.asyncio
async def test_trip_start_needs_a_shift_and_one_trip_per_session_and_plan_needs_a_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-12: 409 without a check-in, 409 for a second running trip, 403 for a driver planning."""
    driver = build_driver_record()
    _patch_driver_and_person(monkeypatch, driver)
    driver_principal = build_principal(roles=frozenset({UserRole.DRIVER}))

    async def no_session(db: AsyncSession, key: UUID) -> None:
        return None

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", no_session)
    with pytest.raises(TripNotCheckedInError):
        await trip_service.start_personal_trip(
            fake_db_session(), PersonalTripStartRequest(), principal=driver_principal
        )

    shift = build_driving_session_record(driver_id=driver.driver_id, vehicle_id=uuid4())

    async def open_session(db: AsyncSession, key: UUID) -> DrivingSessionModel:
        return shift

    async def running_trip(db: AsyncSession, session_id: UUID) -> TripModel:
        return TripModel(trip_id=uuid4(), status="IN_PROGRESS")

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", open_session)
    monkeypatch.setattr(
        driver_repository, "find_in_progress_trip_by_session", running_trip
    )
    with pytest.raises(TripInProgressConflictError):
        await trip_service.start_personal_trip(
            fake_db_session(), PersonalTripStartRequest(), principal=driver_principal
        )

    with pytest.raises(AccessDeniedError):
        await trip_service.plan_trip(
            fake_db_session(),
            TripPlanRequest(
                origin_name="A",
                destination_name="B",
                planned_start_at=utc_now() + timedelta(hours=1),
            ),
            principal=driver_principal,
        )


@pytest.mark.asyncio
async def test_check_out_closes_the_trip_still_running_in_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-12: a trip in progress when the session ends becomes COMPLETED with the reason."""
    driver = build_driver_record()
    _patch_truck(monkeypatch)
    _patch_driver_and_person(monkeypatch, driver)
    _patch_session_writes(monkeypatch)
    shift = build_driving_session_record(driver_id=driver.driver_id, vehicle_id=uuid4())
    trip = TripModel(
        trip_id=uuid4(),
        status="IN_PROGRESS",
        started_at=utc_now() - timedelta(hours=1),
        start_odometer_km=Decimal("10.0"),
    )
    updates: list[dict[str, Any]] = []

    async def open_session(db: AsyncSession, key: UUID) -> DrivingSessionModel:
        return shift

    async def running_trip(db: AsyncSession, session_id: UUID) -> TripModel:
        return trip

    async def update_trip(
        db: AsyncSession, record: TripModel, values: dict[str, Any], **_kwargs: object
    ) -> TripModel:
        updates.append(values)
        return record

    async def no_status(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        session_record.ended_at = kwargs["ended_at"]
        session_record.end_cause = kwargs["end_cause"].value
        return session_record

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", open_session)
    monkeypatch.setattr(driver_repository, "close_session", close_session)
    monkeypatch.setattr(
        driver_repository, "find_in_progress_trip_by_session", running_trip
    )
    monkeypatch.setattr(driver_repository, "update_trip_fields", update_trip)
    monkeypatch.setattr(telemetry_service, "resolve_vehicle_live_status", no_status)

    from app.domains.drivers.schemas import DrivingSessionCheckOutRequest

    await driver_service.check_out_driver(
        fake_db_session(),
        DrivingSessionCheckOutRequest(),
        principal=build_principal(roles=frozenset({UserRole.DRIVER})),
    )

    assert updates[0]["status"] == "COMPLETED"
    assert updates[0]["status_reason"] == driver_service.TRIP_AUTO_CLOSED_REASON


@pytest.mark.asyncio
async def test_membership_end_closes_session_and_profile_but_a_lock_keeps_the_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-10: ENDED deletes the profile; LOCKED only ends the open session."""
    driver = build_driver_record()
    shift = build_driving_session_record(driver_id=driver.driver_id, vehicle_id=uuid4())
    deleted: list[str] = []
    closed: list[DrivingSessionEndCause] = []

    async def find_driver(db: AsyncSession, membership_id: UUID) -> DriverModel:
        return driver

    async def open_session(db: AsyncSession, key: UUID) -> DrivingSessionModel:
        return shift

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        closed.append(kwargs["end_cause"])
        return session_record

    async def soft_delete(
        db: AsyncSession, driver_id: UUID, *, status_reason: str, **_kwargs: object
    ) -> DriverModel:
        deleted.append(status_reason)
        return driver

    monkeypatch.setattr(driver_repository, "find_by_membership_id", find_driver)
    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", open_session)
    monkeypatch.setattr(driver_repository, "close_session", close_session)
    monkeypatch.setattr(driver_repository, "soft_delete", soft_delete)

    for kind in (MembershipEndKind.LOCKED, MembershipEndKind.ENDED):
        await driver_service.handle_membership_end(
            fake_db_session(),
            membership_id=driver.membership_id,
            kind=kind,
            acting_user_id=uuid4(),
            reason="Left the company",
        )

    assert closed == [DrivingSessionEndCause.DRIVER_REMOVED] * 2
    assert deleted == ["Left the company"]


@pytest.mark.asyncio
async def test_create_driver_needs_the_driver_role_and_an_unlocked_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DRV-01: the membership must hold the DRIVER role and not be locked."""
    request = DriverCreateRequest(
        membership_id=uuid4(),
        license_number="LICENSE-9",
        license_class=LicenseClass.CE,
        license_expires_on=utc_now().date() + timedelta(days=100),
    )
    person = build_person_reference(membership_status=MembershipStatus.LOCKED)

    async def resolve_person(db: AsyncSession, membership_id: UUID) -> object:
        return person

    monkeypatch.setattr(
        identity_service, "resolve_membership_person_reference", resolve_person
    )
    with pytest.raises(DriverMembershipLockedError):
        await driver_service.create_driver(
            fake_db_session(), request, principal=build_internal_principal()
        )

    person_active = build_person_reference()

    async def resolve_active(db: AsyncSession, membership_id: UUID) -> object:
        return person_active

    async def no_role(db: AsyncSession, membership_id: UUID, role: UserRole) -> bool:
        return False

    monkeypatch.setattr(
        identity_service, "resolve_membership_person_reference", resolve_active
    )
    monkeypatch.setattr(identity_service, "membership_holds_role", no_role)
    with pytest.raises(DriverRoleMissingError):
        await driver_service.create_driver(
            fake_db_session(), request, principal=build_internal_principal()
        )


@pytest.mark.asyncio
async def test_deactivating_a_driver_ends_the_open_session_and_the_list_searches_the_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DRV-01: INACTIVE ends the session; the list adds the people matching the text."""
    driver = build_driver_record()
    _patch_truck(monkeypatch)
    _patch_driver_and_person(monkeypatch, driver)
    shift = build_driving_session_record(driver_id=driver.driver_id, vehicle_id=uuid4())
    closed: list[DrivingSessionEndCause] = []
    seen: dict[str, object] = {}

    async def open_session(db: AsyncSession, key: UUID) -> DrivingSessionModel:
        return shift

    async def close_session(
        db: AsyncSession, session_record: DrivingSessionModel, **kwargs: Any
    ) -> DrivingSessionModel:
        closed.append(kwargs["end_cause"])
        return session_record

    async def update_fields(
        db: AsyncSession, driver_id: UUID, values: dict[str, Any], **_kwargs: object
    ) -> DriverModel:
        driver.status = values["status"]
        return driver

    async def search_ids(db: AsyncSession, text: str, **_kwargs: object) -> list[UUID]:
        return [driver.membership_id]

    async def list_all(db: AsyncSession, **kwargs: Any) -> list[DriverModel]:
        seen.update(kwargs)
        return [driver]

    async def count(db: AsyncSession, **_kwargs: object) -> int:
        return 1

    monkeypatch.setattr(driver_repository, "find_open_session_by_driver", open_session)
    monkeypatch.setattr(driver_repository, "close_session", close_session)
    monkeypatch.setattr(driver_repository, "update_fields", update_fields)
    monkeypatch.setattr(identity_service, "search_membership_ids_by_person", search_ids)
    monkeypatch.setattr(driver_repository, "list_all", list_all)
    monkeypatch.setattr(driver_repository, "count", count)

    await driver_service.update_driver(
        fake_db_session(),
        driver.driver_id,
        DriverUpdateRequest(status=DriverStatus.INACTIVE, status_reason="Suspended"),
        principal=build_internal_principal(),
    )
    assert closed == [DrivingSessionEndCause.DRIVER_REMOVED]

    listing = await driver_service.list_drivers(
        fake_db_session(),
        principal=build_internal_principal(),
        search_text="Nguyen",
        license_expires_within_days=30,
    )
    assert listing.total == 1
    assert seen["person_membership_ids"] == [driver.membership_id]
    assert seen["license_expires_by"] is not None


@pytest.mark.asyncio
async def test_membership_end_hooks_run_registered_callbacks_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity runs each registered hook with the kind, actor and reason."""
    calls: list[tuple[UUID, MembershipEndKind, str]] = []

    async def hook(
        db: AsyncSession,
        *,
        membership_id: UUID,
        kind: MembershipEndKind,
        acting_user_id: UUID,
        reason: str,
    ) -> None:
        calls.append((membership_id, kind, reason))

    monkeypatch.setattr(identity_member_service, "_membership_end_hooks", [])
    identity_member_service.register_membership_end_hook(hook)
    identity_member_service.register_membership_end_hook(hook)
    membership_id = uuid4()
    await identity_member_service._run_membership_end_hooks(
        fake_db_session(),
        membership_id=membership_id,
        kind=MembershipEndKind.LOCKED,
        acting_user_id=uuid4(),
        reason="Why",
    )
    assert calls == [(membership_id, MembershipEndKind.LOCKED, "Why")]
