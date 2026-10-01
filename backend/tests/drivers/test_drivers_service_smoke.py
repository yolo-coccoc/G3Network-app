"""Smoke tests for the drivers service: profiles and vehicle assignment history (F-E4)."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.repository as driver_repository
import app.domains.drivers.service as driver_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.drivers.exceptions import (
    DriverAssignmentConflictError,
    DriverAssignmentNotFoundError,
    DriverConflictError,
    DriverNotFoundError,
    DriverVehicleNotFoundError,
)
from app.domains.drivers.models import DriverModel, DriverVehicleAssignmentModel
from app.domains.drivers.schemas import DriverCreateRequest, DriverVehicleAssignRequest
from app.domains.drivers.types import DriverStatus
from app.domains.vehicles.types import (
    VehicleReference,
)
from tests.builders import build_assignment_record, build_driver_record, fake_db_session


@pytest.mark.asyncio
async def test_driver_service_creates_driver_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The driver service creates a response when there's no unique conflict (F-E4)."""
    record = build_driver_record()

    async def no_existing_phone(db: AsyncSession, value: str) -> None:
        return None

    async def no_existing_license(db: AsyncSession, value: str) -> None:
        return None

    async def insert_driver(db: AsyncSession, values: dict[str, Any]) -> DriverModel:
        return record

    async def no_active_assignment(db: AsyncSession, driver_id: UUID) -> None:
        return None

    monkeypatch.setattr(driver_repository, "find_by_phone_number", no_existing_phone)
    monkeypatch.setattr(
        driver_repository, "find_by_license_number", no_existing_license
    )
    monkeypatch.setattr(driver_repository, "insert", insert_driver)
    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_driver", no_active_assignment
    )

    response = await driver_service.create_driver(
        fake_db_session(),
        DriverCreateRequest(
            full_name=record.full_name,
            phone_number=record.phone_number,
            license_number=record.license_number,
            status=record.status,
        ),
    )

    assert response.driver_id == record.driver_id
    assert response.license_number == record.license_number
    assert response.current_vehicle_id is None
    assert response.current_vehicle_vin is None


