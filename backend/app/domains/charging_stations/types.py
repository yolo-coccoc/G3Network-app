"""Enums and small DTOs of the ``charging_stations`` domain.

No FastAPI, Pydantic or SQLAlchemy here. ``ChargingResourceStatus`` is the
person-set status of a location, charger or EVSE (DM-25);
``ChargingConnectorStatus`` is the status a charger reports over OCPP (F-C2,
both protocols, also used for the whole charger's status); ``ConnectorStandard``
is the plug standard entered at setup (CS-17); ``OcppMessageDirection`` tags rows
of the verbatim frame log; the ``StationCommand*`` and ``ConfigurationCapture*``
enums describe the command channel (CS-20) and the configuration snapshots
(CS-19, CS-21). ``ConfigurationEntry`` carries one ``GetConfiguration`` key into
the OCPP state service; ``NearestChargingStationReference`` is the DTO
``telemetry`` receives from ``find_nearest_operational_station`` (F-A2).
All status-like columns are plain ``varchar`` in the database (the DBML lists
the allowed values); these enums are where the values live in code.
"""

import enum
from dataclasses import dataclass
from uuid import UUID


class ChargingResourceStatus(str, enum.Enum):
    """Status a person sets on a location, charger or EVSE (DM-25).

    Attributes:
        ACTIVE: In service.
        INACTIVE: Out of service; the status reason says why. A row that left
            the system is INACTIVE and soft-deleted.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ConnectorStandard(str, enum.Enum):
    """Plug standard of a gun, with OCPI ``ConnectorType`` names (CS-17).

    Attributes:
        IEC_62196_T2_COMBO: CCS2.
        GBT_DC: GB/T DC.
        CHADEMO: CHAdeMO.
        CHAOJI: ChaoJi.
        IEC_62196_T1_COMBO: CCS1.
    """

    IEC_62196_T2_COMBO = "IEC_62196_T2_COMBO"
    GBT_DC = "GBT_DC"
    CHADEMO = "CHADEMO"
    CHAOJI = "CHAOJI"
    IEC_62196_T1_COMBO = "IEC_62196_T1_COMBO"


class ChargingConnectorStatus(str, enum.Enum):
    """Live status of a connector as reported by OCPP ``StatusNotification`` (F-C2).

    The values are the exact labels the protocols use:
    OCPP 2.0.1's ``ConnectorStatusEnumType`` (``Available``, ``Occupied``,
    ``Reserved``, ``Unavailable``, ``Faulted``) **plus** the five OCPP 1.6J
    statuses that have no 2.0.1 equivalent, so a 1.6J charger's status is kept
    exactly as it reported it (decision D4 of the OCPP 1.6J planner, CS-04).

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


class StationCommandType(str, enum.Enum):
    """What we ask a charger to do, one shape for both OCPP versions (CS-20).

    Attributes:
        REMOTE_START: Start a transaction (1.6J ``RemoteStartTransaction``,
            2.0.1 ``RequestStartTransaction``).
        REMOTE_STOP: Stop a transaction.
        UNLOCK_CONNECTOR: Unlock a gun's cable.
        RESET: Restart the charger.
        CHANGE_AVAILABILITY: Take a gun (or the charger) in or out of service.
        CHANGE_CONFIGURATION: Set one setting.
        GET_CONFIGURATION: Read the charger's settings (a snapshot).
        TRIGGER_MESSAGE: Ask the charger to send a message now.
    """

    REMOTE_START = "REMOTE_START"
    REMOTE_STOP = "REMOTE_STOP"
    UNLOCK_CONNECTOR = "UNLOCK_CONNECTOR"
    RESET = "RESET"
    CHANGE_AVAILABILITY = "CHANGE_AVAILABILITY"
    CHANGE_CONFIGURATION = "CHANGE_CONFIGURATION"
    GET_CONFIGURATION = "GET_CONFIGURATION"
    TRIGGER_MESSAGE = "TRIGGER_MESSAGE"


