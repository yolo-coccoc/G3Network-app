"""Review smoke tests of the vehicles service: data reach and review findings.

The guard tests pin the organization isolation that holds today: a customer
of one organization gets "not found" for another organization's truck on
get, update, soft delete and transfer, while internal staff reach it. The
tests marked ``xfail(strict=True)`` assert the correct behaviour for a defect
found in the assets review (RV-AS2, RV-AS4, RV-AS5, RV-AS6, RV-AS8, RV-AS10); each fails
today, and the fix turns it into an unexpected pass that forces the marker
off. Every repository call is replaced by an in-memory fake, so nothing here
touches a database.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.service as identity_service
import app.domains.vehicles.repository as vehicle_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.identity.types import OrganizationReference, Principal
from app.domains.vehicles.exceptions import VehicleConflictError, VehicleNotFoundError
from app.domains.vehicles.models import VehicleModel, VehicleModelModel
from app.domains.vehicles.schemas import (
    VehicleCreateRequest,
    VehicleOwnershipTransferRequest,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.errors import DomainError, InvalidInputError
from tests.builders import (
    build_vehicle_model_record,
    build_vehicle_record,
    fake_db_session,
)
from tests.principals import (
    DEFAULT_ORGANIZATION_ID,
    OTHER_ORGANIZATION_ID,
    build_internal_principal,
    build_principal,
)

# A vehicle operation run as one caller: get, update, soft delete or transfer.
VehicleOperation = Callable[[UUID, Principal], Awaitable[object]]


def _install_scoped_vehicle_repository(
    monkeypatch: pytest.MonkeyPatch, vehicle_record: VehicleModel
) -> dict[str, Any]:
    """Replace the vehicle repository with a one-row fake that honours the scope.

    The fake behaves like the real ``get_by_id`` / ``update_fields``: a row of
    another organization than the ``organization_id`` filter is "not found",
    and ``None`` means no restriction (internal staff).

    Args:
        monkeypatch: The test's monkeypatch fixture.
        vehicle_record: The only vehicle in the fake table.

    Returns:
        The values the last ``update_fields`` call wrote (empty if none).
    """
    written_values: dict[str, Any] = {}

    def _visible(vehicle_id: UUID, organization_id: UUID | None) -> bool:
        """Tell whether the fake row matches the ID and the scope."""
        return vehicle_id == vehicle_record.vehicle_id and (
            organization_id is None or organization_id == vehicle_record.organization_id
        )

    async def get_by_id(
        db_session: AsyncSession,
        vehicle_id: UUID,
        *,
        organization_id: UUID | None = None,
        for_update: bool = False,
    ) -> VehicleModel | None:
        """Return the row when it is in scope."""
        return vehicle_record if _visible(vehicle_id, organization_id) else None

    async def update_fields(
        db_session: AsyncSession,
        vehicle_id: UUID,
        values: dict[str, Any],
        *,
        change_reason: str,
        changed_by: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> VehicleModel | None:
        """Record the values when the row is in scope."""
        if not _visible(vehicle_id, organization_id):
            return None
        written_values.update(values)
        return vehicle_record

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(vehicle_repository, "update_fields", update_fields)
    return written_values


async def _get_vehicle(vehicle_id: UUID, principal: Principal) -> object:
    """Read a vehicle as the caller."""
    return await vehicle_service.get_vehicle(
        fake_db_session(), vehicle_id, principal=principal
    )


async def _update_vehicle(vehicle_id: UUID, principal: Principal) -> object:
    """Change a vehicle's manufacturing year as the caller."""
    return await vehicle_service.update_vehicle(
        fake_db_session(),
        vehicle_id,
        VehicleUpdateRequest(year=2025),
        principal=principal,
    )


async def _soft_delete_vehicle(vehicle_id: UUID, principal: Principal) -> object:
    """Soft-delete a vehicle as the caller."""
    await vehicle_service.soft_delete_vehicle(
        fake_db_session(), vehicle_id, principal=principal
    )
    return None


async def _transfer_vehicle(vehicle_id: UUID, principal: Principal) -> object:
    """Transfer a vehicle to a third organization as the caller."""
    return await vehicle_service.transfer_vehicle_ownership(
        fake_db_session(),
        vehicle_id,
        VehicleOwnershipTransferRequest(organization_id=uuid4(), reason="Sold"),
        principal=principal,
    )


