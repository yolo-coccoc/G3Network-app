"""Smoke tests for alert routing, channel settings and delivery (NTF-02, 04, 05, 06)."""

from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.service as identity_service
import app.domains.notifications.delivery as notification_delivery
import app.domains.notifications.providers as notification_providers
import app.domains.notifications.recipient_service as recipient_service
import app.domains.notifications.repository as notification_repository
import app.domains.notifications.service as notifications_service
import app.domains.notifications.settings_service as settings_service
from app.domains.identity.types import (
    EmailTargetReference,
    PushTargetReference,
    RoleHolderReference,
    UserRole,
)
from app.domains.notifications.models import OrganizationNotificationSettingModel
from app.domains.notifications.routing import ROUTING_RULES
from app.domains.notifications.schemas import NotificationSettingUpdateRequest
from app.domains.notifications.types import (
    NotificationContext,
    NotificationSeverity,
    NotificationType,
    VehicleAudienceHooks,
)
from tests.builders import fake_db_session
from tests.principals import build_principal

ORGANIZATION_ID = uuid4()
VEHICLE_ID = uuid4()


@pytest.fixture(autouse=True)
def reset_vehicle_audience_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no drivers / fleet hooks registered."""
    monkeypatch.setattr(recipient_service, "_vehicle_audience_hooks", None)


def _context(
    notification_type: NotificationType = NotificationType.SOS_ALERT,
    vehicle_id: UUID | None = VEHICLE_ID,
) -> NotificationContext:
    """Build the context of a stored alert."""
    return NotificationContext(
        notification_id=11,
        organization_id=ORGANIZATION_ID,
        notification_type=notification_type,
        severity=NotificationSeverity.CRITICAL,
        vehicle_id=vehicle_id,
        subject_type="SUPPORT_CASE",
        subject_id=uuid4(),
        title="SOS",
        body="Help",
    )


def test_every_notification_type_has_a_routing_rule() -> None:
    """A new NotificationType without a rule would fail at the first alert."""
    assert set(ROUTING_RULES) == set(NotificationType)


@pytest.mark.asyncio
async def test_sos_goes_to_managers_org_admin_and_internal_customer_care(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SOS: unlimited holders, only the visible limited manager, customer care, no driver."""
    admin = RoleHolderReference(uuid4(), uuid4(), frozenset({UserRole.ORG_ADMIN}))
    sees_truck = RoleHolderReference(
        uuid4(), uuid4(), frozenset({UserRole.FLEET_MANAGER})
    )
    hidden = RoleHolderReference(uuid4(), uuid4(), frozenset({UserRole.FLEET_MANAGER}))
    care_user_id = uuid4()
    asked_roles: dict[str, object] = {}

    async def list_holders(
        db: object, organization_id: UUID, roles: Sequence[UserRole]
    ) -> list[RoleHolderReference]:
        asked_roles["org"] = (organization_id, set(roles))
        return [admin, sees_truck, hidden]

    async def list_internal(db: object, roles: Sequence[UserRole]) -> list[UUID]:
        asked_roles["internal"] = set(roles)
        return [care_user_id]

    async def filter_holders(
        db: object,
        organization_id: UUID,
        vehicle_id: UUID,
        holders: Sequence[RoleHolderReference],
    ) -> list[UUID]:
        assert vehicle_id == VEHICLE_ID
        assert {holder.user_id for holder in holders} == {
            sees_truck.user_id,
            hidden.user_id,
        }
        return [sees_truck.user_id]

    async def no_driver(db: object, vehicle_id: UUID) -> None:
        raise AssertionError("an SOS is not addressed to the driver")

    monkeypatch.setattr(
        identity_service, "list_organization_role_holders", list_holders
    )
    monkeypatch.setattr(
        identity_service, "list_internal_role_holder_user_ids", list_internal
    )
    recipient_service.register_vehicle_audience_hooks(
        VehicleAudienceHooks(
            find_checked_in_driver_user_id=no_driver,
            filter_role_holders_by_vehicle=filter_holders,
        )
    )

    recipients = await recipient_service.resolve_recipient_user_ids(
        fake_db_session(), _context()
    )

    assert set(recipients) == {admin.user_id, sees_truck.user_id, care_user_id}
    assert asked_roles["internal"] == {UserRole.CUSTOMER_CARE}
    assert asked_roles["org"] == (
        ORGANIZATION_ID,
        {UserRole.FLEET_MANAGER, UserRole.ORG_ADMIN},
    )


