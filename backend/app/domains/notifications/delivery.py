"""Push and e-mail delivery of a new alert (NTF-02, NTF-04, NT-11, NT-12).

The in-app and portal inbox needs no delivery: the recipient rows are the
inbox. This module sends the two extra channels, when the alert's organization
has them on (``settings_service``): a push to every device the recipients are
logged in on (the push token of each unexpired login session, ACC-16) and an
e-mail to each recipient with an address on file. The providers are behind
interfaces with logging fakes (``providers.py``).

Delivery runs inside the producer's transaction, because no queue or worker
exists to hand it to (the fake providers only log, so nothing is lost if the
transaction rolls back; a real provider will need an after-commit step, see the
decision log, NT-15). A failed send is logged and skipped: it never fails the
producer, and there is no delivery-status table (NTF-07 is P1.1, NT-12).
SMS is deferred (NT-11).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.service as identity_service
import app.domains.notifications.settings_service as settings_service
from app.domains.notifications.providers import (
    PushMessage,
    PushOutcome,
    get_email_sender,
    get_push_sender,
)
from app.domains.notifications.types import NotificationContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliverySummary:
    """What one delivery attempt did, for the log.

    Attributes:
        push_sent: Pushes the provider accepted.
        push_dropped_tokens: Dead tokens removed from their login session.
        emails_sent: E-mails handed to the provider.
        failures: Sends that raised and were skipped.
    """

    push_sent: int = 0
    push_dropped_tokens: int = 0
    emails_sent: int = 0
    failures: int = 0


def build_push_message(context: NotificationContext) -> PushMessage:
    """Build the push message of an alert.

    Args:
        context: The alert.

    Returns:
        The title, the text and the data the app needs to open the alert's
        screen (NTF-02): the alert, its kind, its organization, and the
        subject and truck when it has them.
    """
    data = {
        "notification_id": str(context.notification_id),
        "notification_type": context.notification_type.value,
        "organization_id": str(context.organization_id),
    }
    if context.subject_type is not None and context.subject_id is not None:
        data["subject_type"] = context.subject_type
        data["subject_id"] = str(context.subject_id)
    if context.vehicle_id is not None:
        data["vehicle_id"] = str(context.vehicle_id)
    return PushMessage(title=context.title, body=context.body, data=data)


async def deliver_notification(
    db_session: AsyncSession,
    context: NotificationContext,
    recipient_user_ids: Sequence[UUID],
) -> DeliverySummary:
    """Send an alert by push and e-mail as the organization's switches allow.

    Args:
        db_session: Session owned by the producer's entry boundary.
        context: The alert.
        recipient_user_ids: The people who received it in their inbox.

    Returns:
        What was sent.

    Side Effects:
        Reads the organization's switches, the recipients' push tokens and
        addresses, calls the providers, and clears a token the push service
        reports dead; does not commit.
    """
    if not recipient_user_ids:
        return DeliverySummary()
    switches = await settings_service.resolve_channel_switches(
        db_session, context.organization_id, context.notification_type
    )
    push_sent = push_dropped = emails_sent = failures = 0

    if switches.push_enabled:
        push_sender = get_push_sender()
        message = build_push_message(context)
        for push_target in await identity_service.list_push_targets_by_user_ids(
            db_session, recipient_user_ids
        ):
            try:
                outcome = await push_sender.send_push(push_target.push_token, message)
            # A provider call is an external boundary: one bad device must not
            # stop the others or the producer.
            except Exception:
                failures += 1
                logger.exception(
                    "Push delivery failed",
                    extra={"notification_id": context.notification_id},
                )
                continue
            if outcome is PushOutcome.TOKEN_INVALID:
                await identity_service.remove_push_token(
                    db_session, push_target.push_token
                )
                push_dropped += 1
            else:
                push_sent += 1

    if switches.email_enabled:
        email_sender = get_email_sender()
        for email_target in await identity_service.list_email_targets_by_user_ids(
            db_session, recipient_user_ids
        ):
            try:
                await email_sender.send_email(
                    email_target.email, context.title, context.body
                )
            # Same boundary as the push call above.
            except Exception:
                failures += 1
                logger.exception(
                    "E-mail delivery failed",
                    extra={"notification_id": context.notification_id},
                )
                continue
            emails_sent += 1

    summary = DeliverySummary(
        push_sent=push_sent,
        push_dropped_tokens=push_dropped,
        emails_sent=emails_sent,
        failures=failures,
    )
    logger.info(
        "notification delivered",
        extra={
            "notification_id": context.notification_id,
            "notification_type": context.notification_type.value,
            "recipients": len(recipient_user_ids),
            "push_sent": summary.push_sent,
            "emails_sent": summary.emails_sent,
            "failures": summary.failures,
        },
    )
    return summary
