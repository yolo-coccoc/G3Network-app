"""Domain types shared across vehicle layers."""

import enum


class VehicleStatus(str, enum.Enum):
    """Lifecycle statuses supported by a vehicle."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"
    DECOMMISSIONED = "DECOMMISSIONED"
