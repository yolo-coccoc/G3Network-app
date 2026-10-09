"""Smoke tests for the identity tables' schema (models only, no database)."""

from typing import cast

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Table

from app.domains.identity.models import (
    AccessAuditLogModel,
    MembershipModel,
    OrganizationModel,
    UserModel,
    UserRoleAssignmentModel,
)
from app.libs.db.history import UNSPECIFIED_CHANGE_REASON


def _index_names(table_args: tuple[object, ...]) -> set[str]:
    """Return the names of the indexes declared in a model's table arguments."""
    return {arg.name for arg in table_args if isinstance(arg, Index) and arg.name}


def test_soft_delete_check_constraints_follow_dm_25() -> None:
    """Organizations close with deleted_at; a deleted user is locked (DM-25)."""
    organization_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in cast(Table, OrganizationModel.__table__).constraints
        if isinstance(constraint, CheckConstraint)
    }
    user_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in cast(Table, UserModel.__table__).constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert organization_checks == {
        "ck_organizations_closed_iff_deleted": (
            "(status = 'CLOSED') = (deleted_at IS NOT NULL)"
        )
    }
    assert user_checks == {
        "ck_users_deleted_is_locked": "deleted_at IS NULL OR status = 'LOCKED'"
    }


def test_live_row_rules_are_partial_unique_indexes() -> None:
    """Unique business values hold among live rows only (DM-25)."""
    assert "uq_users_active_phone_number" in _index_names(UserModel.__table_args__)
    assert "uq_users_active_email" in _index_names(UserModel.__table_args__)
    assert "uq_memberships_active" in _index_names(MembershipModel.__table_args__)
    assert "uq_user_role_assignments_one_org_admin" in _index_names(
        UserRoleAssignmentModel.__table_args__
    )


def test_role_assignment_organization_is_bound_to_its_membership() -> None:
    """The two-column foreign key makes a role name its membership's own organization."""
    composite_keys = [
        constraint
        for constraint in cast(Table, UserRoleAssignmentModel.__table__).constraints
        if isinstance(constraint, ForeignKeyConstraint) and len(constraint.columns) == 2
    ]

    assert len(composite_keys) == 1
    assert {column.name for column in composite_keys[0].columns} == {
        "membership_id",
        "organization_id",
    }


def test_access_audit_log_time_column_is_in_the_primary_key() -> None:
    """The hypertable time column must be part of the primary key."""
    primary_key_columns = {
        column.name
        for column in cast(Table, AccessAuditLogModel.__table__).primary_key.columns
    }

    assert primary_key_columns == {"access_audit_log_id", "occurred_at"}


def test_unspecified_change_reason_fits_the_history_column() -> None:
    """The trigger's fallback reason fits change_reason varchar(200)."""
    assert 0 < len(UNSPECIFIED_CHANGE_REASON) <= 200
