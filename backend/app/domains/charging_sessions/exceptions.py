"""Business exceptions for the charging_sessions domain."""


class ChargingSessionNotFoundError(Exception):
    """The referenced charging session aggregate was not found."""


class ChargingSessionInputError(Exception):
    """Data normalized from the adapter violates the ingestion contract."""


class ChargingSessionStateError(Exception):
    """An event arrived for a session whose lifecycle state forbids it (F-B2).

    Distinct from ``ChargingSessionInputError``: the input itself is
    well-formed, only the aggregate's current state (e.g. already
    ``COMPLETED``) forbids the transition.
    """