@pytest.mark.asyncio
async def test_driver_service_create_rejects_duplicate_phone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_driver() raises a conflict when the phone number is already used (F-E4)."""
    record = build_driver_record()

    async def existing_phone(db: AsyncSession, value: str) -> DriverModel:
        return record

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("insert must not run when phone number already exists")

    monkeypatch.setattr(driver_repository, "find_by_phone_number", existing_phone)
    monkeypatch.setattr(driver_repository, "insert", fail_if_called)

    with pytest.raises(DriverConflictError):
        await driver_service.create_driver(
            fake_db_session(),
            DriverCreateRequest(
                full_name="Another Driver",
                phone_number=record.phone_number,
                license_number="LICENSE-002",
                status=DriverStatus.ACTIVE,
            ),
        )


@pytest.mark.asyncio
async def test_driver_service_soft_delete_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """soft_delete_driver() succeeds when the driver has no active assignment (F-E4)."""
    record = build_driver_record()

    async def no_active_assignment(db: AsyncSession, driver_id: UUID) -> None:
        return None

    async def soft_delete(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return record

    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_driver", no_active_assignment
    )
    monkeypatch.setattr(driver_repository, "soft_delete", soft_delete)

    deletion_response = await driver_service.soft_delete_driver(
        fake_db_session(), record.driver_id
    )

    assert deletion_response == {"message": "Driver deleted successfully"}


@pytest.mark.asyncio
async def test_driver_service_soft_delete_closes_active_assignment_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """soft_delete_driver() closes an open assignment before deleting (F-E4)."""
    record = build_driver_record()
    vehicle_id = uuid4()
    active_assignment = build_assignment_record(
        driver_id=record.driver_id, vehicle_id=vehicle_id
    )
    closed: dict[str, object] = {}

    async def find_active(
        db: AsyncSession, driver_id: UUID
    ) -> DriverVehicleAssignmentModel:
        return active_assignment

    async def close_assignment(
        db: AsyncSession,
        assignment_record: DriverVehicleAssignmentModel,
        **kwargs: object,
    ) -> DriverVehicleAssignmentModel:
        closed["assignment_id"] = assignment_record.assignment_id
        closed["unassigned_at"] = kwargs["unassigned_at"]
        return assignment_record

    async def soft_delete(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return record

    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_driver", find_active
    )
    monkeypatch.setattr(driver_repository, "close_assignment", close_assignment)
    monkeypatch.setattr(driver_repository, "soft_delete", soft_delete)

    await driver_service.soft_delete_driver(fake_db_session(), record.driver_id)

    assert closed["assignment_id"] == active_assignment.assignment_id
    assert closed["unassigned_at"] is not None


@pytest.mark.asyncio
async def test_assign_vehicle_to_driver_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assign_vehicle_to_driver() opens a new assignment and enriches the VIN (F-E4)."""
    driver_record = build_driver_record()
    vehicle_id = uuid4()
    vehicle_reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    inserted = build_assignment_record(
        driver_id=driver_record.driver_id, vehicle_id=vehicle_id
    )

    async def get_by_id(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver_record

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return vehicle_reference

    async def no_active_by_vehicle(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    async def no_active_by_driver(db: AsyncSession, driver_id: UUID) -> None:
        return None

    async def insert_assignment(
        db: AsyncSession, **kwargs: object
    ) -> DriverVehicleAssignmentModel:
        return inserted

    monkeypatch.setattr(driver_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_vehicle", no_active_by_vehicle
    )
    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_driver", no_active_by_driver
    )
    monkeypatch.setattr(driver_repository, "insert_assignment", insert_assignment)

    response = await driver_service.assign_vehicle_to_driver(
        fake_db_session(),
        driver_record.driver_id,
        DriverVehicleAssignRequest(vehicle_vin=vehicle_reference.vin),
    )

    assert response.assignment_id == inserted.assignment_id
    assert response.vehicle_vin == vehicle_reference.vin
    assert response.unassigned_at is None


@pytest.mark.asyncio
async def test_assign_vehicle_to_driver_rejects_unknown_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assign_vehicle_to_driver() raises when the VIN doesn't resolve to a vehicle (F-E4)."""
    driver_record = build_driver_record()

    async def get_by_id(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver_record

    async def no_vehicle(db: AsyncSession, vin: str) -> None:
        return None

    monkeypatch.setattr(driver_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", no_vehicle
    )

    with pytest.raises(DriverVehicleNotFoundError):
        await driver_service.assign_vehicle_to_driver(
            fake_db_session(),
            driver_record.driver_id,
            DriverVehicleAssignRequest(vehicle_vin="1HGBH41JXMN109186"),
        )


@pytest.mark.asyncio
async def test_assign_vehicle_to_driver_rejects_vehicle_assigned_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assign_vehicle_to_driver() never silently steals a vehicle from another driver (F-E4)."""
    driver_record = build_driver_record()
    other_driver_id = uuid4()
    vehicle_id = uuid4()
    vehicle_reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    assignment_elsewhere = build_assignment_record(
        driver_id=other_driver_id, vehicle_id=vehicle_id
    )

    async def get_by_id(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver_record

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return vehicle_reference

    async def active_by_vehicle(
        db: AsyncSession, vehicle_id: UUID
    ) -> DriverVehicleAssignmentModel:
        return assignment_elsewhere

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("insert_assignment must not run on a conflict")

    monkeypatch.setattr(driver_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_vehicle", active_by_vehicle
    )
    monkeypatch.setattr(driver_repository, "insert_assignment", fail_if_called)

    with pytest.raises(DriverAssignmentConflictError):
        await driver_service.assign_vehicle_to_driver(
            fake_db_session(),
            driver_record.driver_id,
            DriverVehicleAssignRequest(vehicle_vin=vehicle_reference.vin),
        )


@pytest.mark.asyncio
async def test_assign_vehicle_to_driver_is_idempotent_for_same_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assigning the vehicle a driver already has is a no-op returning the existing row (F-E4)."""
    driver_record = build_driver_record()
    vehicle_id = uuid4()
    vehicle_reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    existing_assignment = build_assignment_record(
        driver_id=driver_record.driver_id, vehicle_id=vehicle_id
    )

    async def get_by_id(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver_record

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return vehicle_reference

    async def active_by_vehicle(
        db: AsyncSession, vehicle_id: UUID
    ) -> DriverVehicleAssignmentModel:
        return existing_assignment

    async def fail_if_called(db: AsyncSession, **kwargs: object) -> None:
        raise AssertionError("no new assignment should be inserted")

    monkeypatch.setattr(driver_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_vehicle", active_by_vehicle
    )
    monkeypatch.setattr(driver_repository, "insert_assignment", fail_if_called)
    monkeypatch.setattr(driver_repository, "close_assignment", fail_if_called)

    response = await driver_service.assign_vehicle_to_driver(
        fake_db_session(),
        driver_record.driver_id,
        DriverVehicleAssignRequest(vehicle_vin=vehicle_reference.vin),
    )

    assert response.assignment_id == existing_assignment.assignment_id


@pytest.mark.asyncio
async def test_assign_vehicle_to_driver_auto_closes_previous_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assigning a driver a second vehicle auto-closes their first one (F-E4)."""
    driver_record = build_driver_record()
    old_vehicle_id = uuid4()
    new_vehicle_id = uuid4()
    vehicle_reference = VehicleReference(
        vehicle_id=new_vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )
    previous_assignment = build_assignment_record(
        driver_id=driver_record.driver_id, vehicle_id=old_vehicle_id
    )
    new_assignment = build_assignment_record(
        driver_id=driver_record.driver_id, vehicle_id=new_vehicle_id
    )
    closed_ids: list[UUID] = []

    async def get_by_id(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver_record

    async def resolve_vin(db: AsyncSession, vin: str) -> VehicleReference:
        return vehicle_reference

    async def no_active_by_vehicle(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    async def active_by_driver(
        db: AsyncSession, driver_id: UUID
    ) -> DriverVehicleAssignmentModel:
        return previous_assignment

    async def close_assignment(
        db: AsyncSession,
        assignment_record: DriverVehicleAssignmentModel,
        **kwargs: object,
    ) -> DriverVehicleAssignmentModel:
        closed_ids.append(assignment_record.assignment_id)
        return assignment_record

    async def insert_assignment(
        db: AsyncSession, **kwargs: object
    ) -> DriverVehicleAssignmentModel:
        return new_assignment

    monkeypatch.setattr(driver_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_vehicle", no_active_by_vehicle
    )
    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_driver", active_by_driver
    )
    monkeypatch.setattr(driver_repository, "close_assignment", close_assignment)
    monkeypatch.setattr(driver_repository, "insert_assignment", insert_assignment)

    response = await driver_service.assign_vehicle_to_driver(
        fake_db_session(),
        driver_record.driver_id,
        DriverVehicleAssignRequest(vehicle_vin=vehicle_reference.vin),
    )

    assert closed_ids == [previous_assignment.assignment_id]
    assert response.assignment_id == new_assignment.assignment_id
    assert response.vehicle_id == new_vehicle_id


@pytest.mark.asyncio
async def test_unassign_vehicle_from_driver_closes_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """unassign_vehicle_from_driver() closes the driver's open assignment (F-E4)."""
    driver_record = build_driver_record()
    active_assignment = build_assignment_record(
        driver_id=driver_record.driver_id, vehicle_id=uuid4()
    )
    closed: dict[str, object] = {}

    async def get_by_id(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver_record

    async def find_active(
        db: AsyncSession, driver_id: UUID
    ) -> DriverVehicleAssignmentModel:
        return active_assignment

    async def close_assignment(
        db: AsyncSession,
        assignment_record: DriverVehicleAssignmentModel,
        **kwargs: object,
    ) -> DriverVehicleAssignmentModel:
        closed["assignment_id"] = assignment_record.assignment_id
        return assignment_record

    monkeypatch.setattr(driver_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_driver", find_active
    )
    monkeypatch.setattr(driver_repository, "close_assignment", close_assignment)

    await driver_service.unassign_vehicle_from_driver(
        fake_db_session(), driver_record.driver_id
    )

    assert closed["assignment_id"] == active_assignment.assignment_id


@pytest.mark.asyncio
async def test_unassign_vehicle_from_driver_rejects_when_no_active_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """unassign_vehicle_from_driver() raises when nothing is currently assigned (F-E4)."""
    driver_record = build_driver_record()

    async def get_by_id(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver_record

    async def no_active(db: AsyncSession, driver_id: UUID) -> None:
        return None

    monkeypatch.setattr(driver_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        driver_repository, "find_active_assignment_by_driver", no_active
    )

    with pytest.raises(DriverAssignmentNotFoundError):
        await driver_service.unassign_vehicle_from_driver(
            fake_db_session(), driver_record.driver_id
        )


@pytest.mark.asyncio
async def test_list_driver_assignment_history_enriches_each_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_driver_assignment_history() returns rows enriched with each vehicle's VIN (F-E4)."""
    driver_record = build_driver_record()
    vehicle_id = uuid4()
    closed_at = datetime.now(timezone.utc)
    history_row = build_assignment_record(
        driver_id=driver_record.driver_id,
        vehicle_id=vehicle_id,
        unassigned_at=closed_at,
    )
    vehicle_reference = VehicleReference(
        vehicle_id=vehicle_id, vin="1HGBH41JXMN109186", battery_capacity_kwh=None
    )

    async def get_by_id(db: AsyncSession, driver_id: UUID) -> DriverModel:
        return driver_record

    async def list_assignments(
        db: AsyncSession, driver_id: UUID, **kwargs: object
    ) -> list[DriverVehicleAssignmentModel]:
        return [history_row]

    async def count_assignments(db: AsyncSession, driver_id: UUID) -> int:
        return 1

    async def resolve_by_id(db: AsyncSession, vehicle_id: UUID) -> VehicleReference:
        return vehicle_reference

    monkeypatch.setattr(driver_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        driver_repository, "list_assignments_by_driver", list_assignments
    )
    monkeypatch.setattr(
        driver_repository, "count_assignments_by_driver", count_assignments
    )
    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_id", resolve_by_id
    )

    history = await driver_service.list_driver_assignment_history(
        fake_db_session(), driver_record.driver_id
    )

    assert history.total == 1
    assert history.items[0].vehicle_vin == vehicle_reference.vin
    assert history.items[0].unassigned_at == closed_at


@pytest.mark.asyncio
async def test_get_driver_raises_not_found_for_unknown_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_driver() raises when the driver doesn't exist or was soft-deleted (F-E4)."""

    async def no_driver(db: AsyncSession, driver_id: UUID) -> None:
        return None

    monkeypatch.setattr(driver_repository, "get_by_id", no_driver)

    with pytest.raises(DriverNotFoundError):
        await driver_service.get_driver(fake_db_session(), uuid4())