_VEHICLE_OPERATIONS = [
    pytest.param(_get_vehicle, id="get"),
    pytest.param(_update_vehicle, id="update"),
    pytest.param(_soft_delete_vehicle, id="soft-delete"),
    pytest.param(_transfer_vehicle, id="transfer"),
]


def _vehicle_of_other_organization() -> VehicleModel:
    """Build a vehicle owned by ``OTHER_ORGANIZATION_ID``, taken 30 days ago."""
    vehicle_record = build_vehicle_record()
    vehicle_record.organization_id = OTHER_ORGANIZATION_ID
    vehicle_record.acquired_at = datetime.now(timezone.utc) - timedelta(days=30)
    return vehicle_record


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _VEHICLE_OPERATIONS)
async def test_customer_gets_not_found_for_another_organizations_vehicle(
    monkeypatch: pytest.MonkeyPatch, operation: VehicleOperation
) -> None:
    """A customer of organization A never reaches organization B's truck."""
    vehicle_record = _vehicle_of_other_organization()
    written_values = _install_scoped_vehicle_repository(monkeypatch, vehicle_record)

    with pytest.raises(VehicleNotFoundError):
        await operation(
            vehicle_record.vehicle_id,
            build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        )
    assert written_values == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", _VEHICLE_OPERATIONS)
async def test_internal_staff_reach_any_organizations_vehicle(
    monkeypatch: pytest.MonkeyPatch,
    own_organization_for_new_records: None,
    operation: VehicleOperation,
) -> None:
    """Internal staff (no data scope) reach a customer's truck on every operation."""
    vehicle_record = _vehicle_of_other_organization()
    _install_scoped_vehicle_repository(monkeypatch, vehicle_record)

    await operation(vehicle_record.vehicle_id, build_internal_principal())


@pytest.mark.asyncio
async def test_transfer_vehicle_to_closed_organization_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truck cannot be handed to an organization that was closed (DM-25)."""
    vehicle_record = _vehicle_of_other_organization()
    written_values = _install_scoped_vehicle_repository(monkeypatch, vehicle_record)

    async def find_closed_organization(
        db_session: AsyncSession, organization_id: UUID
    ) -> OrganizationReference:
        """Return the named organization as CLOSED."""
        return OrganizationReference(
            organization_id=organization_id,
            display_name="Closed Co",
            is_internal=False,
            legal_form="COMPANY",
            status="CLOSED",
        )

    monkeypatch.setattr(
        identity_service, "find_organization_reference", find_closed_organization
    )

    with pytest.raises(DomainError):
        await _transfer_vehicle(vehicle_record.vehicle_id, build_internal_principal())
    assert written_values == {}


def _install_free_unique_fields(
    monkeypatch: pytest.MonkeyPatch,
    *,
    existing_by_vin: VehicleModel | None = None,
) -> dict[str, Any]:
    """Let ``create_vehicle`` reach the insert with a fake catalog and table.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        existing_by_vin: A live vehicle already holding the VIN, if any.

    Returns:
        The values handed to ``insert`` (empty until it is called).
    """
    inserted_values: dict[str, Any] = {}

    async def existing_vehicle_model(
        db_session: AsyncSession,
        vehicle_model_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> VehicleModelModel:
        """Return a live catalog model."""
        return build_vehicle_model_record()

    async def no_vehicle_by_plate(db_session: AsyncSession, value: str) -> None:
        """Report the plate as free."""
        return None

    async def vehicle_by_vin(
        db_session: AsyncSession, value: str
    ) -> VehicleModel | None:
        """Report the VIN as held by ``existing_by_vin``."""
        return existing_by_vin

    async def insert(db_session: AsyncSession, values: dict[str, Any]) -> VehicleModel:
        """Record the values and return a matching row."""
        inserted_values.update(values)
        vehicle_record = build_vehicle_record()
        vehicle_record.organization_id = values["organization_id"]
        vehicle_record.acquired_at = values["acquired_at"]
        return vehicle_record

    monkeypatch.setattr(
        vehicle_repository, "get_vehicle_model_by_id", existing_vehicle_model
    )
    monkeypatch.setattr(
        vehicle_repository, "find_by_license_plate", no_vehicle_by_plate
    )
    monkeypatch.setattr(vehicle_repository, "find_by_vin", vehicle_by_vin)
    monkeypatch.setattr(vehicle_repository, "insert", insert)
    return inserted_values


@pytest.mark.asyncio
async def test_create_vehicle_rejects_acquired_at_in_the_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handover date in the future is refused, so the truck stays transferable."""
    inserted_values = _install_free_unique_fields(monkeypatch)

    with pytest.raises(InvalidInputError):
        await vehicle_service.create_vehicle(
            fake_db_session(),
            VehicleCreateRequest(
                license_plate="51C-00001",
                vin="LJ1EKABR3N0000001",
                vehicle_model_id=uuid4(),
                year=2026,
                acquired_at=datetime.now(timezone.utc) + timedelta(days=365),
            ),
            principal=build_principal(),
        )
    assert inserted_values == {}


