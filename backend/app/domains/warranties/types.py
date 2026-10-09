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
