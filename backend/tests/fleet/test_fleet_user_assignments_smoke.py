"""Smoke tests for the fleet limits of a member and the fleet tree (FL-10, FLT-01).

Fake repositories only; the visible-set computation, the tree move and the
deletion ending assignments run against PostgreSQL in
``tests/test_postgres_integration.py``.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.assignment_service as assignment_service
import app.domains.fleet.repository as fleet_repository
import app.domains.fleet.service as fleet_service
import app.domains.identity.dependencies as identity_dependencies
import app.domains.identity.service as identity_service
from app.domains.fleet.exceptions import (
    FleetNotFoundError,
    FleetUserAssignmentConflictError,
    FleetUserAssignmentOrganizationMismatchError,
)
from app.domains.fleet.models import FleetModel, FleetUserAssignmentModel
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import MembershipPersonReference, UserRole
from tests.builders import build_fleet_record, fake_db_session
from tests.principals import (
    DEFAULT_ORGANIZATION_ID,
    build_internal_principal,
    build_principal,
)


def _assignment(fleet_id: UUID, membership_id: UUID) -> FleetUserAssignmentModel:
    """Build an open assignment row."""
    return FleetUserAssignmentModel(
        fleet_user_assignment_id=uuid4(),
        fleet_id=fleet_id,
        membership_id=membership_id,
        assigned_at=datetime.now(timezone.utc),
        assigned_by=None,
        unassigned_at=None,
        unassigned_by=None,
    )


def _membership_reference(organization_id: UUID) -> MembershipPersonReference:
    """Build the identity reference of a membership of the given organization."""
    return MembershipPersonReference(
        membership_id=uuid4(),
        organization_id=organization_id,
        user_id=uuid4(),
        full_name="Manager",
        phone_number="0900000000",
        membership_status="ACTIVE",
        user_status="ACTIVE",
        left_at=None,
    )


def test_principal_is_fleet_limited_only_for_fleet_level_customer_roles() -> None:
    """Only a customer FLEET_MANAGER / DISPATCHER can be limited (FL-10)."""
    assert build_principal(roles=frozenset({UserRole.FLEET_MANAGER})).is_fleet_limited
    assert build_principal(roles=frozenset({UserRole.DISPATCHER})).is_fleet_limited
    assert not build_principal(roles=frozenset({UserRole.ORG_ADMIN})).is_fleet_limited
    assert not build_principal(
        roles=frozenset({UserRole.FLEET_MANAGER, UserRole.ORG_ADMIN})
    ).is_fleet_limited
    assert not build_internal_principal(
        roles=frozenset({UserRole.FLEET_MANAGER})
    ).is_fleet_limited
    assert not build_principal(roles=frozenset({UserRole.DRIVER})).is_fleet_limited


@pytest.mark.asyncio
async def test_resolve_visible_fleet_ids_is_union_of_assigned_fleets_and_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No assignment means no limit; otherwise assigned fleets plus all below."""
    membership_id = uuid4()
    organization_id = uuid4()
    top = build_fleet_record(organization_id=organization_id)
    child_id, grandchild_id, other_id = uuid4(), uuid4(), uuid4()
    children = {top.fleet_id: [child_id], child_id: [grandchild_id]}
    assignments: list[FleetUserAssignmentModel] = []

    async def list_open(
        db: AsyncSession, membership: UUID
    ) -> list[FleetUserAssignmentModel]:
        return assignments

    async def get_by_id(
        db: AsyncSession, fleet_id: UUID, **_scope: object
    ) -> FleetModel | None:
        return top if fleet_id == top.fleet_id else None

    async def list_child_fleet_ids(db: AsyncSession, fleet_id: UUID) -> list[UUID]:
        return children.get(fleet_id, [])

    monkeypatch.setattr(
        fleet_repository, "list_open_assignments_by_membership", list_open
    )
    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(fleet_repository, "list_child_fleet_ids", list_child_fleet_ids)

    assert (
        await fleet_service.resolve_visible_fleet_ids(
            fake_db_session(), membership_id, organization_id
        )
        is None
    )

    assignments.append(_assignment(top.fleet_id, membership_id))
    visible = await fleet_service.resolve_visible_fleet_ids(
        fake_db_session(), membership_id, organization_id
    )
    assert visible == frozenset({top.fleet_id, child_id, grandchild_id})
    assert other_id not in (visible or set())

    # An assigned fleet that vanished fails closed: an empty set, not "no limit".
    assignments[:] = [_assignment(uuid4(), membership_id)]
    assert (
        await fleet_service.resolve_visible_fleet_ids(
            fake_db_session(), membership_id, organization_id
        )
        == frozenset()
    )


