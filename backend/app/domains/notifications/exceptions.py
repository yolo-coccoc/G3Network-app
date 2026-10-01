"""Business exceptions for the notifications domain."""

from app.libs.common.errors import NotFoundError


class NotificationNotFoundError(NotFoundError):
    """Raised when a notification with the given ID does not exist."""
