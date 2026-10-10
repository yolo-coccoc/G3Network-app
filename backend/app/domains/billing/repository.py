"""Database queries of the billing domain.

Tariffs and their versions, session bills, wallets and their ledger, payments.
No business rule and no commit; ``flush`` only to learn generated values or
constraint errors. An ``organization_id`` argument is the data scope: ``None``
means no restriction (internal staff or a system caller).

A bill belongs to the organization that paid for its session, which lives on
``charging_sessions`` (DM-24). Billing may not import that domain's models, so
this module describes the few columns it reads with a lightweight table
definition (the same approach as ``drivers`` and ``warranties``).
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import column, func, select, table, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.billing.models import (
    ChargingSessionBillModel,
    PaymentModel,
    TariffModel,
    TariffVersionModel,
    WalletModel,
    WalletTransactionModel,
)

# The columns of `charging_sessions` a bill's scope and payer are read from.
_sessions = table(
    "charging_sessions",
    column("session_id", PG_UUID(as_uuid=True)),
    column("organization_id", PG_UUID(as_uuid=True)),
    column("started_by", PG_UUID(as_uuid=True)),
)


# --- Tariffs ----------------------------------------------------------------


async def insert_tariff(db: AsyncSession, values: dict[str, object]) -> TariffModel:
    """Insert a tariff.

    Args:
        db: The current async session.
        values: Column values.

    Returns:
        The new row, flushed.
    """
    tariff_record = TariffModel(**values)
    db.add(tariff_record)
    await db.flush()
    return tariff_record


async def get_tariff_by_id(
    db: AsyncSession,
    tariff_id: UUID,
    *,
    organization_id: UUID | None = None,
    for_update: bool = False,
) -> TariffModel | None:
    """Find a tariff, optionally inside an owner's reach and locked.

    Args:
        db: The current async session.
        tariff_id: UUID of the tariff.
        organization_id: Only a tariff of this owner; ``None`` for any.
        for_update: Lock the row until the transaction ends (publishing a
            version numbers it from the newest one).

    Returns:
        The tariff, or ``None``.
    """
    query = select(TariffModel).where(TariffModel.tariff_id == tariff_id)
    if organization_id is not None:
        query = query.where(TariffModel.organization_id == organization_id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one_or_none()


async def find_active_tariff(
    db: AsyncSession, organization_id: UUID, location_id: UUID | None
) -> TariffModel | None:
    """Find the ACTIVE tariff of an owner at a location, or its default.

    Args:
        db: The current async session.
        organization_id: The tariff's owner.
        location_id: The location, or ``None`` for the owner's default.

    Returns:
        The tariff, or ``None`` (the partial unique index allows at most one).
    """
    query = select(TariffModel).where(
        TariffModel.organization_id == organization_id,
        TariffModel.status == "ACTIVE",
        TariffModel.location_id.is_(None)
        if location_id is None
        else TariffModel.location_id == location_id,
    )
    return (await db.execute(query)).scalar_one_or_none()


async def list_tariffs(
    db: AsyncSession,
    *,
    organization_id: UUID | None,
    location_id: UUID | None,
    status: str | None,
    offset: int,
    limit: int,
) -> tuple[Sequence[TariffModel], int]:
    """List tariffs with filters, newest first, and the total count.

    Args:
        db: The current async session.
        organization_id: Only this owner's tariffs; ``None`` for any.
        location_id: Only the tariff of this location.
        status: Only this status.
        offset: Rows to skip.
        limit: Rows to return.

    Returns:
        The page and the total number of matching rows.
    """
    conditions = []
    if organization_id is not None:
        conditions.append(TariffModel.organization_id == organization_id)
    if location_id is not None:
        conditions.append(TariffModel.location_id == location_id)
    if status is not None:
        conditions.append(TariffModel.status == status)
    total = (
        await db.execute(
            select(func.count()).select_from(TariffModel).where(*conditions)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(TariffModel)
            .where(*conditions)
            .order_by(TariffModel.created_at.desc(), TariffModel.tariff_id)
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    return rows.all(), int(total)


async def find_latest_tariff_version(
    db: AsyncSession, tariff_id: UUID
) -> TariffVersionModel | None:
    """Find the version with the highest number of a tariff.

    Args:
        db: The current async session.
        tariff_id: The tariff.

    Returns:
        The newest-numbered version, or ``None`` for a tariff without any.
    """
    query = (
        select(TariffVersionModel)
        .where(TariffVersionModel.tariff_id == tariff_id)
        .order_by(TariffVersionModel.version_no.desc())
        .limit(1)
    )
    return (await db.execute(query)).scalar_one_or_none()


async def insert_tariff_version(
    db: AsyncSession, values: dict[str, object]
) -> TariffVersionModel:
    """Insert an immutable tariff version.

    Args:
        db: The current async session.
        values: Column values.

    Returns:
        The new row, flushed.
    """
    version_record = TariffVersionModel(**values)
    db.add(version_record)
    await db.flush()
    return version_record


async def find_tariff_version_in_force(
    db: AsyncSession, tariff_id: UUID, at: datetime
) -> TariffVersionModel | None:
    """Find a tariff's version in force at a moment (BL-09).

    Args:
        db: The current async session.
        tariff_id: The tariff.
        at: The moment.

    Returns:
        The newest version whose ``effective_from`` has passed, or ``None``.
    """
    query = (
        select(TariffVersionModel)
        .where(
            TariffVersionModel.tariff_id == tariff_id,
            TariffVersionModel.effective_from <= at,
        )
        .order_by(
            TariffVersionModel.effective_from.desc(),
            TariffVersionModel.version_no.desc(),
        )
        .limit(1)
    )
    return (await db.execute(query)).scalar_one_or_none()


async def list_tariff_versions(
    db: AsyncSession, tariff_id: UUID
) -> Sequence[TariffVersionModel]:
    """List a tariff's versions, newest number first.

    Args:
        db: The current async session.
        tariff_id: The tariff.

    Returns:
        Its versions.
    """
    rows = await db.execute(
        select(TariffVersionModel)
        .where(TariffVersionModel.tariff_id == tariff_id)
        .order_by(TariffVersionModel.version_no.desc())
    )
    return rows.scalars().all()


async def get_tariff_version_by_id(
    db: AsyncSession, tariff_version_id: UUID
) -> TariffVersionModel | None:
    """Find a tariff version by ID.

    Args:
        db: The current async session.
        tariff_version_id: UUID of the version.

    Returns:
        The version, or ``None``.
    """
    return (
        await db.execute(
            select(TariffVersionModel).where(
                TariffVersionModel.tariff_version_id == tariff_version_id
            )
        )
    ).scalar_one_or_none()


# --- Session bills ----------------------------------------------------------


async def find_bill_by_session_id(
    db: AsyncSession, session_id: UUID, *, for_update: bool = False
) -> ChargingSessionBillModel | None:
    """Find the bill of a charging session (one bill per session, BL-10).

    Args:
        db: The current async session.
        session_id: The charging session.
        for_update: Lock the row until the transaction ends.

    Returns:
        The bill, or ``None`` when none was created.
    """
    query = select(ChargingSessionBillModel).where(
        ChargingSessionBillModel.session_id == session_id
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one_or_none()


async def insert_bill(
    db: AsyncSession, values: dict[str, object]
) -> ChargingSessionBillModel:
    """Insert a session bill.

    Args:
        db: The current async session.
        values: Column values.

    Returns:
        The new row, flushed.
    """
    bill_record = ChargingSessionBillModel(**values)
    db.add(bill_record)
    await db.flush()
    return bill_record


async def get_bill_by_id(
    db: AsyncSession,
    bill_id: UUID,
    *,
    organization_id: UUID | None = None,
    for_update: bool = False,
) -> ChargingSessionBillModel | None:
    """Find a bill by ID, optionally inside a payer organization's reach.

    Args:
        db: The current async session.
        bill_id: UUID of the bill.
        organization_id: Only a bill whose session this organization paid for;
            ``None`` for any.
        for_update: Lock the bill row until the transaction ends.

    Returns:
        The bill, or ``None``.
    """
    query = select(ChargingSessionBillModel).where(
        ChargingSessionBillModel.charging_session_bill_id == bill_id
    )
    if organization_id is not None:
        query = query.where(
            ChargingSessionBillModel.session_id.in_(
                select(_sessions.c.session_id).where(
                    _sessions.c.organization_id == organization_id
                )
            )
        )
    if for_update:
        query = query.with_for_update(of=ChargingSessionBillModel).execution_options(
            populate_existing=True
        )
    return (await db.execute(query)).scalar_one_or_none()


async def find_session_payer(
    db: AsyncSession, session_id: UUID
) -> tuple[UUID, UUID] | None:
    """Read who paid for a session: its organization and scanning user.

    Args:
        db: The current async session.
        session_id: The charging session.

    Returns:
        ``(organization_id, started_by)``, or ``None`` for an unknown session.
    """
    row = (
        await db.execute(
            select(_sessions.c.organization_id, _sessions.c.started_by).where(
                _sessions.c.session_id == session_id
            )
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


async def list_bills(
    db: AsyncSession,
    *,
    organization_id: UUID | None,
    payer_organization_id: UUID | None,
    started_by: UUID | None,
    status: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    offset: int,
    limit: int,
) -> tuple[Sequence[tuple[ChargingSessionBillModel, UUID, UUID]], int]:
    """List session bills with filters, newest first, and the total count.

    Args:
        db: The current async session.
        organization_id: The data scope: only bills whose session this
            organization paid for; ``None`` for no restriction.
        payer_organization_id: Extra filter for staff: one payer organization.
        started_by: Only bills of sessions this user scanned.
        status: Only this bill status.
        created_from: Only bills created at or after this time (the scan).
        created_to: Only bills created before this time.
        offset: Rows to skip.
        limit: Rows to return.

    Returns:
        ``(bill, payer organization, scanning user)`` rows and the total.
    """
    conditions = []
    scope = organization_id if organization_id is not None else payer_organization_id
    if scope is not None:
        conditions.append(_sessions.c.organization_id == scope)
    if started_by is not None:
        conditions.append(_sessions.c.started_by == started_by)
    if status is not None:
        conditions.append(ChargingSessionBillModel.status == status)
    if created_from is not None:
        conditions.append(ChargingSessionBillModel.created_at >= created_from)
    if created_to is not None:
        conditions.append(ChargingSessionBillModel.created_at < created_to)
    joined = ChargingSessionBillModel.session_id == _sessions.c.session_id
    total = (
        await db.execute(
            select(func.count())
            .select_from(ChargingSessionBillModel)
            .join(_sessions, joined)
            .where(*conditions)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(
                ChargingSessionBillModel,
                _sessions.c.organization_id,
                _sessions.c.started_by,
            )
            .join(_sessions, joined)
            .where(*conditions)
            .order_by(
                ChargingSessionBillModel.created_at.desc(),
                ChargingSessionBillModel.charging_session_bill_id,
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [(row[0], row[1], row[2]) for row in rows], int(total)


async def is_bill_paid(db: AsyncSession, bill_id: UUID) -> bool:
    """Tell whether the wallet ledger holds the payment of a bill (BL-14).

    Args:
        db: The current async session.
        bill_id: The bill.

    Returns:
        ``True`` when a ``SESSION_BILL`` transaction points at it.
    """
    found = (
        await db.execute(
            select(WalletTransactionModel.wallet_transaction_id).where(
                WalletTransactionModel.charging_session_bill_id == bill_id
            )
        )
    ).first()
    return found is not None


# --- Wallets and ledger -----------------------------------------------------


async def find_wallet_by_user_id(
    db: AsyncSession, user_id: UUID, *, for_update: bool = False
) -> WalletModel | None:
    """Find the wallet of a person (one per person, BL-13).

    Args:
        db: The current async session.
        user_id: The person holding the wallet.
        for_update: Lock the row until the transaction ends (every balance
            movement does).

    Returns:
        The wallet, or ``None`` when the person has none yet.
    """
    query = select(WalletModel).where(WalletModel.user_id == user_id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one_or_none()


async def insert_wallet(db: AsyncSession, values: dict[str, object]) -> WalletModel:
    """Insert a wallet.

    Args:
        db: The current async session.
        values: Column values.

    Returns:
        The new row, flushed.
    """
    wallet_record = WalletModel(**values)
    db.add(wallet_record)
    await db.flush()
    return wallet_record


async def update_wallet_balance(
    db: AsyncSession, wallet_id: UUID, new_balance: Decimal
) -> None:
    """Write a wallet's balance without touching its other columns.

    ``updated_at`` is set to itself on purpose: it is a tracked column, so a
    change would write a history row for every ledger movement, and the
    balance is deliberately untracked (database.md).

    Args:
        db: The current async session.
        wallet_id: The wallet.
        new_balance: The balance after the movement.

    Side Effects:
        One UPDATE; flushes, does not commit.
    """
    await db.execute(
        update(WalletModel)
        .where(WalletModel.wallet_id == wallet_id)
        .values(balance=new_balance, updated_at=WalletModel.updated_at)
    )
    await db.flush()


async def insert_wallet_transaction(
    db: AsyncSession, values: dict[str, object]
) -> WalletTransactionModel:
    """Append a ledger row.

    Args:
        db: The current async session.
        values: Column values.

    Returns:
        The new row, flushed.
    """
    transaction_record = WalletTransactionModel(**values)
    db.add(transaction_record)
    await db.flush()
    return transaction_record


async def list_wallet_transactions(
    db: AsyncSession, wallet_id: UUID, *, offset: int, limit: int
) -> tuple[Sequence[WalletTransactionModel], int]:
    """List a wallet's statement, newest first, and the total count.

    Args:
        db: The current async session.
        wallet_id: The wallet.
        offset: Rows to skip.
        limit: Rows to return.

    Returns:
        The page and the total number of ledger rows.
    """
    total = (
        await db.execute(
            select(func.count())
            .select_from(WalletTransactionModel)
            .where(WalletTransactionModel.wallet_id == wallet_id)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(WalletTransactionModel)
            .where(WalletTransactionModel.wallet_id == wallet_id)
            .order_by(
                WalletTransactionModel.occurred_at.desc(),
                WalletTransactionModel.wallet_transaction_id,
            )
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    return rows.all(), int(total)


# --- Payments ---------------------------------------------------------------


async def insert_payment(db: AsyncSession, values: dict[str, object]) -> PaymentModel:
    """Insert a payment.

    Args:
        db: The current async session.
        values: Column values.

    Returns:
        The new row, flushed.
    """
    payment_record = PaymentModel(**values)
    db.add(payment_record)
    await db.flush()
    return payment_record


async def get_payment_by_id(
    db: AsyncSession, payment_id: UUID, *, for_update: bool = False
) -> PaymentModel | None:
    """Find a payment by ID.

    Args:
        db: The current async session.
        payment_id: UUID of the payment.
        for_update: Lock the row until the transaction ends.

    Returns:
        The payment, or ``None``.
    """
    query = select(PaymentModel).where(PaymentModel.payment_id == payment_id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one_or_none()


async def find_payment_by_transfer_code(
    db: AsyncSession, transfer_code: str, *, for_update: bool = False
) -> PaymentModel | None:
    """Find the payment that carries a transfer code.

    Args:
        db: The current async session.
        transfer_code: The unique content of the transfer.
        for_update: Lock the row until the transaction ends.

    Returns:
        The payment, or ``None``.
    """
    query = select(PaymentModel).where(PaymentModel.transfer_code == transfer_code)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one_or_none()


async def find_payment_by_gateway_reference(
    db: AsyncSession, method: str, gateway_reference: str
) -> PaymentModel | None:
    """Find the payment already recorded for a bank transaction.

    Args:
        db: The current async session.
        method: The payment method.
        gateway_reference: The bank's reference of the transaction.

    Returns:
        The payment, or ``None``.
    """
    return (
        await db.execute(
            select(PaymentModel).where(
                PaymentModel.method == method,
                PaymentModel.gateway_reference == gateway_reference,
            )
        )
    ).scalar_one_or_none()
