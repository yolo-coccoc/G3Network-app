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
    """Raised when closing a membership that is not open in the fleet.

    By VIN: the vehicle has no open membership in that fleet. By membership
    ID: the membership is unknown, belongs to another fleet, or is already
    closed.
    """


class GeofenceNotFoundError(FleetError, NotFoundError):
    """Raised when a geofence does not exist, was soft-deleted, or belongs to
    another fleet."""
