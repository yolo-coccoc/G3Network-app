"""Business exceptions raised by the warranties domain."""

from app.libs.common.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
)


class WarrantyError(DomainError):
    """Base exception for warranty business-rule failures."""


class WarrantyNotFoundError(WarrantyError, NotFoundError):
    """Raised when a requested warranty does not exist or was entered by mistake."""


class WarrantyObjectNotFoundError(WarrantyError, NotFoundError):
    """Raised when the truck, battery, T-Box or charger to cover does not exist."""


class WarrantyConflictError(WarrantyError, ConflictError):
    """Raised when a request clashes with the warranty's state.

    The warranty is already VOIDED (it can no longer be edited or voided
    again), or another live warranty of the same type already covers the same
    object in an overlapping period.
    """


class WarrantyPeriodInvalidError(WarrantyError, InvalidInputError):
    """Raised when the coverage ends before it starts."""


class WarrantyLimitsInvalidError(WarrantyError, InvalidInputError):
    """Raised when ``limits`` holds a key the covered object does not allow."""