@pytest.mark.asyncio
async def test_vin_conflict_with_another_tenant_does_not_echo_the_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A customer whose VIN is held in another organization learns nothing more
    than "conflict": the message does not repeat the VIN."""
    other_vehicle = _vehicle_of_other_organization()
    _install_free_unique_fields(monkeypatch, existing_by_vin=other_vehicle)

    with pytest.raises(VehicleConflictError) as conflict:
        await vehicle_service.create_vehicle(
            fake_db_session(),
            VehicleCreateRequest(
                license_plate="51C-00002",
                vin=other_vehicle.vin,
                vehicle_model_id=uuid4(),
                year=2026,
            ),
            principal=build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        )
    assert other_vehicle.vin not in str(conflict.value)


def test_vehicle_create_request_normalizes_vin_and_plate() -> None:
    """VIN and plate are trimmed and upper-cased, so the live-unique indexes
    cannot be bypassed by letter case or spaces."""
    vehicle_create_request = VehicleCreateRequest(
        license_plate=" 51c-12345 ",
        vin="lj1ekabr3n0000001",
        vehicle_model_id=uuid4(),
        year=2026,
    )

    assert vehicle_create_request.vin == "LJ1EKABR3N0000001"
    assert vehicle_create_request.license_plate == "51C-12345"


@pytest.mark.asyncio
async def test_update_vehicle_with_blank_status_reason_is_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A status reason of only spaces is refused as invalid input (400): by the
    request schema, or by the service, never by a bare ValueError (500).

    The real ``update_fields`` runs, so the history context is set exactly as
    in production; the blank check fails before the fake session is used.
    """
    vehicle_record = build_vehicle_record()

    async def get_by_id(
        db_session: AsyncSession,
        vehicle_id: UUID,
        *,
        organization_id: UUID | None = None,
    ) -> VehicleModel:
        """Return the vehicle."""
        return vehicle_record

    monkeypatch.setattr(vehicle_repository, "get_by_id", get_by_id)

    try:
        vehicle_update_request = VehicleUpdateRequest(
            status=VehicleStatus.INACTIVE, status_reason="   "
        )
    except ValidationError:
        return
    with pytest.raises(InvalidInputError):
        await vehicle_service.update_vehicle(
            fake_db_session(),
            vehicle_record.vehicle_id,
            vehicle_update_request,
            principal=build_principal(organization_id=vehicle_record.organization_id),
        )


@pytest.mark.asyncio
async def test_vehicle_back_to_active_clears_status_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting a truck ACTIVE without a reason clears the stored reason (the
    DBML: NULL when ACTIVE), the same rule batteries already follow."""
    vehicle_record = build_vehicle_record()
    vehicle_record.status = VehicleStatus.INACTIVE
    vehicle_record.status_reason = "Brake repair at the workshop"
    written_values = _install_scoped_vehicle_repository(monkeypatch, vehicle_record)

    await vehicle_service.update_vehicle(
        fake_db_session(),
        vehicle_record.vehicle_id,
        VehicleUpdateRequest(status=VehicleStatus.ACTIVE),
        principal=build_principal(organization_id=vehicle_record.organization_id),
    )

    assert "status_reason" in written_values
    assert written_values["status_reason"] is None
