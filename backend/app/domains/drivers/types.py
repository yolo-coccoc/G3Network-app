"""Shared internal data types and DTOs used within the drivers domain."""

import enum
from dataclasses import dataclass
from uuid import UUID


class DriverStatus(str, enum.Enum):
    """Supported lifecycle statuses of a driver."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True)
class DriverReference:
    """Minimal information for other domains to reference a driver.

    Not consumed by any caller yet - established for consistency with
    every other CRUD domain in this backend, each of which exposes a
    `resolve_*` DTO for cross-domain lookups (see `VehicleReference`,
    `TelematicVehicleMapping`).

    Attributes:
        driver_id: Internal ID of the driver.
        full_name: Driver's full name, for display in a caller's own
            response without a second lookup.
    """

    driver_id: UUID
    full_name: str
