"""Business exceptions for the charging_sessions domain."""

from app.libs.common.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)


class ChargingSessionNotFoundError(NotFoundError):
    """The referenced charging session aggregate was not found."""


class ChargingSessionInputError(InvalidInputError):
    """Data normalized from the adapter violates the ingestion contract."""


class ChargingSessionStateError(ConflictError):
    """An event arrived for a session whose lifecycle state forbids it (F-B2).

    Distinct from ``ChargingSessionInputError``: the input itself is
    well-formed, only the aggregate's current state (e.g. already
    ``COMPLETED``) forbids the transition.
    """


class ChargingSessionTokenError(InvalidInputError):
    """A charger's start message carried a token no PENDING session issued (CE-11).

    The gateway answers the charger ``Invalid``; no session row is created and
    the frame stays in the raw OCPP log.
    """


class ChargingConnectorBusyError(ConflictError):
    """The gun (or every gun of the charger) already has a running charge (CHG-01)."""


class ChargingSessionAlreadyOpenError(ConflictError):
    """The person already has a charge open, so a second scan is refused (CHG-01)."""


class ChargingSessionStopDeniedError(PermissionDeniedError):
    """Only the person who started a charge, or our staff, may stop it (CHG-01)."""
