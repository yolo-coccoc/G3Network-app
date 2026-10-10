"""Shared internal enums and DTOs of the billing domain.

The billing tables store the enums as plain ``varchar`` columns (the DBML lists
the allowed values in each column note, with no database value check); the
enums name the allowed values for code. The frozen dataclasses are what other
domains receive from the billing service (never an ORM model). The tariff,
bill and payment services come with the billing work package (WP9); today the
service answers only what the QR start and the receipt need (WP8).
"""

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


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


@dataclass(frozen=True, slots=True)
class WalletStanding:
    """What the scan needs to know about a person's wallet (BL-13, BL-14).

    Attributes:
        balance: Current balance; ``0`` when the person has no wallet yet (the
            scan never creates one).
        is_blocked: Whether the wallet exists and is ``BLOCKED``.
    """

    balance: Decimal
    is_blocked: bool


@dataclass(frozen=True, slots=True)
class SessionBillReference:
    """A session's bill as the receipt shows it (CHG-03, BL-10).

    Attributes:
        status: ``QUOTED``, ``BILLED``, ``ON_HOLD`` or ``VOID``.
        price_per_kwh: Price frozen at the scan, before VAT.
        vat_rate_percent: VAT rate frozen with the price.
        energy_wh: Energy billed in Wh, ``None`` until billed.
        amount_before_vat: Rounded amount before VAT, ``None`` until billed.
        vat_amount: Rounded VAT, ``None`` until billed.
        billed_at: When the amount was fixed, ``None`` until billed.
    """

    status: ChargingSessionBillStatus
    price_per_kwh: Decimal
    vat_rate_percent: Decimal
    energy_wh: Decimal | None
    amount_before_vat: Decimal | None
    vat_amount: Decimal | None
    billed_at: datetime | None
