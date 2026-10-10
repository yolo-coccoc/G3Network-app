"""Business exceptions raised by the batteries domain."""

from app.libs.common.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
)


class BatteryError(DomainError):
    """Base exception for battery business-rule failures."""


class BatteryNotFoundError(BatteryError, NotFoundError):
    """Raised when a requested battery does not exist or left the system."""


class BatteryConflictError(BatteryError, ConflictError):
    """Raised when a battery request clashes with the current state.

    A duplicate serial number, a pack already fitted (to this or another
    truck), a truck that already holds a pack, a pack that is not fitted when
    it is to be removed, or an INACTIVE pack that is to be fitted.
    """


class BatteryModelNotFoundError(BatteryError, NotFoundError):
    """Raised when a requested battery model does not exist or was removed."""


class BatteryModelConflictError(BatteryError, ConflictError):
    """Raised when a battery model with the same maker and name exists."""


class BatteryVehicleNotFoundError(BatteryError, NotFoundError):
    """Raised when the truck to fit a battery to does not exist."""


class BatteryDateInvalidError(BatteryError, InvalidInputError):
    """Raised when an installation or transfer date breaks a rule.

    The date is in the future, or not after the date the current owner took
    the battery.
    """