class StationCommandOutcome(str, enum.Enum):
    """Observed result of a command (CS-20); no reason column, it is observed.

    Attributes:
        PENDING: Created or sent, waiting for the answer. A PENDING row with
            no ``ocpp_message_id`` is still queued for the gateway.
        ACCEPTED: The charger accepted it.
        REJECTED: The charger refused it.
        ERROR: The charger answered with an OCPP error.
        TIMEOUT: No answer within the configured time.
        NOT_SENT: The charger was not connected.
    """

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    NOT_SENT = "NOT_SENT"


class ConfigurationCaptureReason(str, enum.Enum):
    """Why a configuration snapshot was taken (CS-19).

    Attributes:
        BOOT: Automatically after each boot.
        ON_DEMAND: A person asked for it.
        AFTER_CHANGE: Right after a setting was changed, to confirm it.
    """

    BOOT = "BOOT"
    ON_DEMAND = "ON_DEMAND"
    AFTER_CHANGE = "AFTER_CHANGE"


class ConfigurationCaptureOutcome(str, enum.Enum):
    """Observed result of a configuration snapshot (CS-19).

    Attributes:
        PENDING: Asked, not complete yet.
        COMPLETE: Every part received.
        FAILED: No answer in time, an error answer, or parts missing.
    """

    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class ConfigurationMutability(str, enum.Enum):
    """Whether a charger setting can be changed (CS-19).

    Attributes:
        READ_ONLY: A change is refused (1.6J ``readonly=true``).
        READ_WRITE: Can be changed (1.6J ``readonly=false``).
        WRITE_ONLY: 2.0.1 only, e.g. a password: no value is shown.
    """

    READ_ONLY = "READ_ONLY"
    READ_WRITE = "READ_WRITE"
    WRITE_ONLY = "WRITE_ONLY"


@dataclass(frozen=True, slots=True)
class ConfigurationEntry:
    """One configuration key reported by a charger in ``GetConfiguration``.

    Attributes:
        key: The configuration key name (for example ``SupportedFeatureProfiles``).
        value: The key's value as text, or ``None`` if the charger reported the
            key without one.
        is_readonly: Whether the charger refuses to change this key (a design
            constraint of the charger, not an error); stored as ``mutability``.
    """

    key: str
    value: str | None
    is_readonly: bool


@dataclass(frozen=True, slots=True)
class ReportEntry:
    """One setting value of an OCPP 2.0.1 ``NotifyReport`` part (CS-19).

    Attributes:
        variable_name: The variable.
        value: The value as text, ``None`` if none (or write-only).
        mutability: Whether the setting can be changed.
        attribute_type: ``Actual``, ``Target``, ``MinSet`` or ``MaxSet``.
        component_name: The component the variable belongs to.
        component_instance: Instance of the component, if any.
        ocpp_evse_id: EVSE the component sits on, if any.
        ocpp_connector_id: Connector the component sits on, if any.
        variable_instance: Instance of the variable, if any.
    """

    variable_name: str
    value: str | None
    mutability: ConfigurationMutability
    attribute_type: str = "Actual"
    component_name: str | None = None
    component_instance: str | None = None
    ocpp_evse_id: int | None = None
    ocpp_connector_id: int | None = None
    variable_instance: str | None = None


@dataclass(frozen=True)
class NearestChargingStationReference:
    """A station resolved as nearest to a given point (F-A2).

    "Available" means a charger with status ACTIVE at an ACTIVE, public
    location, not soft-deleted, with at least one connector whose last
    reported status is ``Available``. The charger's derived ``is_online`` is
    deliberately not consulted (stale-status handling:
    ``docs/decisions/deferred.md`` item 76).

    Attributes:
        station_id: Internal UUID of the station.
        display_name: Display name of the station's location.
        latitude: GPS latitude in decimal degrees.
        longitude: GPS longitude in decimal degrees.
        distance_km: Great-circle distance from the query point, in km.
    """

    station_id: UUID
    display_name: str
    latitude: float
    longitude: float
    distance_km: float


@dataclass(frozen=True)
class StationCommandReference:
    """A command queued for a charger, as another domain sees it (PR-16).

    Attributes:
        command_id: Internal UUID of the command.
        station_id: The charger it goes to.
        command_type: What was asked.
        outcome: Observed result; ``PENDING`` right after queueing.
    """

    command_id: UUID
    station_id: UUID
    command_type: StationCommandType
    outcome: StationCommandOutcome
