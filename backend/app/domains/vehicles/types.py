"""Shared internal data types and DTOs used within the vehicles domain."""

import enum
from dataclasses import dataclass
from uuid import UUID


class VehicleStatus(str, enum.Enum):
    """Supported lifecycle statuses of a vehicle."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"
    DECOMMISSIONED = "DECOMMISSIONED"


@dataclass(frozen=True)
class VehicleReference:
    """Minimal information for other domains to reference a vehicle.

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        vin: VIN (chassis number) used to identify the vehicle in business logic.
    """

    vehicle_id: UUID
    vin: str
