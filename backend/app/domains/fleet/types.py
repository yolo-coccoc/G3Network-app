"""Shared internal data types used within the fleet domain."""

import enum


class FleetStatus(str, enum.Enum):
    """Supported lifecycle statuses of a fleet."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
