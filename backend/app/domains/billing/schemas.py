"""Pydantic request and response schemas of the billing HTTP API.

Money is a whole number of dong (``int``) on the wire: the database keeps
``numeric(14,2)`` columns that only ever hold whole dong, and JSON ``Decimal``
values would travel as strings. Rates (VAT percent) stay ``Decimal``.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.billing.types import (
    BankNotificationResult,
    ChargingSessionBillStatus,
    PaymentPurpose,
    PaymentStatus,
    TariffStatus,
    WalletStatus,
    WalletTransactionType,
)

_REASON_FIELD = Field(..., min_length=1, max_length=200)


# --- Tariffs ----------------------------------------------------------------


class TariffPeriodSchema(BaseModel):
    """One time-of-use period of a tariff version (BL-09), in Vietnam time.

    Attributes:
        days: Weekday codes ``MON`` .. ``SUN`` the period starts on.
        from_time: Start, ``HH:MM`` (JSON key ``from``).
        to_time: End, ``HH:MM`` or ``24:00`` (JSON key ``to``); an end at or
            before the start crosses midnight.
        price_per_kwh: Price in the period, before VAT, whole dong.
    """

    model_config = ConfigDict(populate_by_name=True)

    days: list[str] = Field(..., min_length=1, max_length=7)
    from_time: str = Field(..., alias="from")
    to_time: str = Field(..., alias="to")
    price_per_kwh: int = Field(..., ge=0)


class TariffCreateRequest(BaseModel):
    """Create a tariff (BL-08).

    Attributes:
        name: Name shown in the app and on receipts.
        location_id: The location it prices; ``None`` is the owner's default
            for all its locations.
        organization_id: The owner; internal staff may name another
            organization, everyone else owns the tariff themselves.
        currency: ISO 4217 code; only ``VND`` is accepted today.
    """

    name: str = Field(..., min_length=1, max_length=100)
    location_id: UUID | None = None
    organization_id: UUID | None = None
    currency: str = Field(default="VND", pattern="^VND$")


class TariffUpdateRequest(BaseModel):
    """Rename a tariff.

    Attributes:
        name: The new name.
    """

    name: str = Field(..., min_length=1, max_length=100)


class TariffStatusRequest(BaseModel):
    """Retire or reactivate a tariff, with the reason (DM-19).

    Attributes:
        reason: Why, stored as the tariff's ``status_reason``.
    """

    reason: str = _REASON_FIELD


class TariffVersionPublishRequest(BaseModel):
    """Publish a new immutable version of a tariff (BL-09).

    Attributes:
        price_per_kwh: Normal price per kWh before VAT, whole dong.
        vat_rate_percent: VAT rate, 0 to 100.
        time_periods: Time-of-use periods replacing the normal price in some
            hours; ``None`` for one price all day.
        effective_from: When it takes over; now or later (default now).
        change_reason: Why this version is published.
    """

    price_per_kwh: int = Field(..., ge=0)
    vat_rate_percent: Decimal = Field(..., ge=0, le=100, decimal_places=2)
    time_periods: list[TariffPeriodSchema] | None = None
    effective_from: datetime | None = None
    change_reason: str = _REASON_FIELD


class TariffVersionResponse(BaseModel):
    """One published version of a tariff.

    Attributes:
        tariff_version_id: UUID of the version.
        tariff_id: UUID of the tariff.
        version_no: Number within the tariff, from 1.
        effective_from: When it takes (or took) over.
        price_per_kwh: Normal price per kWh before VAT.
        vat_rate_percent: VAT rate.
        time_periods: Time-of-use periods, ``None`` for one price all day.
        change_reason: Why it was published.
        created_by: The user who published it.
        created_at: When it was published.
    """

    tariff_version_id: UUID
    tariff_id: UUID
    version_no: int
    effective_from: datetime
    price_per_kwh: int
    vat_rate_percent: Decimal
    time_periods: list[dict[str, object]] | None
    change_reason: str
    created_by: UUID
    created_at: datetime


class TariffResponse(BaseModel):
    """A tariff and the version in force now.

    Attributes:
        tariff_id: UUID of the tariff.
        organization_id: The owner.
        location_id: The location it prices, ``None`` for the owner's default.
        name: Name shown in the app.
        currency: ISO 4217 code.
        status: ``ACTIVE`` or ``INACTIVE``.
        status_reason: Why it has that status.
        current_version: The version in force now, ``None`` until one takes
            effect.
        created_at: When it was created.
        updated_at: When it was last changed.
    """

    tariff_id: UUID
    organization_id: UUID
    location_id: UUID | None
    name: str
    currency: str
    status: TariffStatus
    status_reason: str | None
    current_version: TariffVersionResponse | None
    created_at: datetime
    updated_at: datetime


class TariffListResponse(BaseModel):
    """A page of tariffs.

    Attributes:
        items: The tariffs.
        total: Matching tariffs over all pages.
        page: Page number.
        page_size: Rows per page.
    """

    items: list[TariffResponse]
    total: int
    page: int
    page_size: int


class TariffQuoteResponse(BaseModel):
    """The price a charger asks at a moment (PAY-09).

    Attributes:
        tariff_id: The tariff in force.
        tariff_version_id: Its version in force.
        version_no: The version number.
        tariff_name: Name of the tariff.
        currency: ISO 4217 code.
        at: The moment the price is for.
        price_per_kwh: Price for that hour before VAT.
        price_per_kwh_with_vat: The same price with VAT, rounded to whole dong.
        normal_price_per_kwh: The version's normal price before VAT.
        vat_rate_percent: VAT rate.
        time_periods: The version's time-of-use periods, so the app can show
            the cheapest window.
    """

    tariff_id: UUID
    tariff_version_id: UUID
    version_no: int
    tariff_name: str
    currency: str
    at: datetime
    price_per_kwh: int
    price_per_kwh_with_vat: int
    normal_price_per_kwh: int
    vat_rate_percent: Decimal
    time_periods: list[dict[str, object]] | None


# --- Bills ------------------------------------------------------------------


class BillReviewRequest(BaseModel):
    """Release or void a bill that is on hold, with the reason (DM-19).

    Attributes:
        reason: What the reviewer checked or why the bill is voided.
    """

    reason: str = _REASON_FIELD


class SessionBillResponse(BaseModel):
    """The bill of one charging session (CHG-03, PAY-10).

    Attributes:
        bill_id: UUID of the bill.
        session_id: The charging session.
        organization_id: The organization that paid for the session.
        started_by: The user who scanned, whose wallet pays.
        status: ``QUOTED``, ``BILLED``, ``ON_HOLD`` or ``VOID``.
        status_reason: Why it has that status.
        tariff_version_id: The tariff version whose price was frozen at the scan.
        price_per_kwh: The frozen price before VAT.
        vat_rate_percent: The frozen VAT rate.
        energy_wh: Energy billed in Wh, ``None`` until figures exist.
        energy_source: ``METER_STOP`` or ``LAST_MEASUREMENT``.
        amount_before_vat: Rounded amount before VAT, ``None`` until billed.
        vat_amount: Rounded VAT, ``None`` until billed.
        total_amount: Amount plus VAT, ``None`` until billed.
        is_paid: Whether the wallet ledger holds the payment of the bill.
        billed_at: When the amount was fixed.
        created_at: When the price was frozen (the scan).
    """

    bill_id: UUID
    session_id: UUID
    organization_id: UUID
    started_by: UUID
    status: ChargingSessionBillStatus
    status_reason: str | None
    tariff_version_id: UUID
    price_per_kwh: int
    vat_rate_percent: Decimal
    energy_wh: Decimal | None
    energy_source: str | None
    amount_before_vat: int | None
    vat_amount: int | None
    total_amount: int | None
    is_paid: bool
    billed_at: datetime | None
    created_at: datetime


class SessionBillListResponse(BaseModel):
    """A page of session bills.

    Attributes:
        items: The bills.
        total: Matching bills over all pages.
        page: Page number.
        page_size: Rows per page.
    """

    items: list[SessionBillResponse]
    total: int
    page: int
    page_size: int


# --- Wallets ----------------------------------------------------------------


class WalletResponse(BaseModel):
    """A person's wallet.

    Attributes:
        wallet_id: UUID of the wallet.
        user_id: The person holding it.
        balance: Current balance in dong; may be negative after a long charge.
        currency: ISO 4217 code.
        status: ``ACTIVE`` or ``BLOCKED``.
        status_reason: Why it has that status.
        minimum_balance_to_charge: Balance needed to start a charge, ``0`` when
            the check is off.
    """

    wallet_id: UUID
    user_id: UUID
    balance: int
    currency: str
    status: WalletStatus
    status_reason: str | None
    minimum_balance_to_charge: int


class WalletTransactionResponse(BaseModel):
    """One line of a wallet's statement.

    Attributes:
        wallet_transaction_id: UUID of the line.
        transaction_type: ``TOP_UP``, ``SESSION_BILL``, ``REFUND`` or
            ``ADJUSTMENT``.
        amount: Signed amount in dong.
        balance_after: Balance right after.
        payment_id: The payment behind a top-up or refund.
        charging_session_bill_id: The bill paid.
        reason: Why, for an adjustment.
        occurred_at: When it happened.
    """

    wallet_transaction_id: UUID
    transaction_type: WalletTransactionType
    amount: int
    balance_after: int
    payment_id: UUID | None
    charging_session_bill_id: UUID | None
    reason: str | None
    occurred_at: datetime


class WalletTransactionListResponse(BaseModel):
    """A page of a wallet's statement, newest first.

    Attributes:
        items: The lines.
        total: Lines over all pages.
        page: Page number.
        page_size: Rows per page.
    """

    items: list[WalletTransactionResponse]
    total: int
    page: int
    page_size: int


class WalletAdjustmentRequest(BaseModel):
    """A manual correction of a balance by our staff (BL-14).

    Attributes:
        amount: Signed amount in dong, not zero.
        reason: Why, kept on the ledger line.
    """

    amount: int
    reason: str = _REASON_FIELD


class WalletStatusRequest(BaseModel):
    """Block or unblock a wallet (BL-13).

    Attributes:
        status: The status to set.
        reason: Why, stored as the wallet's ``status_reason``.
    """

    status: WalletStatus
    reason: str = _REASON_FIELD


# --- Payments ---------------------------------------------------------------


class TopUpRequest(BaseModel):
    """Ask for a VietQR code to top up the caller's wallet (PAY-06).

    Attributes:
        amount: Amount to transfer in whole dong, within the configured limits.
    """

    amount: int = Field(..., ge=1)


class PaymentResponse(BaseModel):
    """A payment (a top-up at launch).

    Attributes:
        payment_id: UUID of the payment.
        user_id: The person it belongs to.
        purpose: ``TOP_UP`` or ``REFUND``.
        amount: Amount in dong; once paid, the amount actually received.
        currency: ISO 4217 code.
        method: ``BANK_TRANSFER``.
        transfer_code: The unique content the transfer must carry.
        status: ``PENDING``, ``SUCCEEDED`` or ``FAILED``.
        gateway_reference: The bank's reference, once paid.
        expires_at: When an unpaid code stops waiting.
        created_at: When the code was issued.
        completed_at: When the money was confirmed, or the request failed.
    """

    payment_id: UUID
    user_id: UUID
    purpose: PaymentPurpose
    amount: int
    currency: str
    method: str
    transfer_code: str | None
    status: PaymentStatus
    gateway_reference: str | None
    expires_at: datetime
    created_at: datetime
    completed_at: datetime | None


class TopUpResponse(PaymentResponse):
    """A new top-up with what the app needs to show the QR code.

    Attributes:
        transfer_code: The unique content the transfer must carry (always set
            for a top-up).
        vietqr_payload: The VietQR string the app draws as a QR image.
        bank_bin: NAPAS BIN of the receiving bank.
        account_number: The receiving account number.
        account_name: The account holder's name to show next to the code.
    """

    transfer_code: str
    vietqr_payload: str
    bank_bin: str
    account_number: str
    account_name: str


class BankNotificationResponse(BaseModel):
    """The answer to a bank notification (always 200 once authenticated).

    Attributes:
        result: What happened to it.
        payment_id: The payment it was matched to, if any.
        amount_differs: Whether the amount received differs from the amount
            the payer asked for (the amount received is credited, BL-15).
    """

    result: BankNotificationResult
    payment_id: UUID | None = None
    amount_differs: bool = False


class BankNotificationSimulationRequest(BaseModel):
    """Simulate an incoming bank transfer with the fake provider (development).

    Attributes:
        transfer_code: The code of a pending top-up; it becomes the content.
        amount: Amount received in whole dong; defaults to the amount asked.
        bank_transaction_id: The bank reference; a random one when omitted.
    """

    transfer_code: str = Field(..., min_length=1, max_length=40)
    amount: int | None = Field(None, ge=1)
    bank_transaction_id: str | None = Field(None, min_length=1, max_length=100)
