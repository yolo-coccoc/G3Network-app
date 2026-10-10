"""Internal data types and DTOs in the telematics domain."""

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class TelematicStatus(str, enum.Enum):
    """Status of a Telematic device, set by a person (DM-25).

    ``INACTIVE`` covers a device not usable now (the reason says why) and a
    device that left the system (also unmounted and soft-deleted).
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True)
class TelematicVehicleMapping:
    """Mapping of an active telematic device to a vehicle.

    Attributes:
        telematic_id: Internal ID of the device.
        vehicle_id: Internal ID of the assigned vehicle.
        organization_id: The organization that owns that vehicle now; the
            sample written at this moment copies it (DM-24 case C).
    """

    telematic_id: UUID
    vehicle_id: UUID
    organization_id: UUID


class TelematicConfigPushOutcome(str, enum.Enum):
    """Outcome for one fleet vehicle in a fleet-wide config push (F-J2, D9).

    ``PUBLISHED``: the broker accepted the command. ``SKIPPED``: nothing was attempted
    (soft-deleted vehicle, no live device, or a device that is not
    ``ACTIVE``). ``FAILED``: the publish was attempted and raised.
    """

    PUBLISHED = "published"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class TelematicDeviceHealth:
    """Read-time health of a mounted device, from its vehicle's telemetry (F-J1).

    Computed per request and never stored, like every derived state here.

    Attributes:
        last_seen_at: Newest telemetry ``received_at`` of the mounted
            vehicle, or ``None`` if it never reported.
        is_online: The newest telemetry arrived within
            ``TELEMETRY_ONLINE_THRESHOLD_SECONDS`` (planner D2).
        is_silent: The device-health monitor's silence rule holds (see
            ``monitoring/silence_rule.py``).
        last_signal_strength_dbm: Signal strength of the vehicle's newest
            reading in dBm, or ``None`` if it was not reported.
    """

    last_seen_at: datetime | None
    is_online: bool
    is_silent: bool
    last_signal_strength_dbm: int | None


class TelematicHealthState(str, enum.Enum):
    """One-word health of a device for the dashboard (DEV-04), computed at read time.

    The first rule that holds wins, in this order.

    Attributes:
        INACTIVE: Taken out of service by a person; its quiet is expected.
        NOT_MOUNTED: In stock or removed, or mounted on a deleted vehicle.
        NO_DATA: Mounted and in service but the truck never reported (a
            provisioning gap, as in ``silence_rule``).
        SILENT: No telemetry for ``TELEMATICS_SILENT_THRESHOLD_MINUTES``.
        ATTENTION: Reporting, but the newest status report names a problem
            (mobile data not ``ACTIVE``, or a GNSS antenna fault).
        HEALTHY: Reporting and nothing in the newest status report is wrong.
    """

    INACTIVE = "INACTIVE"
    NOT_MOUNTED = "NOT_MOUNTED"
    NO_DATA = "NO_DATA"
    SILENT = "SILENT"
    ATTENTION = "ATTENTION"
    HEALTHY = "HEALTHY"


@dataclass(frozen=True)
class TelematicMountedDevice:
    """The live device fitted on a truck, for another domain (VEH-05 activation).

    Attributes:
        telematic_id: Internal ID of the device.
        telematic_serial: Serial printed on the device.
        status: Status set by a person.
        mounted_at: When it was mounted on the truck (``installed_at``; the
            ``acquired_at`` of the device for a row mounted before the column
            was filled).
    """

    telematic_id: UUID
    telematic_serial: str
    status: TelematicStatus
    mounted_at: datetime
