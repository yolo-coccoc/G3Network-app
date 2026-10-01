"""Shared internal data types and DTOs used within the fleet domain."""

import enum
from dataclasses import dataclass
from uuid import UUID


class FleetStatus(str, enum.Enum):
    """Supported lifecycle statuses of a fleet."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True)
class GeofenceReference:
    """Minimal reference to a live geofence, for other domains (F-A5).

    Returned by ``list_geofences_containing``; the ``telemetry`` domain uses
    it to detect a member vehicle entering or leaving a fleet's area and to
    name the area in the alert.

    Attributes:
        geofence_id: Internal ID of the geofence.
        name: Display name of the geofence, shown in alerts.
    """

    geofence_id: UUID
    name: str
