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

    Returned by `resolve_driver_reference_by_id`; the `support` domain
    uses it to validate a case's driver and to fill `driver_name` in its
    case responses.

    Attributes:
        driver_id: Internal ID of the driver.
        full_name: Driver's full name, for display in a caller's own
            response without a second lookup.
    """

    driver_id: UUID
    full_name: str
