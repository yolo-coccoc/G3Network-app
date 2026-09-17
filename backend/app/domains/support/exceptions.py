"""Business exceptions raised by the support domain."""


class SupportError(Exception):
    """Base exception for support-case business-rule failures."""


class SupportCaseNotFoundError(SupportError):
    """Raised when a requested support case does not exist."""


class SupportVehicleNotFoundError(SupportError):
    """Raised when a VIN given for a case doesn't resolve to a vehicle."""


class SupportDriverNotFoundError(SupportError):
    """Raised when a driver ID given for a case doesn't resolve to a driver."""


class SupportCaseStateError(SupportError):
    """Raised when a status change is attempted on a terminal support case."""
