"""Public service of the identity domain.

The functions other domains call: lookups of a membership's person, of an
organization and its settings, and the helper that writes the personal-data
access audit log. Authentication of a request is exposed to FastAPI through
`dependencies.py` (`get_current_principal`, `require_roles`). Everything else
in the domain (accounts, login, members, roles, consent) is reached over HTTP
only. Other domains never import the identity models or repository.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.audit_service as audit_service
import app.domains.identity.member_service as member_service
import app.domains.identity.organization_service as organization_service
import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import OrganizationNotFoundError
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    MembershipEndHook,
    MembershipPersonReference,
    OrganizationReference,
    OrganizationSettingsReference,
    Principal,
    UserRole,
)


async def resolve_membership_person_reference(
    db_session: AsyncSession,
    membership_id: UUID,
) -> MembershipPersonReference | None:
    """Find a membership and the person behind it, as an internal DTO.

    Args:
        db_session: Database session owned by the entry boundary.
        membership_id: Internal ID of the membership.

    Returns:
        `MembershipPersonReference`, or `None` if the membership is unknown.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    found = await identity_repository.find_membership_with_user(
        db_session, membership_id
    )
    if found is None:
        return None
    membership_record, user_record = found
    return MembershipPersonReference(
        membership_id=membership_record.membership_id,
        organization_id=membership_record.organization_id,
        user_id=user_record.user_id,
        full_name=user_record.full_name,
        phone_number=user_record.phone_number,
        membership_status=membership_record.status,
        user_status=user_record.status,
        left_at=membership_record.left_at,
    )


async def find_organization_reference(
    db_session: AsyncSession, organization_id: UUID
) -> OrganizationReference | None:
    """Look an organization up by ID, for example to validate a foreign key.

    Args:
        db_session: Database session owned by the entry boundary.
        organization_id: Internal ID of the organization.

    Returns:
        `OrganizationReference`, or `None` if the organization is unknown (a
        closed organization is still returned; its `status` says so).

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    return await organization_service.find_organization_reference(
        db_session, organization_id
    )


async def resolve_organization_settings(
    db_session: AsyncSession, organization_id: UUID
) -> OrganizationSettingsReference | None:
    """Read the settings an organization chose for itself (ID-45).

    Args:
        db_session: Database session owned by the entry boundary.
        organization_id: Internal ID of the organization.

    Returns:
        The settings (defaults when the row is missing), or `None` if the
        organization is unknown.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    return await organization_service.resolve_organization_settings(
        db_session, organization_id
    )


async def record_data_access(
    db_session: AsyncSession,
    *,
    principal: Principal,
    action: AccessAuditAction,
    resource_type: str,
    resource_id: str | None,
    client_context: ClientContext | None,
    details: dict[str, Any] | None = None,
    organization_id: UUID | None = None,
) -> None:
    """Write a `VIEW` or `EXPORT` row of the personal-data access audit log (ACC-18).

    Call it once when a screen showing personal or location data is opened
    (not per refresh), and for every export.

    Args:
        db_session: Database session owned by the entry boundary; the row is
            written in the same transaction as the request.
        principal: The caller who accessed the data.
        action: `AccessAuditAction.VIEW` or `AccessAuditAction.EXPORT`.
        resource_type: Kind of data, e.g. ``VEHICLE_LOCATION_HISTORY``.
        resource_id: ID of the record accessed, as text.
        client_context: IP address and user agent of the request (the
            `get_client_context` dependency).
        details: Facts of the action; an export needs ``{"reason": ...}``.
        organization_id: Organization whose data was accessed; defaults to
            the caller's organization.

    Raises:
        AuditLogInvalidError: The action is not a data action, or an export
            has no reason.

    Side Effects:
        Inserts one `access_audit_logs` row; does not commit.
    """
    await audit_service.record_data_access(
        db_session,
        principal=principal,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        client_context=client_context,
        organization_id=organization_id,
    )


async def resolve_organization_for_new_record(
    db_session: AsyncSession,
    principal: Principal,
    requested_organization_id: UUID | None,
) -> UUID:
    """Decide which organization owns a record the caller is creating (DM-24).

    The owner is the caller's own organization. Internal staff may name
    another organization (a truck sold to a customer, a station of a
    customer); that organization must exist.

    Args:
        db_session: Database session owned by the entry boundary.
        principal: The caller.
        requested_organization_id: The ``organization_id`` of the request, if
            the request carries one.

    Returns:
        The organization to write on the new record.

    Raises:
        OrganizationNotFoundError: The named organization does not exist, or a
            non-internal caller named an organization other than their own
            (out of reach looks like missing, 404).

    Side Effects:
        One read-only query when another organization is named.
    """
    if (
        requested_organization_id is None
        or requested_organization_id == principal.organization_id
    ):
        return principal.organization_id
    if not principal.can_access_organization(requested_organization_id):
        raise OrganizationNotFoundError(
            f"Organization '{requested_organization_id}' not found"
        )
    if await find_organization_reference(db_session, requested_organization_id) is None:
        raise OrganizationNotFoundError(
            f"Organization '{requested_organization_id}' not found"
        )
    return requested_organization_id


async def list_organization_role_holder_user_ids(
    db_session: AsyncSession,
    organization_id: UUID,
    roles: Sequence[UserRole],
) -> list[UUID]:
    """List the people of one organization who hold one of the given roles.

    Used by producers of organization alerts (a silent device, DEV-05) to
    decide who receives them: it answers "who is the organization's
    administrator or fleet manager" without the caller reading identity tables.

    Args:
        db_session: Database session owned by the entry boundary.
        organization_id: The organization whose members are searched.
        roles: Roles that qualify; a person holding any of them is returned.

    Returns:
        User IDs of active members holding a role right now (each once);
        empty when nobody does.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    return await identity_repository.list_user_ids_holding_roles_in_organization(
        db_session, organization_id, [role.value for role in roles]
    )


async def membership_holds_role(
    db_session: AsyncSession, membership_id: UUID, role: UserRole
) -> bool:
    """Tell whether a membership holds a role right now.

    Args:
        db_session: Database session owned by the entry boundary.
        membership_id: Internal ID of the membership.
        role: The role asked about.

    Returns:
        True when the role is assigned to the membership and not revoked.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    return (
        await identity_repository.find_active_role_assignment(
            db_session, membership_id=membership_id, role=role.value
        )
        is not None
    )


async def search_membership_ids_by_person(
    db_session: AsyncSession,
    search_text: str,
    *,
    organization_id: UUID | None,
    limit: int = 500,
) -> list[UUID]:
    """Find memberships whose person's name or phone number contains a text.

    Used by a list that searches by person (the driver list) while the name
    lives on the user, another domain's table.

    Args:
        db_session: Database session owned by the entry boundary.
        search_text: Case-insensitive text to look for.
        organization_id: Only memberships of this organization; `None` means
            every organization.
        limit: Largest number of IDs returned.

    Returns:
        Matching membership IDs, newest first.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    return await identity_repository.search_membership_ids_by_person(
        db_session,
        search_text=search_text,
        organization_id=organization_id,
        limit=limit,
    )


def register_membership_end_hook(hook: MembershipEndHook) -> None:
    """Register a callback run when a membership ends or is locked (DR-10).

    Args:
        hook: Async callable taking the database session and the keyword
            arguments ``membership_id``, ``kind``, ``acting_user_id`` and
            ``reason``; it runs in the same transaction as the change.

    Side Effects:
        Adds the hook to a process-wide list (done once at start-up by
        ``app/api/membership_end_hooks.py``).
    """
    member_service.register_membership_end_hook(hook)
