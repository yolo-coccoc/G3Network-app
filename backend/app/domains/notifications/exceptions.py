"""Business exceptions for the notifications domain."""

from app.libs.common.errors import InvalidInputError, NotFoundError


class NotificationNotFoundError(NotFoundError):
    """Raised when a notification with the given ID does not exist."""


class NotificationRecipientNotFoundError(NotFoundError):
    """Raised when an alert never reached the person asking about it."""


class NotificationFilterError(InvalidInputError):
    """Raised when list filters contradict each other (unread without a person)."""


class NotificationSettingOrganizationNotFoundError(NotFoundError):
    """Raised when the organization whose channel settings are asked for is
    unknown or out of the caller's reach."""
