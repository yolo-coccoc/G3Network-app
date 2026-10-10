"""Business exceptions raised by the fleet domain."""

from app.libs.common.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
)


class FleetError(DomainError):
    """Base exception for fleet business-rule failures."""


class FleetNotFoundError(FleetError, NotFoundError):
    """Raised when a requested fleet does not exist."""


class FleetConflictError(FleetError, ConflictError):
    """Raised when unique fleet data conflicts with an existing fleet."""


class FleetParentNotFoundError(FleetError, NotFoundError):
    """Raised when the parent fleet given for a fleet does not exist or was
    soft-deleted (FL-02)."""


class FleetParentOrganizationMismatchError(FleetError, InvalidInputError):
    """Raised when a fleet is placed under a parent that belongs to another
    organization (FL-08)."""


class FleetVehicleOrganizationMismatchError(FleetError, InvalidInputError):
    """Raised when a vehicle owned by another organization is added to a fleet
    (FL-09): a fleet holds only its own organization's vehicles."""


class FleetHierarchyLoopError(FleetError, InvalidInputError):
    """Raised when moving a fleet under itself or under one of its own
    sub-fleets, which would make the fleet tree a loop (FL-02)."""


class FleetHasSubFleetsError(FleetError, ConflictError):
    """Raised when deleting a fleet that still has live sub-fleets (FL-08)."""


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
