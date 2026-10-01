"""Shared base classes for domain exceptions, and their HTTP meaning.

Every domain exception inherits from exactly one of these bases. The bases
carry no HTTP knowledge themselves (services stay transport-free);
``app/api/main.py`` registers one exception handler per base, so a router
only needs its own ``try/except`` for an exception that must map to a
different status code than its base implies.
"""


class DomainError(Exception):
    """Root of every business exception raised by a domain service."""


class NotFoundError(DomainError):
    """A referenced object does not exist or was soft-deleted (HTTP 404)."""


class ConflictError(DomainError):
    """The request clashes with current state: a duplicate, or a transition
    the object's lifecycle does not allow (HTTP 409)."""


class InvalidInputError(DomainError):
    """Input that passed schema validation but breaks a business rule, such
    as a time range in the wrong order or longer than allowed (HTTP 400)."""


class UpstreamUnavailableError(DomainError):
    """An external system the operation depends on (MQTT broker, charger)
    did not accept the request (HTTP 502)."""
