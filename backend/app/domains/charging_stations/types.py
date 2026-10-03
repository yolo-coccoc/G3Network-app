"""Enums and small DTOs of the ``charging_stations`` domain.

No FastAPI, Pydantic or SQLAlchemy here. ``ChargingStationMaintenanceStatus``
is the admin-set directory state (F-C1); ``ChargingConnectorStatus`` is the
status a charger reports over OCPP (F-C2, both protocols, also used for the
whole charger's connector ``0``); ``OcppMessageDirection`` tags rows of the
verbatim frame log; ``ConfigurationEntry`` carries one ``GetConfiguration``
key into the OCPP state service; ``NearestChargingStationReference`` is the
DTO ``telemetry`` receives from ``find_nearest_operational_station`` (F-A2,
now occupancy-aware: at least one ``Available`` connector is required).
Administrative/technical status history and capability negotiation remain
deferred (``docs/decisions/deferred.md`` items 27 and 28).
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
    Python member names — see ``app.libs.db.enums.enum_values``):
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
class NearestChargingStationReference:
    """A station resolved as nearest to a given point (F-A2).

    "Available" (decision D3 of the happy-path completion planner) means not
    soft-deleted, ``maintenance_status == OPERATIONAL`` and at least one
    connector whose last reported status is ``Available``. The charger's
    derived ``is_online`` is deliberately not consulted, so a charger that
    went offline with a gun last reported ``Available`` still qualifies
    (stale-status handling: ``docs/decisions/deferred.md`` item 76).

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
