"""Shared internal enums of the billing domain.

The billing tables store these as plain ``varchar`` columns (the DBML lists the
allowed values in each column note, with no database value check); the enums
name the allowed values for code. Services, repositories and endpoints come
with the billing work package (WP9).
"""

import enum


class TariffStatus(str, enum.Enum):
    """Status of a tariff, decided by its owner (DM-19).

    Attributes:
        ACTIVE: In use.
        INACTIVE: Retired.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ChargingSessionBillStatus(str, enum.Enum):
    """Status of the bill of one charging session (BL-10).

    Attributes:
        QUOTED: The price was frozen at the scan, the session is not finished.
        BILLED: The amount is computed and fixed.
        ON_HOLD: Not billed automatically, waiting for review (for example the
            stop reading and the last measurement disagree, CE-12).
        VOID: No charge (the session was ``ABANDONED``).
    """

    QUOTED = "QUOTED"
    BILLED = "BILLED"
    ON_HOLD = "ON_HOLD"
    VOID = "VOID"


class BillEnergySource(str, enum.Enum):
    """Where the billed energy came from (CE-12).

    Attributes:
        METER_STOP: The charger's closing reading minus its start reading.
        LAST_MEASUREMENT: The newest measurement, when the stop reading was
            missing.
    """

    METER_STOP = "METER_STOP"
    LAST_MEASUREMENT = "LAST_MEASUREMENT"


class PaymentPurpose(str, enum.Enum):
    """What a payment is for (BL-15).

    Attributes:
        TOP_UP: Adds money to the person's wallet.
        REFUND: Unused balance returned through the original channel.
    """

    TOP_UP = "TOP_UP"
    REFUND = "REFUND"


class PaymentMethod(str, enum.Enum):
    """How the money moved (BL-15).

    Attributes:
        BANK_TRANSFER: A bank transfer through a VietQR code.
    """

    BANK_TRANSFER = "BANK_TRANSFER"


class PaymentStatus(str, enum.Enum):
    """Observed outcome of a payment, so it has no reason column.

    Attributes:
        PENDING: The QR was shown, no money yet.
        SUCCEEDED: Money received (or the refund sent).
        FAILED: Failed or expired unpaid.
    """

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class WalletStatus(str, enum.Enum):
    """Status of a wallet, decided by us (DM-19).

    Attributes:
        ACTIVE: Usable.
        BLOCKED: No top-up or session start, for example during a fraud check.
    """

    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"


class WalletTransactionType(str, enum.Enum):
    """Kind of movement in a wallet's ledger (BL-14).

    Attributes:
        TOP_UP: Money in, from a payment.
        SESSION_BILL: Money out, pays a session bill.
        REFUND: Money out, unused balance returned through a payment.
        ADJUSTMENT: Money in or out by our staff, with a reason.
    """

    TOP_UP = "TOP_UP"
    SESSION_BILL = "SESSION_BILL"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"
