"""Business exceptions for the notifications domain."""


class NotificationNotFoundError(Exception):
    """Raised when a notification with the given ID does not exist."""
