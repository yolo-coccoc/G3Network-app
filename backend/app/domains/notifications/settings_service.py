"""Organization channel settings: push and e-mail per kind of alert (NTF-05, NT-12).

The in-app and portal inbox always shows every alert; an organization only
switches push and e-mail, per kind of alert (a kind belongs to one service, so
switching it switches that part of the service, NT-03). Only the organization's
choices are stored: a kind with no row uses the default from the routing table
(push on, e-mail on for the critical kinds), and the row is created the first
time the ORG_ADMIN saves the kind. The table is change-tracked, so every save
records who changed it and why.

Access: the organization's ORG_ADMIN (and our administrators, NTF-05) read and
change their own organization's switches; internal staff may name any
organization. The route-level role gate is in the router; this module applies
the data scope.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.service as identity_service
import app.domains.notifications.repository as notification_repository
from app.domains.identity.types import Principal
from app.domains.notifications.exceptions import (
    NotificationSettingOrganizationNotFoundError,
)
from app.domains.notifications.routing import get_routing_rule
from app.domains.notifications.schemas import (
    NotificationSettingListResponse,
    NotificationSettingResponse,
    NotificationSettingUpdateRequest,
)
from app.domains.notifications.types import ChannelSwitches, NotificationType

# History reason used when the administrator types none (a routine save).
SETTING_CHANGED_REASON = "Notification channels changed"


def build_default_switches(notification_type: NotificationType) -> ChannelSwitches:
    """Return the default switches of a kind of alert (NT-12).

    Args:
        notification_type: The kind of alert.

    Returns:
        The defaults from its routing rule, marked as defaults.
    """
    rule = get_routing_rule(notification_type)
    return ChannelSwitches(
        push_enabled=rule.push_by_default,
        email_enabled=rule.email_by_default,
        is_default=True,
    )


async def resolve_channel_switches(
    db_session: AsyncSession,
    organization_id: UUID,
    notification_type: NotificationType,
) -> ChannelSwitches:
    """Get the push and e-mail switches that apply to one alert.

    Args:
        db_session: Session owned by the entry boundary.
        organization_id: The organization the alert belongs to.
        notification_type: The kind of alert.

    Returns:
        The organization's saved switches, or the defaults without a row.

    Side Effects:
        One read-only query.
    """
    setting_record = await notification_repository.find_setting(
        db_session, organization_id, notification_type.value
    )
    if setting_record is None:
        return build_default_switches(notification_type)
    return ChannelSwitches(
        push_enabled=setting_record.push_enabled,
        email_enabled=setting_record.email_enabled,
        is_default=False,
    )


async def _resolve_target_organization_id(
    db_session: AsyncSession,
    principal: Principal,
    organization_id: UUID | None,
) -> UUID:
    """Pick the organization a settings call is about and check the reach.

    Args:
        db_session: Session owned by the HTTP boundary.
        principal: The caller.
        organization_id: The organization named in the request, if any.

    Returns:
        The caller's own organization when none is named, else the named one.

    Raises:
        NotificationSettingOrganizationNotFoundError: The organization is out
            of the caller's reach or does not exist.
    """
    target_organization_id = organization_id or principal.organization_id
    if not principal.can_access_organization(target_organization_id):
        raise NotificationSettingOrganizationNotFoundError(
            f"Organization '{target_organization_id}' not found"
        )
    if (
        target_organization_id != principal.organization_id
        and await identity_service.find_organization_reference(
            db_session, target_organization_id
        )
        is None
    ):
        raise NotificationSettingOrganizationNotFoundError(
            f"Organization '{target_organization_id}' not found"
        )
    return target_organization_id


async def list_organization_notification_settings(
    db_session: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID | None = None,
) -> NotificationSettingListResponse:
    """List every kind of alert with the organization's effective switches.

    Args:
        db_session: Session owned by the HTTP boundary.
        principal: The caller (an ORG_ADMIN or our administrator).
        organization_id: The organization to read; internal staff only,
            otherwise the caller's own.

    Returns:
        One entry per kind of alert, saved values where the organization
        has a row and defaults elsewhere.

    Raises:
        NotificationSettingOrganizationNotFoundError: Out of reach.
    """
    target_organization_id = await _resolve_target_organization_id(
        db_session, principal, organization_id
    )
    saved_by_type = {
        record.notification_type: record
        for record in await notification_repository.list_settings_by_organization(
            db_session, target_organization_id
        )
    }
    entries: list[NotificationSettingResponse] = []
    for notification_type in NotificationType:
        saved = saved_by_type.get(notification_type.value)
        if saved is None:
            defaults = build_default_switches(notification_type)
            entries.append(
                NotificationSettingResponse(
                    notification_type=notification_type,
                    push_enabled=defaults.push_enabled,
                    email_enabled=defaults.email_enabled,
                    is_default=True,
                    updated_at=None,
                )
            )
        else:
            entries.append(
                NotificationSettingResponse(
                    notification_type=notification_type,
                    push_enabled=saved.push_enabled,
                    email_enabled=saved.email_enabled,
                    is_default=False,
                    updated_at=saved.updated_at,
                )
            )
    return NotificationSettingListResponse(
        organization_id=target_organization_id, settings=entries
    )


async def update_organization_notification_setting(
    db_session: AsyncSession,
    notification_type: NotificationType,
    update_request: NotificationSettingUpdateRequest,
    *,
    principal: Principal,
    organization_id: UUID | None = None,
) -> NotificationSettingResponse:
    """Save an organization's switches for one kind of alert.

    Args:
        db_session: Session owned by the HTTP boundary.
        notification_type: The kind of alert.
        update_request: The new switches and an optional reason.
        principal: The caller (an ORG_ADMIN or our administrator).
        organization_id: The organization to change; internal staff only,
            otherwise the caller's own.

    Returns:
        The saved switches.

    Raises:
        NotificationSettingOrganizationNotFoundError: Out of reach.

    Side Effects:
        Creates the row on the first save (lazy), else updates it and writes
        the old row to ``organization_notification_setting_history`` with the
        caller and the reason; does not commit.
    """
    target_organization_id = await _resolve_target_organization_id(
        db_session, principal, organization_id
    )
    saved = await notification_repository.upsert_setting(
        db_session,
        organization_id=target_organization_id,
        notification_type=notification_type.value,
        push_enabled=update_request.push_enabled,
        email_enabled=update_request.email_enabled,
        changed_by=principal.user_id,
        change_reason=update_request.reason or SETTING_CHANGED_REASON,
    )
    return NotificationSettingResponse(
        notification_type=notification_type,
        push_enabled=saved.push_enabled,
        email_enabled=saved.email_enabled,
        is_default=False,
        updated_at=saved.updated_at,
    )
