"""Domain exceptions for Telematic device CRUD."""


class TelematicError(Exception):
    """Generic business error for the Telematic domain."""


class TelematicNotFoundError(TelematicError):
    """Telematic device not found."""


class TelematicConflictError(TelematicError):
    """Device data or vehicle mapping is duplicated."""


class TelematicNotConfigurableError(TelematicError):
    """Device is not in a status that accepts a config push (F-J2)."""


class TelematicCommandPublishError(TelematicError):
    """Publishing a device command over MQTT failed (F-J2)."""
