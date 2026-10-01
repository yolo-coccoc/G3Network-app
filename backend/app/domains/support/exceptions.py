"""Business exceptions raised by the support domain."""

from app.libs.common.errors import ConflictError, DomainError, NotFoundError


class SupportError(DomainError):
    """Base exception for support-case business-rule failures."""


class SupportCaseNotFoundError(SupportError, NotFoundError):
    """Raised when a requested support case does not exist."""


class SupportVehicleNotFoundError(SupportError, NotFoundError):
    """Raised when a VIN given for a case doesn't resolve to a vehicle."""


class SupportDriverNotFoundError(SupportError, NotFoundError):
    """Raised when a driver ID given for a case doesn't resolve to a driver."""


class SupportCaseStateError(SupportError, ConflictError):
    """Raised when a status change is attempted on a terminal support case."""
