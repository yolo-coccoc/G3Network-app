"""Shared types for ``charging_stations`` in the ideal MVP.

Directory/descriptive metadata about a pre-provisioned station (location,
power rating, connector standard, operating hours, maintenance status) is
part of the active contract (F-C1). A connector's live OCPP-reported status
(F-C2) is also part of the active contract now. Still excluded: administrative
status and capability negotiation — that reliability path remains deferred (see
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

    The values are the exact labels the protocols use (stored as values, not
    Python member names — see ``models.py``'s ``enum_values()`` helper):
    OCPP 2.0.1's ``ConnectorStatusEnumType`` (``Available``, ``Occupied``,
    ``Reserved``, ``Unavailable``, ``Faulted``) **plus** the five OCPP 1.6J
    statuses that have no 2.0.1 equivalent, so a 1.6J charger's status is kept
    exactly as it reported it (decision D4 of the OCPP 1.6J planner).

    Busy rule: a connector is free **only** when ``AVAILABLE``. ``OCCUPIED``,
    ``PREPARING``, ``CHARGING``, ``SUSPENDED_EV``, ``SUSPENDED_EVSE`` and
    ``FINISHING`` are busy (``Suspended*`` are normal pauses, not faults);
    ``RESERVED`` and ``UNAVAILABLE`` are not free either; ``FAULTED`` is not
    free and needs attention.

    Attributes:
        AVAILABLE: Ready, no vehicle (both protocols).
        OCCUPIED: 2.0.1's single "in use" status.
        RESERVED: Booked (both protocols).
        UNAVAILABLE: Out of service (both protocols).
        FAULTED: Fault, cannot charge (both protocols).
        PREPARING: 1.6J: plugged in or authorized, not yet delivering.
        CHARGING: 1.6J: delivering power.
        SUSPENDED_EV: 1.6J: connected, the vehicle paused the charge.
        SUSPENDED_EVSE: 1.6J: connected, the charger paused the charge.
        FINISHING: 1.6J: session ended, gun not yet removed.
    """

    AVAILABLE = "Available"
    OCCUPIED = "Occupied"
    RESERVED = "Reserved"
    UNAVAILABLE = "Unavailable"
    FAULTED = "Faulted"
    PREPARING = "Preparing"
    CHARGING = "Charging"
    SUSPENDED_EV = "SuspendedEV"
    SUSPENDED_EVSE = "SuspendedEVSE"
    FINISHING = "Finishing"


class OcppMessageDirection(str, enum.Enum):
    """Direction of a stored OCPP frame relative to the CSMS.

    Attributes:
        CP_TO_CSMS: A frame received from the charge point (inbound).
        CSMS_TO_CP: A frame sent by this backend to the charge point (outbound).
    """

    CP_TO_CSMS = "CP_TO_CSMS"
    CSMS_TO_CP = "CSMS_TO_CP"


@dataclass(frozen=True, slots=True)
class ConfigurationEntry:
    """One configuration key reported by a charger in ``GetConfiguration``.

    Attributes:
        key: The configuration key name (for example ``SupportedFeatureProfiles``).
        value: The key's value as text, or ``None`` if the charger reported the
            key without one.
        is_readonly: Whether the charger refuses to change this key (a design
            constraint of the charger, not an error).
    """

    key: str
    value: str | None
    is_readonly: bool


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