@pytest.mark.asyncio
async def test_battery_alert_also_reaches_the_checked_in_driver_and_named_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Battery: managers plus the driver at the wheel (NT-07) plus explicit recipients."""
    manager = RoleHolderReference(uuid4(), uuid4(), frozenset({UserRole.FLEET_MANAGER}))
    driver_user_id, named_user_id = uuid4(), uuid4()

    async def list_holders(
        db: object, organization_id: UUID, roles: Sequence[UserRole]
    ) -> list[RoleHolderReference]:
        return [manager, manager]

    async def filter_holders(
        db: object,
        organization_id: UUID,
        vehicle_id: UUID,
        holders: Sequence[RoleHolderReference],
    ) -> list[UUID]:
        return [holder.user_id for holder in holders]

    async def find_driver(db: object, vehicle_id: UUID) -> UUID:
        return driver_user_id

    monkeypatch.setattr(
        identity_service, "list_organization_role_holders", list_holders
    )
    recipient_service.register_vehicle_audience_hooks(
        VehicleAudienceHooks(
            find_checked_in_driver_user_id=find_driver,
            filter_role_holders_by_vehicle=filter_holders,
        )
    )

    recipients = await recipient_service.resolve_recipient_user_ids(
        fake_db_session(),
        _context(NotificationType.BATTERY_ALERT),
        extra_user_ids=[named_user_id],
    )

    assert recipients == [manager.user_id, driver_user_id, named_user_id]


@pytest.mark.asyncio
async def test_trip_assigned_has_no_role_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named-person alert asks identity for nobody and keeps only the producer's list."""

    async def must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("no role lookup for a trip assignment")

    monkeypatch.setattr(
        identity_service, "list_organization_role_holders", must_not_be_called
    )
    driver_user_id = uuid4()

    recipients = await recipient_service.resolve_recipient_user_ids(
        fake_db_session(),
        _context(NotificationType.TRIP_ASSIGNED, vehicle_id=None),
        extra_user_ids=[driver_user_id],
    )

    assert recipients == [driver_user_id]


def test_channel_defaults_are_push_on_and_email_on_only_for_sos() -> None:
    """NT-12: push on for every kind, e-mail on for the critical kinds."""
    assert all(rule.push_by_default for rule in ROUTING_RULES.values())
    assert [
        notification_type
        for notification_type, rule in ROUTING_RULES.items()
        if rule.email_by_default
    ] == [NotificationType.SOS_ALERT]


@pytest.mark.asyncio
async def test_settings_switch_off_push_but_keep_email_in_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Push off suppresses the push only; e-mail still goes to people with an address."""
    user_id = uuid4()
    pushed: list[str] = []
    emailed: list[str] = []

    async def find_setting(
        db: object, organization_id: UUID, notification_type: str
    ) -> OrganizationNotificationSettingModel:
        return OrganizationNotificationSettingModel(
            organization_id=organization_id,
            notification_type=notification_type,
            push_enabled=False,
            email_enabled=True,
        )

    async def push_targets(
        db: object, user_ids: Sequence[UUID]
    ) -> list[PushTargetReference]:
        return [PushTargetReference(user_id, "token-1")]

    async def email_targets(
        db: object, user_ids: Sequence[UUID]
    ) -> list[EmailTargetReference]:
        return [EmailTargetReference(user_id, "a@example.com", "A")]

    class RecordingPush:
        async def send_push(
            self, push_token: str, message: notification_providers.PushMessage
        ) -> notification_providers.PushOutcome:
            pushed.append(push_token)
            return notification_providers.PushOutcome.SENT

    class RecordingEmail:
        async def send_email(self, email: str, subject: str, body: str) -> None:
            emailed.append(email)

    monkeypatch.setattr(notification_repository, "find_setting", find_setting)
    monkeypatch.setattr(identity_service, "list_push_targets_by_user_ids", push_targets)
    monkeypatch.setattr(
        identity_service, "list_email_targets_by_user_ids", email_targets
    )
    monkeypatch.setattr(notification_delivery, "get_push_sender", RecordingPush)
    monkeypatch.setattr(notification_delivery, "get_email_sender", RecordingEmail)

    summary = await notification_delivery.deliver_notification(
        fake_db_session(), _context(), [user_id]
    )

    assert pushed == []
    assert emailed == ["a@example.com"]
    assert (summary.push_sent, summary.emails_sent) == (0, 1)


@pytest.mark.asyncio
async def test_dead_push_token_is_removed_and_a_failing_send_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TOKEN_INVALID answer clears the token; a raising provider never raises out."""
    user_id = uuid4()
    removed: list[str] = []

    async def find_setting(*args: object) -> None:
        return None

    async def push_targets(
        db: object, user_ids: Sequence[UUID]
    ) -> list[PushTargetReference]:
        return [
            PushTargetReference(user_id, "dead"),
            PushTargetReference(user_id, "boom"),
            PushTargetReference(user_id, "ok"),
        ]

    async def email_targets(*args: object) -> list[EmailTargetReference]:
        return []

    async def remove_token(db: object, push_token: str) -> None:
        removed.append(push_token)

    class Push:
        async def send_push(
            self, push_token: str, message: notification_providers.PushMessage
        ) -> notification_providers.PushOutcome:
            if push_token == "boom":
                raise RuntimeError("provider down")
            if push_token == "dead":
                return notification_providers.PushOutcome.TOKEN_INVALID
            return notification_providers.PushOutcome.SENT

    monkeypatch.setattr(notification_repository, "find_setting", find_setting)
    monkeypatch.setattr(identity_service, "list_push_targets_by_user_ids", push_targets)
    monkeypatch.setattr(
        identity_service, "list_email_targets_by_user_ids", email_targets
    )
    monkeypatch.setattr(identity_service, "remove_push_token", remove_token)
    monkeypatch.setattr(notification_delivery, "get_push_sender", Push)

    summary = await notification_delivery.deliver_notification(
        fake_db_session(), _context(NotificationType.BATTERY_ALERT), [user_id]
    )

    assert removed == ["dead"]
    assert (summary.push_sent, summary.push_dropped_tokens, summary.failures) == (
        1,
        1,
        1,
    )


