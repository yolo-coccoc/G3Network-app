"""Shared internal enums of the warranties domain.

The ``warranties`` table stores these as plain ``varchar`` columns (the DBML
lists the allowed values in the column note, with no database check); the
enums name the allowed values for code.
"""

import enum


class WarrantyType(str, enum.Enum):
    """How a warranty came to be.

    Attributes:
        STANDARD: Delivered with the object.
        EXTENDED: Bought later.
    """

    STANDARD = "STANDARD"
    EXTENDED = "EXTENDED"


class WarrantyStatus(str, enum.Enum):
    """Status of a warranty (DM-25).

    Attributes:
        ACTIVE: In force unless expired; expiry is computed, never stored.
        VOIDED: Revoked by our warranty team, with a reason. A warranty
            entered by mistake is ``VOIDED`` and soft-deleted.
    """

    ACTIVE = "ACTIVE"
    VOIDED = "VOIDED"


class WarrantyObjectKind(str, enum.Enum):
    """What a warranty covers: the one link of the row that is set (VH-18, VH-19).

    Attributes:
        VEHICLE: A truck.
        BATTERY: A battery; the warranty follows it from truck to truck.
        TELEMATIC: A T-Box.
        STATION: A charger.
    """

    VEHICLE = "VEHICLE"
    BATTERY = "BATTERY"
    TELEMATIC = "TELEMATIC"
    STATION = "STATION"


# The ``limits`` keys each kind of object allows (DBML note of
# ``warranties.limits``): each is the counter reading at which coverage ends.
WARRANTY_LIMIT_KEYS: dict[WarrantyObjectKind, frozenset[str]] = {
    WarrantyObjectKind.VEHICLE: frozenset({"distance_km"}),
    WarrantyObjectKind.BATTERY: frozenset({"energy_throughput_kwh", "charge_cycles"}),
    WarrantyObjectKind.TELEMATIC: frozenset({"operating_hours", "message_count"}),
    WarrantyObjectKind.STATION: frozenset({"energy_delivered_kwh", "session_count"}),
}
