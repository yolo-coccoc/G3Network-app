"""Shared types for ``charging_stations`` in the ideal MVP.

Directory/descriptive metadata about a pre-provisioned station (location,
power rating, connector standard, operating hours, maintenance status) is
part of the active contract (F-C1). A connector's live OCPP-reported status
(F-C2) is also part of the active contract now. Still excluded: heartbeat-based
online/offline connection state, administrative status, and capability
negotiation — that reliability path remains deferred (see
``docs/01-requirements/future.md`` items 27 and 28). The verbatim OCPP
message log is the one exception to "no technical history": it stores raw
frames, not derived status (see ``OcppMessageDirection``).
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


class ChargingConnectorStatus(str, enum.Enum):
    """Live status of a connector as reported by OCPP ``StatusNotification`` (F-C2).

    These are OCPP 2.0.1's exact ``ConnectorStatusEnumType`` values (used as
    the enum's values, not just its Python member names — see
    ``models.py``'s ``enum_values()`` helper). OCPP 2.0.1 has no distinct
    "Charging" status: ``Preparing``/``Charging``/``SuspendedEV``/
    ``Finishing`` from OCPP 1.6J are all folded into ``OCCUPIED``.
    """

    AVAILABLE = "Available"
    OCCUPIED = "Occupied"
    RESERVED = "Reserved"
    UNAVAILABLE = "Unavailable"
    FAULTED = "Faulted"


class OcppMessageDirection(str, enum.Enum):
    """Direction of a stored OCPP frame relative to the CSMS.

    Attributes:
        CP_TO_CSMS: A frame received from the charge point (inbound).
        CSMS_TO_CP: A frame sent by this backend to the charge point (outbound).
    """

    CP_TO_CSMS = "CP_TO_CSMS"
    CSMS_TO_CP = "CSMS_TO_CP"


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
