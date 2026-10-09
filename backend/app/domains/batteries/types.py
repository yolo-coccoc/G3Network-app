"""Shared internal enums of the batteries domain.

The batteries tables store these as plain ``varchar`` columns (the DBML lists
the allowed values in the column note, with no database check); the enums name
the allowed values for code. A column holds the member's value, e.g.
``BatteryStatus.ACTIVE.value``.
"""

import enum


class BatteryChemistry(str, enum.Enum):
    """Cell chemistry of a battery model."""

    LFP = "LFP"
    NMC = "NMC"


class BatteryStatus(str, enum.Enum):
    """Status of a battery, set by a person (DM-25).

    Attributes:
        ACTIVE: Usable.
        INACTIVE: Not usable now (repair, check); a battery that leaves the
            system is ``INACTIVE`` and soft-deleted. Installed or in stock is
            not a status: it is read from ``batteries.vehicle_id``.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
