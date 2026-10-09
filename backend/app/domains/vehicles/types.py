"""Shared internal data types and DTOs used within the vehicles domain."""

import enum
from dataclasses import dataclass
from uuid import UUID


class VehicleStatus(str, enum.Enum):
    """Service status of a vehicle, set by a person or a business rule (DM-25).

    Attributes:
        ACTIVE: In service.
        INACTIVE: Not in service (e.g. in the workshop); ``status_reason`` says
            why. A vehicle that leaves the system is ``INACTIVE`` and
            soft-deleted. Whether it is moving or reporting is computed from
            telemetry, never stored.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True)
class VehicleReference:
    """Minimal information for other domains to reference a vehicle.

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        vin: VIN (chassis number) used to identify the vehicle in business logic.
        organization_id: The organization that owns the vehicle now; a record
            written at this moment (a telemetry sample) copies it (DM-24 C).
        battery_capacity_kwh: Nominal battery pack capacity in kWh from the
            vehicle's model (``vehicle_models.nominal_battery_capacity_kwh``),
            or `None` if the model has none recorded - carried here so a
            single cross-domain lookup can serve both "does this vehicle
            exist" and "what's its pack size" (F-A6/F-C6, VH-16).
    """

    vehicle_id: UUID
    vin: str
    organization_id: UUID
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