@pytest.mark.asyncio
async def test_settings_list_shows_defaults_until_saved_and_save_records_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defaults without a row; the first save passes the typed reason and actor to the history."""
    saved: dict[str, object] = {}

    async def list_settings(
        db: object, organization_id: UUID
    ) -> list[OrganizationNotificationSettingModel]:
        return []

    async def upsert(
        db: object, **kwargs: object
    ) -> OrganizationNotificationSettingModel:
        saved.update(kwargs)
        return OrganizationNotificationSettingModel(
            push_enabled=kwargs["push_enabled"],
            email_enabled=kwargs["email_enabled"],
            updated_at=None,
        )

    monkeypatch.setattr(
        notification_repository, "list_settings_by_organization", list_settings
    )
    monkeypatch.setattr(notification_repository, "upsert_setting", upsert)
    principal = build_principal()

    listing = await settings_service.list_organization_notification_settings(
        fake_db_session(), principal=principal
    )
    sos = next(
        entry
        for entry in listing.settings
        if entry.notification_type is NotificationType.SOS_ALERT
    )
    assert len(listing.settings) == len(NotificationType)
    assert (sos.push_enabled, sos.email_enabled, sos.is_default) == (True, True, True)

    response = await settings_service.update_organization_notification_setting(
        fake_db_session(),
        NotificationType.GEOFENCE_ALERT,
        NotificationSettingUpdateRequest(
            push_enabled=False, email_enabled=False, reason="Too noisy"
        ),
        principal=principal,
    )
    assert response.is_default is False and response.push_enabled is False
    assert saved["change_reason"] == "Too noisy"
    assert saved["changed_by"] == principal.user_id
    assert saved["notification_type"] == "GEOFENCE_ALERT"


class _FakeSavepoint:
    """Minimal session: ``begin_nested`` is an async context manager."""

    @asynccontextmanager
    async def begin_nested(self):  # type: ignore[no-untyped-def]
        yield


@pytest.mark.asyncio
async def test_create_notification_routes_and_survives_a_routing_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_notification stores the alert, adds the routed inboxes, and never raises on routing."""
    from app.domains.notifications.models import NotificationModel

    added: list[tuple[int, list[UUID]]] = []
    delivered: list[list[UUID]] = []
    user_id = uuid4()

    async def insert(db: object, **kwargs: object) -> NotificationModel:
        return NotificationModel(notification_id=5, **kwargs)

    async def resolve(
        db: object, context: NotificationContext, *, extra_user_ids: Sequence[UUID]
    ) -> list[UUID]:
        return [user_id, *extra_user_ids]

    async def insert_recipients(
        db: object, notification_id: int, user_ids: list[UUID]
    ) -> int:
        added.append((notification_id, user_ids))
        return len(user_ids)

    async def deliver(
        db: object, context: NotificationContext, user_ids: Sequence[UUID]
    ) -> None:
        delivered.append(list(user_ids))

    monkeypatch.setattr(notification_repository, "insert", insert)
    monkeypatch.setattr(recipient_service, "resolve_recipient_user_ids", resolve)
    monkeypatch.setattr(notification_repository, "insert_recipients", insert_recipients)
    monkeypatch.setattr(notification_delivery, "deliver_notification", deliver)
    other = uuid4()
    session = cast(AsyncSession, _FakeSavepoint())

    reference = await notifications_service.create_notification(
        session,
        organization_id=ORGANIZATION_ID,
        notification_type=NotificationType.TRIP_ASSIGNED,
        severity=NotificationSeverity.INFO,
        vehicle_id=None,
        title="t",
        body="b",
        payload={},
        recipient_user_ids=[other],
    )

    assert reference.notification_id == 5
    assert added == [(5, [user_id, other])]
    assert delivered == [[user_id, other]]

    async def failing_resolve(*args: object, **kwargs: object) -> list[UUID]:
        raise RuntimeError("identity down")

    monkeypatch.setattr(
        recipient_service, "resolve_recipient_user_ids", failing_resolve
    )
    again = await notifications_service.create_notification(
        session,
        organization_id=ORGANIZATION_ID,
        notification_type=NotificationType.SOS_ALERT,
        severity=NotificationSeverity.CRITICAL,
        vehicle_id=None,
        title="t",
        body="b",
        payload={},
    )
    assert again.notification_id == 5
