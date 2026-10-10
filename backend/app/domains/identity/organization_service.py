"""Organizations and their settings (ACC-01, ACC-02, ACC-08, ID-45).

Business rules for the registry of every party that uses the system: creating
a customer or partner with its first ORG_ADMIN, editing the profile, changing
the lifecycle status (a closed organization is soft-deleted, DM-25), assigning
the account manager, and the organization's own settings. Every change to a
tracked row sets the change context with the caller and the typed reason first.

Limitations: the tax code is only checked for length and digits against the
legal form (ID-05), not against the tax authority.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.member_service as member_service
import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import (
    AccessDeniedError,
    AccountManagerInvalidError,
    OrganizationConflictError,
    OrganizationInvalidError,
    OrganizationNotFoundError,
)
from app.domains.identity.models import OrganizationModel, OrganizationSettingModel
from app.domains.identity.schemas import (
    AccountManagerRequest,
    OrganizationCreateRequest,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdateRequest,
    OrganizationStatusRequest,
    OrganizationUpdateRequest,
)
from app.domains.identity.types import (
    INTERNAL_ADMIN_ROLES,
    ORGANIZATION_CREATOR_ROLES,
    OrganizationLegalForm,
    OrganizationReference,
    OrganizationSettingsReference,
    OrganizationStatus,
    Principal,
    UserRole,
)
from app.libs.common.clock import utc_now
from app.libs.common.pagination import normalize_page_window
from app.libs.db.history import set_change_context

# Tax-code lengths per legal form (ID-05): a company has 10 digits (13 for a
# branch); an individual uses the 12-digit citizen ID.
_COMPANY_TAX_CODE_LENGTHS = (10, 13)
_INDIVIDUAL_TAX_CODE_LENGTH = 12

# Defaults of a new `organization_settings` row, used when a row is missing.
DEFAULT_TELEMETRY_INTERVAL_SECONDS = 10
DEFAULT_DRIVING_SESSION_AUTO_END_MINUTES = 120

# Profile fields an ORG_ADMIN may edit on their own organization; the rest
# (legal name, tax code) print on invoices and belong to our staff.
_ORG_ADMIN_EDITABLE_FIELDS = frozenset({"display_name", "address"})


def _is_internal_with_role(principal: Principal, roles: frozenset[UserRole]) -> bool:
    """Tell whether the caller is internal and holds one of the roles."""
    return principal.is_internal and principal.has_any_role(*roles)


def normalize_tax_code(legal_form: OrganizationLegalForm, tax_code: str) -> str:
    """Check a tax code against the legal form and drop separators (ID-05).

    Args:
        legal_form: Company or individual.
        tax_code: The code as typed; dashes and spaces are ignored.

    Returns:
        The digits only.

    Raises:
        OrganizationInvalidError: The code is not numeric, or its length does
            not fit the legal form.
    """
    digits = tax_code.replace("-", "").replace(" ", "")
    if not digits.isdigit():
        raise OrganizationInvalidError("The tax code must contain digits only")
    if legal_form is OrganizationLegalForm.COMPANY:
        if len(digits) not in _COMPANY_TAX_CODE_LENGTHS:
            raise OrganizationInvalidError("A company tax code has 10 or 13 digits")
    elif len(digits) != _INDIVIDUAL_TAX_CODE_LENGTH:
        raise OrganizationInvalidError(
            "An individual's tax code is the 12-digit citizen ID"
        )
    return digits


def to_organization_response(
    organization_record: OrganizationModel,
) -> OrganizationResponse:
    """Map an organization row to its response (pure mapping, no I/O)."""
    return OrganizationResponse.model_validate(organization_record)


async def get_reachable_organization(
    db_session: AsyncSession, principal: Principal, organization_id: UUID
) -> OrganizationModel:
    """Load an organization the caller's data reach includes (ACC-15).

    Args:
        db_session: Current database session.
        principal: The caller.
        organization_id: The organization.

    Returns:
        The organization row, a closed one included.

    Raises:
        OrganizationNotFoundError: It does not exist or is out of reach (the
            same answer, so IDs cannot be probed).
    """
    organization_record = await identity_repository.get_organization(
        db_session, organization_id
    )
    if organization_record is None or not principal.can_access_organization(
        organization_id
    ):
        raise OrganizationNotFoundError("The organization does not exist")
    return organization_record


async def create_organization(
    db_session: AsyncSession,
    principal: Principal,
    create_request: OrganizationCreateRequest,
) -> OrganizationResponse:
    """Create an organization with default settings and its first ORG_ADMIN.

    The first administrator receives an INVITED membership holding ORG_ADMIN
    and an SMS invitation (ID-15). Only our HEAD_ADMIN may create an internal
    organization.

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN / SALES).
        create_request: Profile and first administrator.

    Returns:
        The new organization.

    Raises:
        AccessDeniedError: The caller may not create organizations (or an
            internal one).
        OrganizationInvalidError: Tax code does not fit the legal form, or an
            internal organization is not a company.
        OrganizationConflictError: The tax code is used by a live organization.
        MembershipConflictError, UserConflictError, OneTimeCodeRateLimitError:
            Raised while inviting the first administrator.
    """
    if not _is_internal_with_role(principal, ORGANIZATION_CREATOR_ROLES):
        raise AccessDeniedError(
            "Only our sales and administrators create organizations"
        )
    if create_request.is_internal:
        if not principal.has_any_role(UserRole.HEAD_ADMIN):
            raise AccessDeniedError(
                "Only a HEAD_ADMIN creates an internal organization"
            )
        if create_request.legal_form is not OrganizationLegalForm.COMPANY:
            raise OrganizationInvalidError("An internal organization is a company")
    tax_code = (
        None
        if create_request.tax_code is None
        else normalize_tax_code(create_request.legal_form, create_request.tax_code)
    )
    if (
        tax_code is not None
        and await identity_repository.find_live_organization_by_tax_code(
            db_session, tax_code
        )
        is not None
    ):
        raise OrganizationConflictError(
            "This tax code already belongs to an organization"
        )
    try:
        organization_record = await identity_repository.insert_organization(
            db_session,
            {
                "is_internal": create_request.is_internal,
                "legal_form": create_request.legal_form.value,
                "display_name": create_request.display_name,
                "legal_name": create_request.legal_name,
                "tax_code": tax_code,
                "address": create_request.address,
                "status": OrganizationStatus.ACTIVE.value,
            },
        )
    except IntegrityError as error:
        raise OrganizationConflictError(
            "This tax code already belongs to an organization"
        ) from error
    await identity_repository.insert_organization_settings(
        db_session, organization_record.organization_id
    )
    await member_service.add_invited_member(
        db_session,
        invited_by=principal.user_id,
        organization_record=organization_record,
        phone_number=create_request.first_admin.phone_number,
        full_name=create_request.first_admin.full_name,
        roles=[UserRole.ORG_ADMIN],
    )
    return to_organization_response(organization_record)


async def get_organization(
    db_session: AsyncSession, principal: Principal, organization_id: UUID
) -> OrganizationResponse:
    """Read one organization inside the caller's data reach (ACC-01).

    Args:
        db_session: Current database session.
        principal: The caller.
        organization_id: The organization.

    Returns:
        The organization.

    Raises:
        OrganizationNotFoundError: It does not exist or is out of reach.
    """
    return to_organization_response(
        await get_reachable_organization(db_session, principal, organization_id)
    )


async def list_organizations(
    db_session: AsyncSession,
    principal: Principal,
    *,
    page: int,
    page_size: int,
    search_text: str | None,
    status: OrganizationStatus | None,
) -> OrganizationListResponse:
    """List organizations: all for internal callers, only their own otherwise.

    Args:
        db_session: Current database session.
        principal: The caller.
        page: Page number from 1.
        page_size: Rows per page.
        search_text: Substring of a name or the tax code.
        status: Only this status.

    Returns:
        A page of organizations, newest first.
    """
    page_window = normalize_page_window(page, page_size)
    only_organization_id = None if principal.is_internal else principal.organization_id
    status_value = None if status is None else status.value
    organization_records = await identity_repository.list_organizations(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        search_text=search_text,
        status=status_value,
        only_organization_id=only_organization_id,
    )
    total = await identity_repository.count_organizations(
        db_session,
        search_text=search_text,
        status=status_value,
        only_organization_id=only_organization_id,
    )
    return OrganizationListResponse(
        items=[to_organization_response(record) for record in organization_records],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_organization(
    db_session: AsyncSession,
    principal: Principal,
    organization_id: UUID,
    update_request: OrganizationUpdateRequest,
) -> OrganizationResponse:
    """Edit an organization's profile with a typed reason (ACC-01).

    Our sales and administrators edit any organization; an ORG_ADMIN edits the
    display name and address of their own.

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        organization_id: The organization.
        update_request: New values (null = unchanged) and the reason.

    Returns:
        The updated organization.

    Raises:
        OrganizationNotFoundError: It does not exist or is out of reach.
        AccessDeniedError: The caller may not edit these fields.
        OrganizationInvalidError: Tax code does not fit the legal form.
        OrganizationConflictError: The tax code belongs to another organization.
    """
    organization_record = await get_reachable_organization(
        db_session, principal, organization_id
    )
    values: dict[str, Any] = update_request.model_dump(
        exclude_unset=True, exclude={"reason"}
    )
    values = {key: value for key, value in values.items() if value is not None}
    is_staff = _is_internal_with_role(principal, ORGANIZATION_CREATOR_ROLES)
    is_own_admin = (
        principal.has_any_role(UserRole.ORG_ADMIN)
        and principal.organization_id == organization_id
    )
    if not is_staff and not (
        is_own_admin and set(values) <= _ORG_ADMIN_EDITABLE_FIELDS
    ):
        raise AccessDeniedError("You may not edit these fields of the organization")
    if "tax_code" in values:
        values["tax_code"] = normalize_tax_code(
            OrganizationLegalForm(organization_record.legal_form), values["tax_code"]
        )
        other_organization = (
            await identity_repository.find_live_organization_by_tax_code(
                db_session, values["tax_code"]
            )
        )
        if (
            other_organization is not None
            and other_organization.organization_id != organization_id
        ):
            raise OrganizationConflictError(
                "This tax code already belongs to an organization"
            )
    if not values:
        return to_organization_response(organization_record)
    await set_change_context(
        db_session, changed_by=principal.user_id, change_reason=update_request.reason
    )
    try:
        await identity_repository.apply_organization_values(
            db_session, organization_record, values
        )
    except IntegrityError as error:
        raise OrganizationConflictError(
            "This tax code already belongs to an organization"
        ) from error
    return to_organization_response(organization_record)


async def change_organization_status(
    db_session: AsyncSession,
    principal: Principal,
    organization_id: UUID,
    status_request: OrganizationStatusRequest,
) -> OrganizationResponse:
    """Suspend, close or reactivate an organization (ACC-01, ID-06, ID-34).

    SUSPENDED and CLOSED block the members' logins but leave their memberships
    as they are. CLOSED soft-deletes the row (DM-25); reactivating clears it.
    Sessions that were acting for the organization lose it, and the members'
    running driving sessions end through the membership-end hooks (DR-10).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        organization_id: The organization.
        status_request: The new status and the reason.

    Returns:
        The updated organization.

    Raises:
        AccessDeniedError: The caller is not an internal administrator.
        OrganizationNotFoundError: The organization does not exist.
        OrganizationConflictError: The status is unchanged, an internal
            organization would be suspended or closed, or reopening clashes
            with a tax code taken meanwhile.
    """
    if not _is_internal_with_role(principal, INTERNAL_ADMIN_ROLES):
        raise AccessDeniedError(
            "Only our administrators change an organization's status"
        )
    organization_record = await get_reachable_organization(
        db_session, principal, organization_id
    )
    new_status = status_request.status
    if organization_record.status == new_status.value:
        raise OrganizationConflictError("The organization already has this status")
    if organization_record.is_internal and new_status is not OrganizationStatus.ACTIVE:
        raise OrganizationConflictError(
            "An internal organization cannot be suspended or closed"
        )
    values: dict[str, Any] = {
        "status": new_status.value,
        "status_reason": None
        if new_status is OrganizationStatus.ACTIVE
        else status_request.reason,
        "deleted_at": utc_now() if new_status is OrganizationStatus.CLOSED else None,
    }
    await set_change_context(
        db_session, changed_by=principal.user_id, change_reason=status_request.reason
    )
    try:
        await identity_repository.apply_organization_values(
            db_session, organization_record, values
        )
    except IntegrityError as error:
        raise OrganizationConflictError(
            "The tax code was taken by another organization meanwhile"
        ) from error
    if new_status is not OrganizationStatus.ACTIVE:
        await identity_repository.clear_session_organization(
            db_session, user_id=None, organization_id=organization_id
        )
        await member_service.run_membership_end_hooks_for_organization(
            db_session,
            organization_id=organization_id,
            acting_user_id=principal.user_id,
            reason=status_request.reason,
        )
    return to_organization_response(organization_record)


async def assign_account_manager(
    db_session: AsyncSession,
    principal: Principal,
    organization_id: UUID,
    manager_request: AccountManagerRequest,
) -> OrganizationResponse:
    """Assign or clear the organization's account manager (ACC-02, ID-07).

    Reassignments are kept in `organization_history`.

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller (internal SALES / CO_ADMIN / HEAD_ADMIN).
        organization_id: The organization.
        manager_request: The manager (or null to clear) and the reason.

    Returns:
        The updated organization.

    Raises:
        AccessDeniedError: The caller may not assign managers.
        OrganizationNotFoundError: The organization does not exist.
        AccountManagerInvalidError: The user is not an active SALES member of
            an internal organization.
    """
    if not _is_internal_with_role(principal, ORGANIZATION_CREATOR_ROLES):
        raise AccessDeniedError("Only our sales and administrators assign managers")
    organization_record = await get_reachable_organization(
        db_session, principal, organization_id
    )
    manager_id = manager_request.account_manager_id
    if (
        manager_id is not None
        and not await identity_repository.is_active_internal_sales_user(
            db_session, manager_id
        )
    ):
        raise AccountManagerInvalidError(
            "The account manager must be an active SALES user of our organization"
        )
    await set_change_context(
        db_session, changed_by=principal.user_id, change_reason=manager_request.reason
    )
    await identity_repository.apply_organization_values(
        db_session, organization_record, {"account_manager_id": manager_id}
    )
    return to_organization_response(organization_record)


async def _get_or_create_settings(
    db_session: AsyncSession, organization_id: UUID
) -> OrganizationSettingModel:
    """Return an organization's settings row, creating the default one if missing."""
    settings_record = await identity_repository.get_organization_settings(
        db_session, organization_id
    )
    if settings_record is None:
        settings_record = await identity_repository.insert_organization_settings(
            db_session, organization_id
        )
    return settings_record


