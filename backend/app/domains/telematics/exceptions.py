"""Domain exceptions for Telematic device CRUD."""


class TelematicError(Exception):
    """Generic business error for the Telematic domain."""


class TelematicNotFoundError(TelematicError):
    """Telematic device not found."""


class TelematicConflictError(TelematicError):
    """Device data or vehicle mapping is duplicated."""
