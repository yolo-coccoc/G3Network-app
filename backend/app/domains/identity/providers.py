"""Outbound message providers of the identity domain (SMS and e-mail).

The real gateways are external services (PR-15), so the domain talks to the
`SmsSender` / `EmailSender` interfaces. The only implementation today is a fake
that writes the message to the log; the setting `IDENTITY_SMS_PROVIDER` /
`IDENTITY_EMAIL_PROVIDER` selects it, and a real gateway becomes a new value
and class here.

Security note: the fake logs the whole message, one-time codes included. That
is its purpose in development; it must never be the provider in production.
"""

import logging
from typing import Protocol

from app.libs.common.config import settings

logger = logging.getLogger(__name__)


class SmsSender(Protocol):
    """Sends a text message to a phone number."""

    async def send_sms(self, phone_number: str, message: str) -> None:
        """Deliver one SMS.

        Args:
            phone_number: Destination in E.164 form.
            message: The message text.
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


class LoggingSmsSender:
    """Fake SMS gateway: writes the message to the application log."""

    async def send_sms(self, phone_number: str, message: str) -> None:
        """Log the message instead of sending it.

        Args:
            phone_number: Destination in E.164 form.
            message: The message text.
        """
        logger.info(
            "fake_sms_sent", extra={"phone_number": phone_number, "message": message}
        )


class LoggingEmailSender:
    """Fake e-mail gateway: writes the message to the application log."""

    async def send_email(self, email: str, subject: str, body: str) -> None:
        """Log the e-mail instead of sending it.

        Args:
            email: Destination address.
            subject: Subject line.
            body: Plain-text body.
        """
        logger.info(
            "fake_email_sent",
            extra={"email": email, "subject": subject, "body": body},
        )


def get_sms_sender() -> SmsSender:
    """Return the SMS provider chosen by `IDENTITY_SMS_PROVIDER`.

    Returns:
        The configured sender (the logging fake is the only one).
    """
    if settings.IDENTITY_SMS_PROVIDER == "log":
        return LoggingSmsSender()
    raise ValueError(f"unknown SMS provider {settings.IDENTITY_SMS_PROVIDER!r}")


def get_email_sender() -> EmailSender:
    """Return the e-mail provider chosen by `IDENTITY_EMAIL_PROVIDER`.

    Returns:
        The configured sender (the logging fake is the only one).
    """
    if settings.IDENTITY_EMAIL_PROVIDER == "log":
        return LoggingEmailSender()
    raise ValueError(f"unknown e-mail provider {settings.IDENTITY_EMAIL_PROVIDER!r}")