async def get_organization_settings(
    db_session: AsyncSession, principal: Principal, organization_id: UUID
) -> OrganizationSettingsResponse:
    """Read an organization's own settings (ID-45).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        organization_id: The organization.

    Returns:
        The settings (a default row is created if the organization has none).

    Raises:
        OrganizationNotFoundError: It does not exist or is out of reach.
    """
    await get_reachable_organization(db_session, principal, organization_id)
    return OrganizationSettingsResponse.model_validate(
        await _get_or_create_settings(db_session, organization_id)
    )


async def update_organization_settings(
    db_session: AsyncSession,
    principal: Principal,
    organization_id: UUID,
    update_request: OrganizationSettingsUpdateRequest,
) -> OrganizationSettingsResponse:
    """Change an organization's settings with a typed reason (ID-45).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller (the organization's ORG_ADMIN, or our
            HEAD_ADMIN / CO_ADMIN).
        organization_id: The organization.
        update_request: New values (null = unchanged) and the reason.

    Returns:
        The updated settings.

    Raises:
        OrganizationNotFoundError: It does not exist or is out of reach.
        AccessDeniedError: The caller may not change settings.
    """
    await get_reachable_organization(db_session, principal, organization_id)
    is_own_admin = (
        principal.has_any_role(UserRole.ORG_ADMIN)
        and principal.organization_id == organization_id
    )
    if not is_own_admin and not _is_internal_with_role(principal, INTERNAL_ADMIN_ROLES):
        raise AccessDeniedError("Only an organization administrator changes settings")
    settings_record = await _get_or_create_settings(db_session, organization_id)
    values: dict[str, Any] = {
        key: value
        for key, value in update_request.model_dump(exclude={"reason"}).items()
        if value is not None
    }
    if not values:
        return OrganizationSettingsResponse.model_validate(settings_record)
    await set_change_context(
        db_session, changed_by=principal.user_id, change_reason=update_request.reason
    )
    await identity_repository.apply_organization_settings_values(
        db_session, settings_record, values
    )
    return OrganizationSettingsResponse.model_validate(settings_record)


