"""Shared types for ``charging_stations`` in the ideal MVP.

Directory/descriptive metadata about a pre-provisioned station (location,
power rating, connector standard, operating hours, maintenance status) is
part of the active contract (F-C1). Live, OCPP-derived technical/connection
status — heartbeat-based online/offline state, administrative status,
capability negotiation — is still excluded: devices are assumed to always be
online and active in the local happy path, and that reliability path remains
deferred (see ``docs/01-requirements/future.md`` items 27 and 28).
"""

import enum
from dataclasses import dataclass
from uuid import UUID


class ChargingStationMaintenanceStatus(str, enum.Enum):
    """Admin-set maintenance state of a pre-provisioned charging station.

    This is a manually maintained directory field, distinct from any future
    live/OCPP-derived technical or connection status.
    """

    OPERATIONAL = "OPERATIONAL"
    UNDER_MAINTENANCE = "UNDER_MAINTENANCE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


@dataclass(frozen=True)
class NearestChargingStation:
    """A station resolved as nearest to a given point (F-A2).

    "Available" is approximated as "not soft-deleted and not under
    maintenance/out of service" - this codebase has no live occupancy or
    online/offline signal (see the module docstring and
    ``docs/01-requirements/future.md``).

    Attributes:
        station_id: Internal UUID of the station.
        display_name: Display name.
        latitude: GPS latitude in decimal degrees.
        longitude: GPS longitude in decimal degrees.
        distance_km: Great-circle distance from the query point, in km.
    """

    station_id: UUID
    display_name: str
    latitude: float
    longitude: float
    distance_km: float
