"""SQLAlchemy models of the billing domain.

Tariffs and their immutable versions price the energy (BL-08, BL-09); a
``charging_session_bills`` row freezes the price at the scan and fixes the
amount at the stop (BL-10); wallets and their append-only ledger hold a
person's prepaid balance (BL-13, BL-14) and ``payments`` are the real money in
or out (BL-12, BL-15). Plans, subscriptions and invoices are parked (BL-16)
and have no model. The statuses are plain ``varchar`` columns, with the allowed
values in ``types.py`` (no database value check).

The module holds models only: services, repositories and endpoints come with
the billing work package.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class TariffModel(Base):
    """Whose price it is and where it applies (BL-08).

    The prices themselves are immutable versions in ``tariff_versions``. The
    price at a location is its own ACTIVE tariff, otherwise the owner's
    default (``location_id`` ``NULL``). Change history is on (retiring or
    renaming is a decision); no soft delete (sessions point to its versions
    forever).

    Attributes:
        tariff_id: Internal UUID.
        organization_id: The owner: the organization whose locations this
            prices.
        location_id: The location it prices; ``NULL`` is the owner's default.
        name: Name shown in the app and on receipts.
        currency: ISO 4217 currency code.
        status: ``ACTIVE`` or ``INACTIVE``.
        status_reason: Why the tariff has its status, nullable.
        created_at: When the row was created.
        updated_at: When the row was last changed.
    """

    __tablename__ = "tariffs"

    tariff_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    location_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_locations.location_id", ondelete="RESTRICT"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        # One active tariff per location and one default per owner: NULL
        # locations compare equal so the owner's default is unique too.
        Index(
            "uq_tariffs_active_owner_location",
            "organization_id",
            "location_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_tariffs_location_id", "location_id"),
    )


class TariffVersionModel(Base):
    """One immutable set of prices of a tariff (BL-09).

    A price change is a new version; the current version is the newest whose
    ``effective_from`` has passed. No change history: a version is never
    edited.

    Attributes:
        tariff_version_id: Internal UUID.
        tariff_id: The tariff this version belongs to.
        version_no: Version number within the tariff, starting at 1.
        effective_from: When this version takes over from the previous one.
        price_per_kwh: Normal price per kWh, before VAT, in the tariff
            currency.
        time_periods: Time-of-use prices replacing the normal price in some
            hours (Vietnam time), a list of ``{days, from, to,
            price_per_kwh}``; ``NULL`` for one price all day.
        vat_rate_percent: VAT rate in force for this version.
        change_reason: Why this version was published.
        created_by: The user who published it.
        created_at: When it was published.
    """

    __tablename__ = "tariff_versions"

    tariff_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    tariff_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tariffs.tariff_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer(), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    price_per_kwh: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    time_periods: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSONB(), nullable=True
    )
    vat_rate_percent: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    change_reason: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "price_per_kwh >= 0", name="ck_tariff_versions_price_non_negative"
        ),
        CheckConstraint(
            "vat_rate_percent BETWEEN 0 AND 100",
            name="ck_tariff_versions_vat_rate_range",
        ),
        Index(
            "uq_tariff_versions_tariff_version_no",
            "tariff_id",
            "version_no",
            unique=True,
        ),
        Index("ix_tariff_versions_tariff_effective", "tariff_id", "effective_from"),
    )


class ChargingSessionBillModel(Base):
    """The bill of one charging session (BL-10, BL-11, CE-12, PAY-10).

    The price frozen at the scan, the kWh billed and the amount. The amounts
    are what was charged after rounding, so they are stored facts, the same on
    the receipt, the payment and the e-invoice. Change history is on (releasing
    a held bill is a person's decision); never edited after ``BILLED``.

    Attributes:
        charging_session_bill_id: Internal UUID.
        session_id: The charging session; one bill per session.
        tariff_version_id: The tariff version whose price was shown at the
            scan.
        price_per_kwh: Price for the scan's hour, before VAT, frozen for the
            whole session.
        vat_rate_percent: VAT rate frozen with the price.
        status: ``QUOTED``, ``BILLED``, ``ON_HOLD`` or ``VOID``.
        status_reason: Why the bill has its status, nullable.
        energy_wh: Energy billed, in Wh; ``NULL`` until billed.
        energy_source: ``METER_STOP`` or ``LAST_MEASUREMENT``.
        amount_before_vat: ``energy_wh / 1000 x price_per_kwh``, rounded to
            whole dong; ``NULL`` until billed.
        vat_amount: VAT on ``amount_before_vat``, rounded; ``NULL`` until
            billed.
        billed_at: When the amount was fixed.
        created_at: When the row was created: the scan time.
        updated_at: When the row was last changed.
    """

    __tablename__ = "charging_session_bills"

    charging_session_bill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    tariff_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tariff_versions.tariff_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    price_per_kwh: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    vat_rate_percent: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    energy_wh: Mapped[Decimal | None] = mapped_column(Numeric(24, 3), nullable=True)
    energy_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amount_before_vat: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    vat_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    billed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("session_id", name="uq_charging_session_bills_session_id"),
        CheckConstraint(
            "status <> 'BILLED' OR (energy_wh IS NOT NULL "
            "AND energy_source IS NOT NULL AND amount_before_vat IS NOT NULL "
            "AND vat_amount IS NOT NULL AND billed_at IS NOT NULL)",
            name="ck_charging_session_bills_billed_amounts",
        ),
        CheckConstraint(
            "status NOT IN ('QUOTED', 'VOID') OR (energy_wh IS NULL "
            "AND energy_source IS NULL AND amount_before_vat IS NULL "
            "AND vat_amount IS NULL AND billed_at IS NULL)",
            name="ck_charging_session_bills_unbilled_no_amounts",
        ),
        Index("ix_charging_session_bills_tariff_version_id", "tariff_version_id"),
        Index("ix_charging_session_bills_status_created", "status", "created_at"),
    )


class PaymentModel(Base):
    """Real money in or out of a person's wallet (BL-12, BL-15).

    Top-ups and refunds only. At launch a driver tops up by bank transfer
    through a VietQR code carrying a unique ``transfer_code``. Paying a session
    from the wallet is a wallet transaction, not a payment. No change history
    (the status is observed) and no soft delete (a financial record).

    Attributes:
        payment_id: Internal UUID.
        user_id: The person topping up, or receiving a refund.
        purpose: ``TOP_UP`` or ``REFUND``.
        refund_of_payment_id: The top-up a refund returns money through; set
            only for ``REFUND``.
        amount: Amount, always positive; for a bank transfer the amount
            actually received.
        currency: ISO 4217 currency code.
        method: How the money moved (``BANK_TRANSFER`` at launch).
        transfer_code: Unique note a bank transfer must carry; matches the
            incoming transfer to this row.
        gateway_reference: The bank's or gateway's transaction reference, for
            reconciliation; never card data.
        gateway_result_code: The bank's or gateway's own result code, as sent.
        status: ``PENDING``, ``SUCCEEDED`` or ``FAILED``; observed.
        completed_at: When the money was confirmed or the request failed.
        created_at: When the row was created: when the QR was shown.
        updated_at: When the row was last changed.
    """

    __tablename__ = "payments"

    payment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    refund_of_payment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("payments.payment_id", ondelete="RESTRICT"),
        nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    transfer_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gateway_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gateway_result_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "(purpose = 'REFUND') = (refund_of_payment_id IS NOT NULL)",
            name="ck_payments_refund_links_payment",
        ),
        Index(
            "uq_payments_transfer_code",
            "transfer_code",
            unique=True,
            postgresql_where=text("transfer_code IS NOT NULL"),
        ),
        # The same bank notification is never recorded twice.
        Index(
            "uq_payments_method_gateway_reference",
            "method",
            "gateway_reference",
            unique=True,
            postgresql_where=text("gateway_reference IS NOT NULL"),
        ),
        Index("ix_payments_user_created", "user_id", "created_at"),
    )


class WalletModel(Base):
    """A person's prepaid balance for charging (BL-13, BL-15).

    Usable only for our own services. The balance is checked against a minimum
    at the scan, never by a database constraint, because the deduction at the
    end of a session may take it below zero. Change history is on for
    decisions (blocking), with ``balance`` excluded since every transaction
    moves it. No soft delete: the ledger must stay.

    Attributes:
        wallet_id: Internal UUID.
        user_id: The person holding it; one wallet per person.
        balance: Current balance, kept equal to the sum of its transactions;
            may go negative.
        currency: ISO 4217 currency code.
        status: ``ACTIVE`` or ``BLOCKED``.
        status_reason: Why the wallet has its status, nullable.
        created_at: When the row was created.
        updated_at: When the row was last changed.
    """

    __tablename__ = "wallets"

    wallet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (Index("uq_wallets_user_id", "user_id", unique=True),)


class WalletTransactionModel(Base):
    """The append-only ledger of a wallet (BL-14).

    Every top-up, session payment, refund and staff adjustment, with the
    balance right after it. Never edited or deleted; a mistake is corrected by
    an ``ADJUSTMENT``.

    Attributes:
        wallet_transaction_id: Internal UUID.
        wallet_id: The wallet affected; the row reads its holder through it.
        transaction_type: ``TOP_UP``, ``SESSION_BILL``, ``REFUND`` or
            ``ADJUSTMENT``.
        amount: Signed amount: positive adds money, negative removes it.
        balance_after: The wallet balance right after this transaction.
        payment_id: The gateway payment behind a ``TOP_UP`` or ``REFUND``.
        charging_session_bill_id: The bill paid, for ``SESSION_BILL``; unique,
            so a bill is paid once.
        created_by: Our staff member, for an ``ADJUSTMENT``.
        reason: Why, required for an ``ADJUSTMENT``.
        occurred_at: When the transaction happened.
    """

    __tablename__ = "wallet_transactions"

    wallet_transaction_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    wallet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("wallets.wallet_id", ondelete="RESTRICT"),
        nullable=False,
    )
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    payment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("payments.payment_id", ondelete="RESTRICT"),
        nullable=True,
    )
    charging_session_bill_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "charging_session_bills.charging_session_bill_id", ondelete="RESTRICT"
        ),
        nullable=True,
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "(transaction_type NOT IN ('TOP_UP', 'REFUND') "
            "OR payment_id IS NOT NULL) "
            "AND (transaction_type <> 'SESSION_BILL' "
            "OR charging_session_bill_id IS NOT NULL) "
            "AND (transaction_type <> 'ADJUSTMENT' "
            "OR (created_by IS NOT NULL AND reason IS NOT NULL))",
            name="ck_wallet_transactions_links_match_type",
        ),
        CheckConstraint(
            "(transaction_type <> 'TOP_UP' OR amount > 0) "
            "AND (transaction_type NOT IN ('SESSION_BILL', 'REFUND') OR amount < 0)",
            name="ck_wallet_transactions_amount_sign",
        ),
        Index("ix_wallet_transactions_wallet_time", "wallet_id", "occurred_at"),
        Index(
            "uq_wallet_transactions_session_bill",
            "charging_session_bill_id",
            unique=True,
            postgresql_where=text("charging_session_bill_id IS NOT NULL"),
        ),
        Index("ix_wallet_transactions_payment_id", "payment_id"),
    )
