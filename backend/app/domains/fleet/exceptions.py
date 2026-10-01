"""Business exceptions raised by the fleet domain."""

from app.libs.common.errors import ConflictError, DomainError, NotFoundError


class FleetError(DomainError):
    """Base exception for fleet business-rule failures."""


class FleetNotFoundError(FleetError, NotFoundError):
    """Raised when a requested fleet does not exist."""


class FleetConflictError(FleetError, ConflictError):
    """Raised when unique fleet data conflicts with an existing fleet."""


class FleetVehicleNotFoundError(FleetError, NotFoundError):
    """Raised when a VIN given for a membership doesn't resolve to a vehicle."""


class FleetMembershipConflictError(FleetError, ConflictError):
    """Raised when the target vehicle already has a different active membership."""


class FleetMembershipNotFoundError(FleetError, NotFoundError):
    """Raised when removing a vehicle that has no active membership in the fleet."""
