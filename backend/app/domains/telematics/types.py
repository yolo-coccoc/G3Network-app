"""Internal data types and DTOs in the telematics domain."""

import enum
from dataclasses import dataclass
from uuid import UUID


class TelematicStatus(str, enum.Enum):
    """Operating status of a Telematic device."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"


@dataclass(frozen=True)
class TelematicVehicleMapping:
    """Mapping of an active telematic device to a vehicle.

    Attributes:
        telematic_id: Internal ID of the device.
        vehicle_id: Internal ID of the assigned vehicle.
    """

    telematic_id: UUID
    vehicle_id: UUID
