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


class TelematicConflictError(TelematicError, ConflictError):
    """Device data or vehicle mapping is duplicated."""


class TelematicNotConfigurableError(TelematicError, ConflictError):
    """Device is not in a status that accepts a config push (F-J2)."""


class TelematicCommandPublishError(TelematicError, UpstreamUnavailableError):
    """Publishing a device command over MQTT failed (F-J2)."""
