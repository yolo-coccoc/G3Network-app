"""Outbound push and e-mail providers of the notifications domain (NTF-02, NTF-04).

The real services (Firebase push, an e-mail gateway) are external (PR-15), so
the domain talks to the ``PushSender`` / ``EmailSender`` interfaces. The only
implementations today are fakes that write the message to the log; the
settings ``NOTIFICATIONS_PUSH_PROVIDER`` / ``NOTIFICATIONS_EMAIL_PROVIDER``
select them, and a real service becomes a new setting value and a new class
here.

Never log a frame or a message body that carries more than the alert already
shows the recipient; the fakes log the title, the text and the address so a
developer can see what would have been sent.
"""

import enum
import logging
from dataclasses import dataclass
from typing import Protocol

from app.libs.common.config import settings

logger = logging.getLogger(__name__)


class PushOutcome(str, enum.Enum):
    """What the push service said about one token.

    Attributes:
        SENT: Accepted for delivery.
        TOKEN_INVALID: The token is dead (the app was removed); the caller
            forgets it (NT-12).
    """

    SENT = "SENT"
    TOKEN_INVALID = "TOKEN_INVALID"


@dataclass(frozen=True)
class PushMessage:
    """One push message.

    Attributes:
        title: Headline shown by the phone.
        body: Text shown under it.
        data: Key-value data the app reads when the person taps it (which
            alert, which screen to open, NTF-02).
    """

    title: str
    body: str
    data: dict[str, str]


class PushSender(Protocol):
    """Sends a push message to one device."""

    async def send_push(self, push_token: str, message: PushMessage) -> PushOutcome:
        """Deliver one push.

        Args:
            push_token: The device's token.
            message: What to show.

        Returns:
            What the push service answered for the token.
        """
        ...


class EmailSender(Protocol):
    """Sends an e-mail."""

    async def send_email(self, email: str, subject: str, body: str) -> None:
        """Deliver one e-mail.

        Args:
            email: Destination address.
            subject: Subject line.
            body: Plain-text body.
        """
        ...


class LoggingPushSender:
    """Fake push service: writes the message to the application log."""

    async def send_push(self, push_token: str, message: PushMessage) -> PushOutcome:
        """Log the push instead of sending it.

        Args:
            push_token: The device's token.
            message: What would be shown.

        Returns:
            Always ``SENT``.
        """
        logger.info(
            "fake_push_sent",
            extra={
                "push_token": push_token,
                "title": message.title,
                "text": message.body,
                "data": message.data,
            },
        )
        return PushOutcome.SENT


class LoggingEmailSender:
    """Fake e-mail service: writes the message to the application log."""

    async def send_email(self, email: str, subject: str, body: str) -> None:
        """Log the e-mail instead of sending it.

        Args:
            email: Destination address.
            subject: Subject line.
            body: Plain-text body.
        """
        logger.info(
            "fake_notification_email_sent",
            extra={"email": email, "subject": subject, "text": body},
        )


def get_push_sender() -> PushSender:
    """Return the push provider chosen by ``NOTIFICATIONS_PUSH_PROVIDER``.

    Returns:
        The configured sender (the logging fake is the only one).

    Raises:
        ValueError: If the setting names an unknown provider.
    """
    if settings.NOTIFICATIONS_PUSH_PROVIDER == "log":
        return LoggingPushSender()
    raise ValueError(f"unknown push provider {settings.NOTIFICATIONS_PUSH_PROVIDER!r}")


def get_email_sender() -> EmailSender:
    """Return the e-mail provider chosen by ``NOTIFICATIONS_EMAIL_PROVIDER``.

    Returns:
        The configured sender (the logging fake is the only one).

    Raises:
        ValueError: If the setting names an unknown provider.
    """
    if settings.NOTIFICATIONS_EMAIL_PROVIDER == "log":
        return LoggingEmailSender()
    raise ValueError(
        f"unknown e-mail provider {settings.NOTIFICATIONS_EMAIL_PROVIDER!r}"
    )
