"""Domain exceptions for Telematic device CRUD."""

from app.libs.common.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    UpstreamUnavailableError,
)


class TelematicError(DomainError):
    """Generic business error for the Telematic domain."""


class TelematicNotFoundError(TelematicError, NotFoundError):
    """Telematic device not found."""


class TelematicVehicleNotFoundError(TelematicError, NotFoundError):
    """The ``vehicle_vin`` sent on a device create/update matches no live
    vehicle (D10 of the happy-path planner, deferred.md item 83)."""


class TelematicConflictError(TelematicError, ConflictError):
    """Device data or vehicle mapping is duplicated."""


class TelematicNotConfigurableError(TelematicError, ConflictError):
    """Device is not mounted on a vehicle or not ACTIVE, so it cannot take a
    config push (F-J2, TX-08)."""


class TelematicCommandPublishError(TelematicError, UpstreamUnavailableError):
    """Publishing a device command over MQTT failed (F-J2)."""
