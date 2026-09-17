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


class VehicleActivationStatus(str, enum.Enum):
    """Progress of a vehicle through the F-F2 device-provisioning flow.

    Distinct from ``VehicleStatus`` - this tracks whether a vehicle has
    been through end-to-end provisioning at least once, not its current
    operating state.

    Attributes:
        PENDING: No telematic device has been assigned to this vehicle yet.
        DEVICE_ASSIGNED: A telematic device is assigned, but no telemetry
            has been received from it yet.
        ACTIVATED: At least one telemetry message has been received for
            this vehicle - end-to-end data flow confirmed.
    """

    PENDING = "PENDING"
    DEVICE_ASSIGNED = "DEVICE_ASSIGNED"
    ACTIVATED = "ACTIVATED"


@dataclass(frozen=True)
class VehicleReference:
    """Minimal information for other domains to reference a vehicle.

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        vin: VIN (chassis number) used to identify the vehicle in business logic.
        battery_capacity_kwh: Nominal battery pack capacity in kWh, or
            `None` if not recorded - a genuine static vehicle attribute,
            carried here so a single cross-domain lookup can serve both
            "does this vehicle exist" and "what's its pack size" (F-A6/F-C6).
    """

    vehicle_id: UUID
    vin: str
    battery_capacity_kwh: float | None


@dataclass(frozen=True)
class VehicleSummary:
    """A wider, display-oriented DTO for other domains that list vehicles.

    Deliberately a sibling of `VehicleReference`, not a widened version of
    it - coding-conventions section 5.1 lists `Summary` ("summarized
    data") as its own DTO role distinct from `Reference` ("minimal
    reference to an object"). Keeping them separate means a caller that
    only needs `VehicleReference`'s narrow shape (e.g. `telemetry`'s
    F-A6/F-C6 reports) never has to carry fields it doesn't use, and
    `VehicleReference`'s existing consumers/tests are unaffected by this
    addition.

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        vin: VIN (chassis number).
        license_plate: License plate, for display in a caller's own list.
        status: Vehicle lifecycle status (F-E1's "status" column).
    """

    vehicle_id: UUID
    vin: str
    license_plate: str
    status: VehicleStatus
