"""Access audit log: writing entries and searching them (ACC-18, ID-21, ID-41).

The log is append-only. Account security events are written by the
authentication code through `record_account_event`; other domains write a
`VIEW` or `EXPORT` entry through the public `record_data_access` in
`service.py`, which forwards here. A view is logged once per screen opened.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import AccessDeniedError, AuditLogInvalidError
from app.domains.identity.schemas import (
    AccessAuditLogListResponse,
    AccessAuditLogResponse,
)
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    Principal,
    UserRole,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window

# Account events are about the account, not about a record of the customer.
ACCOUNT_RESOURCE_TYPE = "USER_ACCOUNT"

# Search window used when the caller gives no start time.
DEFAULT_SEARCH_WINDOW = timedelta(days=30)


async def record_account_event(
    db_session: AsyncSession,
    *,
    user_id: UUID | None,
    organization_id: UUID | None,
    action: AccessAuditAction,
    details: dict[str, Any] | None,
    client_context: ClientContext | None,
) -> None:
    """Append a login or account security event (ID-22).

    Args:
        db_session: Session owned by the entry boundary; the entry is written
            in the same transaction as the event it records.
        user_id: The account the event is about; `None` for a failed login
            with a phone number that matches no account.
        organization_id: The organization involved, if any.
        action: One of the account events of `AccessAuditAction`.
        details: Action-specific facts (e.g. ``{"failure": "WRONG_PASSWORD"}``).
        client_context: IP address and user agent of the request, if known.

    Side Effects:
        Inserts one `access_audit_logs` row; does not commit.
    """
    await identity_repository.insert_audit_log(
        db_session,
        {
            "user_id": user_id,
            "organization_id": organization_id,
            "action": action.value,
            "resource_type": ACCOUNT_RESOURCE_TYPE,
            "resource_id": None if user_id is None else str(user_id),
            "details": details,
            "ip_address": None if client_context is None else client_context.ip_address,
            "user_agent": None if client_context is None else client_context.user_agent,
        },
    )


async def record_data_access(
    db_session: AsyncSession,
    *,
    principal: Principal,
    action: AccessAuditAction,
    resource_type: str,
    resource_id: str | None,
    details: dict[str, Any] | None,
    client_context: ClientContext | None,
    organization_id: UUID | None = None,
) -> None:
    """Append a `VIEW` or `EXPORT` entry for personal or location data.

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller who opened the screen or exported the data.
        action: `VIEW` or `EXPORT`.
        resource_type: Kind of data, e.g. ``VEHICLE_LOCATION_HISTORY``.
        resource_id: ID of the record accessed, as text.
        details: Facts of the action; an `EXPORT` needs ``{"reason": ...}``.
        client_context: IP address and user agent of the request, if known.
        organization_id: Organization whose data was accessed; defaults to
            the caller's organization.

    Raises:
        AuditLogInvalidError: If the action is not a data action, or an
            export has no reason.

    Side Effects:
        Inserts one `access_audit_logs` row; does not commit.
    """
    if action not in (AccessAuditAction.VIEW, AccessAuditAction.EXPORT):
        raise AuditLogInvalidError("Only VIEW and EXPORT are data-access actions")
    if action is AccessAuditAction.EXPORT:
        reason = (details or {}).get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise AuditLogInvalidError("An export needs a reason")
    await identity_repository.insert_audit_log(
        db_session,
        {
            "user_id": principal.user_id,
            "organization_id": organization_id or principal.organization_id,
            "action": action.value,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details,
            "ip_address": None if client_context is None else client_context.ip_address,
            "user_agent": None if client_context is None else client_context.user_agent,
        },
    )


async def search_audit_logs(
    db_session: AsyncSession,
    principal: Principal,
    *,
    user_id: UUID | None,
    organization_id: UUID | None,
    action: AccessAuditAction | None,
    resource_type: str | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
    page: int,
    page_size: int,
) -> AccessAuditLogListResponse:
    """Search the audit log (ACC-18).

    Our HEAD_ADMIN / CO_ADMIN search every organization; an ORG_ADMIN searches
    only their own organization, whatever `organization_id` says.

    Args:
        db_session: Current database session.
        principal: The caller.
        user_id: Only entries about or by this user.
        organization_id: Only this organization (internal administrators).
        action: Only this action.
        resource_type: Only this resource type.
        occurred_from: Start of the range; defaults to 30 days before the end.
        occurred_to: End of the range; defaults to now.
        page: Page number from 1.
        page_size: Rows per page.

    Returns:
        A page of entries, newest first.

    Raises:
        AccessDeniedError: If the caller is neither an internal administrator
            nor an ORG_ADMIN.
        AuditLogInvalidError: If the range is reversed or longer than
            `IDENTITY_AUDIT_LOG_MAX_RANGE_DAYS`.

    Side Effects:
        Read-only; does not commit.
    """
    is_internal_admin = principal.is_internal and principal.has_any_role(
        UserRole.HEAD_ADMIN, UserRole.CO_ADMIN
    )
    if not is_internal_admin:
        if not principal.has_any_role(UserRole.ORG_ADMIN):
            raise AccessDeniedError("Only administrators may read the audit log")
        organization_id = principal.organization_id
    range_end = occurred_to or utc_now()
    range_start = occurred_from or range_end - DEFAULT_SEARCH_WINDOW
    if range_start > range_end:
        raise AuditLogInvalidError("The start of the range is after its end")
    if range_end - range_start > timedelta(
        days=settings.IDENTITY_AUDIT_LOG_MAX_RANGE_DAYS
    ):
        raise AuditLogInvalidError(
            f"The range may not be longer than "
            f"{settings.IDENTITY_AUDIT_LOG_MAX_RANGE_DAYS} days"
        )
    page_window = normalize_page_window(page, page_size)
    filters: dict[str, Any] = {
        "user_id": user_id,
        "organization_id": organization_id,
        "action": None if action is None else action.value,
        "resource_type": resource_type,
        "occurred_from": range_start,
        "occurred_to": range_end,
    }
    audit_records = await identity_repository.list_audit_logs(
        db_session, offset=page_window.offset, limit=page_window.page_size, **filters
    )
    total = await identity_repository.count_audit_logs(db_session, **filters)
    return AccessAuditLogListResponse(
        items=[
            AccessAuditLogResponse.model_validate(audit_record)
            for audit_record in audit_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )
