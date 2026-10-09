"""Shared internal data types and DTOs used within the drivers domain.

The DBML types most closed lists here as plain ``varchar`` (the allowed
values are the enums below, not database types); only ``DriverStatus`` is a
PostgreSQL enum, kept from the first design.
"""

import enum
from dataclasses import dataclass
from uuid import UUID


class DriverStatus(str, enum.Enum):
    """Supported lifecycle statuses of a driver profile (decided by the organization)."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class LicenseClass(str, enum.Enum):
    """Licence class under Law 36/2024/QH15 (DR-09)."""

    B = "B"
    C1 = "C1"
    C = "C"
    D1 = "D1"
    D2 = "D2"
    D = "D"
    BE = "BE"
    C1E = "C1E"
    CE = "CE"
    D1E = "D1E"
    D2E = "D2E"
    DE = "DE"


class CheckInMethod(str, enum.Enum):
    """How a driver checked in to a truck (DR-07)."""

    QR = "QR"
    APP = "APP"
    PORTAL = "PORTAL"


class DrivingSessionEndCause(str, enum.Enum):
    """Why a driving session ended (DR-07)."""

    CHECKED_OUT = "CHECKED_OUT"
    TAKEN_OVER = "TAKEN_OVER"
    OTHER_TRUCK = "OTHER_TRUCK"
    AUTO_ENDED = "AUTO_ENDED"
    DRIVER_REMOVED = "DRIVER_REMOVED"
    OWNER_CHANGED = "OWNER_CHANGED"


class TripStatus(str, enum.Enum):
    """Lifecycle of a trip (DR-12)."""

    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class DeclaredLoadStatus(str, enum.Enum):
    """Load a driver declared when starting a trip (MON-13)."""

    LOADED = "LOADED"
    EMPTY = "EMPTY"


@dataclass(frozen=True)
class DriverReference:
    """Minimal information for other domains to reference a driver.

    Returned by `resolve_driver_reference_by_id`; the `support` domain
    uses it to validate a case's driver and to fill `driver_name` in its
    case responses.

    Attributes:
        driver_id: Internal ID of the driver profile.
        full_name: The person's full name (read from the user through the
            profile's membership), for display in a caller's own response
            without a second lookup.
    """

    driver_id: UUID
    full_name: str
