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


class ChargingStationMaintenanceStatus(str, enum.Enum):
    """Admin-set maintenance state of a pre-provisioned charging station.

    This is a manually maintained directory field, distinct from any future
    live/OCPP-derived technical or connection status.
    """

    OPERATIONAL = "OPERATIONAL"
    UNDER_MAINTENANCE = "UNDER_MAINTENANCE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