@pytest.mark.asyncio
async def test_limited_manager_cannot_reach_fleet_outside_their_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fleet outside the visible set is not found; a top-level create is refused."""
    principal = build_principal(roles=frozenset({UserRole.FLEET_MANAGER}))
    outside = build_fleet_record(organization_id=principal.organization_id)
    inside_id = uuid4()

    async def get_by_id(
        db: AsyncSession, fleet_id: UUID, **_scope: object
    ) -> FleetModel:
        return outside

    async def visible_ids(
        db: AsyncSession, principal_arg: object
    ) -> frozenset[UUID] | None:
        return frozenset({inside_id})

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        fleet_service, "resolve_principal_visible_fleet_ids", visible_ids
    )

    with pytest.raises(FleetNotFoundError):
        await fleet_service.get_fleet(
            fake_db_session(), outside.fleet_id, principal=principal
        )


@pytest.mark.asyncio
async def test_assign_fleet_rejects_other_organization_and_warns_on_covered_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fleet and the membership share an organization; a covered fleet warns."""
    principal = build_principal()
    parent_id = uuid4()
    fleet = build_fleet_record(organization_id=DEFAULT_ORGANIZATION_ID)
    fleet.parent_fleet_id = parent_id
    membership = _membership_reference(DEFAULT_ORGANIZATION_ID)
    held: set[tuple[UUID, UUID]] = {(parent_id, membership.membership_id)}

    async def resolve_membership(
        db: AsyncSession, membership_id: UUID
    ) -> MembershipPersonReference:
        return membership

    async def get_by_id(
        db: AsyncSession, fleet_id: UUID, **_scope: object
    ) -> FleetModel | None:
        return fleet if fleet_id == fleet.fleet_id else None

    async def find_open(
        db: AsyncSession, fleet_id: UUID, membership_id: UUID
    ) -> FleetUserAssignmentModel | None:
        if (fleet_id, membership_id) in held:
            return _assignment(fleet_id, membership_id)
        return None

    async def insert_assignment(
        db: AsyncSession, **kwargs: object
    ) -> FleetUserAssignmentModel:
        assert kwargs["assigned_by"] == principal.user_id
        return _assignment(fleet.fleet_id, membership.membership_id)

    monkeypatch.setattr(
        identity_service, "resolve_membership_person_reference", resolve_membership
    )
    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(fleet_repository, "find_open_assignment", find_open)
    monkeypatch.setattr(fleet_repository, "insert_assignment", insert_assignment)

    response = await assignment_service.assign_fleet_to_membership(
        fake_db_session(), membership.membership_id, fleet.fleet_id, principal=principal
    )
    assert response.warnings == [assignment_service.WARNING_COVERED_BY_ASSIGNED_PARENT]

    # The same fleet held already: conflict.
    held.add((fleet.fleet_id, membership.membership_id))
    with pytest.raises(FleetUserAssignmentConflictError):
        await assignment_service.assign_fleet_to_membership(
            fake_db_session(),
            membership.membership_id,
            fleet.fleet_id,
            principal=principal,
        )

    # A membership of another organization (internal staff may reach it).
    other_membership = _membership_reference(uuid4())

    async def resolve_other(
        db: AsyncSession, membership_id: UUID
    ) -> MembershipPersonReference:
        return other_membership

    monkeypatch.setattr(
        identity_service, "resolve_membership_person_reference", resolve_other
    )
    with pytest.raises(FleetUserAssignmentOrganizationMismatchError):
        await assignment_service.assign_fleet_to_membership(
            fake_db_session(),
            other_membership.membership_id,
            fleet.fleet_id,
            principal=build_internal_principal(),
        )


def test_build_fleet_tree_node_rolls_vehicle_counts_up_to_parents() -> None:
    """A node's roll-up count is its own trucks plus those of the fleets below."""
    organization_id = uuid4()
    top = build_fleet_record(organization_id=organization_id)
    child = build_fleet_record(organization_id=organization_id)
    child.parent_fleet_id = top.fleet_id
    grandchild = build_fleet_record(organization_id=organization_id)
    grandchild.parent_fleet_id = child.fleet_id

    node = fleet_service.build_fleet_tree_node(
        top,
        {top.fleet_id: [child], child.fleet_id: [grandchild]},
        {top.fleet_id: 1, child.fleet_id: 2, grandchild.fleet_id: 4},
        set(),
    )

    assert node.vehicle_count == 1
    assert node.vehicle_count_with_descendants == 7
    assert node.children[0].children[0].vehicle_count_with_descendants == 4


@pytest.mark.asyncio
async def test_visible_vehicle_dependency_fails_closed_without_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A limited caller gets no trucks if no resolver was registered."""
    monkeypatch.setattr(identity_dependencies, "_visible_vehicle_resolver", None)
    limited = build_principal(roles=frozenset({UserRole.FLEET_MANAGER}))
    unlimited = build_principal()

    assert (
        await identity_dependencies.get_visible_vehicle_ids(limited, fake_db_session())
        == frozenset()
    )
    assert (
        await identity_dependencies.get_visible_vehicle_ids(
            unlimited, fake_db_session()
        )
        is None
    )


@pytest.mark.asyncio
async def test_limited_manager_cannot_delete_a_fleet_that_is_given_to_someone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting an assigned fleet would end a limit: only the administrator may."""
    principal = build_principal(roles=frozenset({UserRole.FLEET_MANAGER}))
    fleet = build_fleet_record(organization_id=principal.organization_id)

    async def get_by_id(
        db: AsyncSession, fleet_id: UUID, **_scope: object
    ) -> FleetModel:
        return fleet

    async def visible_ids(
        db: AsyncSession, principal_arg: object
    ) -> frozenset[UUID] | None:
        return frozenset({fleet.fleet_id})

    async def count_child_fleets(db: AsyncSession, fleet_id: UUID) -> int:
        return 0

    async def list_open(
        db: AsyncSession, fleet_id: UUID
    ) -> list[FleetUserAssignmentModel]:
        return [_assignment(fleet_id, uuid4())]

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(fleet_repository, "count_child_fleets", count_child_fleets)
    monkeypatch.setattr(fleet_repository, "list_open_assignments_by_fleet", list_open)
    monkeypatch.setattr(
        fleet_service, "resolve_principal_visible_fleet_ids", visible_ids
    )

    with pytest.raises(AccessDeniedError):
        await fleet_service.soft_delete_fleet(
            fake_db_session(), fleet.fleet_id, principal=principal
        )