async def find_organization_reference(
    db_session: AsyncSession, organization_id: UUID
) -> OrganizationReference | None:
    """Look an organization up for another domain.

    Args:
        db_session: Current database session.
        organization_id: The organization.

    Returns:
        A minimal reference, or `None` if the organization is unknown.
    """
    organization_record = await identity_repository.get_organization(
        db_session, organization_id
    )
    if organization_record is None:
        return None
    return OrganizationReference(
        organization_id=organization_record.organization_id,
        display_name=organization_record.display_name,
        is_internal=organization_record.is_internal,
        legal_form=organization_record.legal_form,
        status=organization_record.status,
    )


async def resolve_organization_settings(
    db_session: AsyncSession, organization_id: UUID
) -> OrganizationSettingsReference | None:
    """Read an organization's settings for another domain without creating a row.

    Args:
        db_session: Current database session.
        organization_id: The organization.

    Returns:
        The settings, defaults when the organization has no row, or `None`
        when the organization is unknown.
    """
    if await identity_repository.get_organization(db_session, organization_id) is None:
        return None
    settings_record = await identity_repository.get_organization_settings(
        db_session, organization_id
    )
    if settings_record is None:
        return OrganizationSettingsReference(
            organization_id=organization_id,
            telemetry_interval_seconds=DEFAULT_TELEMETRY_INTERVAL_SECONDS,
            driving_session_auto_end_minutes=DEFAULT_DRIVING_SESSION_AUTO_END_MINUTES,
        )
    return OrganizationSettingsReference(
        organization_id=organization_id,
        telemetry_interval_seconds=settings_record.telemetry_interval_seconds,
        driving_session_auto_end_minutes=settings_record.driving_session_auto_end_minutes,
    )
