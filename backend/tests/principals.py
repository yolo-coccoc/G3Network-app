"""Lean builder of a `Principal` for service and router tests (WP2b)."""

from uuid import UUID, uuid4

from app.domains.identity.types import Principal, UserRole

# Fixed organization of the default test principal, so a test can build a
# record "of the same organization" or "of another organization" by ID.
DEFAULT_ORGANIZATION_ID = UUID("00000000-0000-0000-0000-0000000000a1")
# The acting user of every test principal. The PostgreSQL integration tests
# insert this user into their temporary database, so history rows and
# `added_by` / `granted_by` columns can point at it.
ACTOR_USER_ID = UUID("00000000-0000-0000-0000-0000000000c3")
OTHER_ORGANIZATION_ID = UUID("00000000-0000-0000-0000-0000000000b2")


def build_principal(
    *,
    roles: frozenset[UserRole] | None = None,
    organization_id: UUID = DEFAULT_ORGANIZATION_ID,
    is_internal: bool = False,
    user_id: UUID | None = None,
) -> Principal:
    """Build a principal; by default a customer ORG_ADMIN of the default org.

    Args:
        roles: Roles held; defaults to ``{ORG_ADMIN}``.
        organization_id: The organization the session acts for.
        is_internal: Whether that organization is one of ours.
        user_id: The person; the shared test actor when omitted.

    Returns:
        A `Principal` with random membership and session IDs.
    """
    return Principal(
        user_id=user_id or ACTOR_USER_ID,
        membership_id=uuid4(),
        organization_id=organization_id,
        session_id=uuid4(),
        roles=roles if roles is not None else frozenset({UserRole.ORG_ADMIN}),
        is_internal=is_internal,
    )


def build_internal_principal(*, roles: frozenset[UserRole] | None = None) -> Principal:
    """Build an internal principal (sees every organization).

    Args:
        roles: Roles held; defaults to ``{HEAD_ADMIN}``.

    Returns:
        A `Principal` of an internal organization.
    """
    return build_principal(
        roles=roles if roles is not None else frozenset({UserRole.HEAD_ADMIN}),
        organization_id=uuid4(),
        is_internal=True,
    )
