"""Wallets and their append-only ledger (PAY-07, BL-13, BL-14).

A wallet is a person's prepaid balance (one per person, created lazily the
first time something needs it). Every movement is a `wallet_transactions` row
with the balance right after it; the wallet's ``balance`` is always the sum of
its rows. Rows are never edited or deleted: a mistake is corrected by an
``ADJUSTMENT`` by our staff, with a reason.

Concurrency: every movement locks the wallet row first (``SELECT ... FOR
UPDATE``), so two movements of one person line up and each ``balance_after``
is true. The balance may go below zero (a long charge, BL-14); nothing here
refuses a debit. Balance updates leave ``updated_at`` alone on purpose, so the
wallet's change history (which tracks every column but ``balance``) records
decisions only (see ``repository.update_wallet_balance``).

Functions run inside the caller's transaction and never commit or roll back.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.billing.repository as billing_repository
from app.domains.billing.exceptions import (
    WalletConflictError,
    WalletInputError,
    WalletNotFoundError,
)
from app.domains.billing.models import WalletModel, WalletTransactionModel
from app.domains.billing.schemas import (
    WalletAdjustmentRequest,
    WalletResponse,
    WalletStatusRequest,
    WalletTransactionListResponse,
    WalletTransactionResponse,
)
from app.domains.billing.types import WalletStatus, WalletTransactionType
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window
from app.libs.db.history import set_change_context

CURRENCY_VND = "VND"


async def get_or_create_wallet_for_update(
    db: AsyncSession, user_id: UUID
) -> WalletModel:
    """Find a person's wallet and lock it, creating it on first need (BL-13).

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person.

    Returns:
        The locked wallet; a new one is ACTIVE with a zero balance.

    Raises:
        WalletNotFoundError: The user does not exist (the foreign key refused
            the new wallet).

    Side Effects:
        May insert the wallet, in a savepoint so a concurrent creation or an
        unknown user does not poison the transaction.
    """
    wallet_record = await billing_repository.find_wallet_by_user_id(
        db, user_id, for_update=True
    )
    if wallet_record is not None:
        return wallet_record
    try:
        async with db.begin_nested():
            await billing_repository.insert_wallet(
                db,
                {
                    "user_id": user_id,
                    "balance": Decimal(0),
                    "currency": CURRENCY_VND,
                    "status": WalletStatus.ACTIVE.value,
                },
            )
    except IntegrityError:
        # Either a request of the same person created it a moment ago, or the
        # user does not exist: the re-read tells which.
        pass
    wallet_record = await billing_repository.find_wallet_by_user_id(
        db, user_id, for_update=True
    )
    if wallet_record is None:
        raise WalletNotFoundError(f"User '{user_id}' was not found")
    return wallet_record


async def apply_wallet_movement(
    db: AsyncSession,
    wallet_record: WalletModel,
    *,
    transaction_type: WalletTransactionType,
    amount: Decimal,
    payment_id: UUID | None = None,
    charging_session_bill_id: UUID | None = None,
    created_by: UUID | None = None,
    reason: str | None = None,
    occurred_at: datetime | None = None,
) -> WalletTransactionModel:
    """Append one ledger row and move the wallet's balance by the same amount.

    The caller holds the wallet lock (`get_or_create_wallet_for_update`) and
    guarantees the links and sign the table's check constraints ask for.

    Args:
        db: The async session owned by the entry boundary.
        wallet_record: The locked wallet.
        transaction_type: The kind of movement.
        amount: Signed whole-dong amount.
        payment_id: The payment behind a top-up or refund.
        charging_session_bill_id: The bill a session payment pays.
        created_by: The staff member of an adjustment.
        reason: Why, for an adjustment.
        occurred_at: When it happened; now when omitted.

    Returns:
        The ledger row.

    Side Effects:
        One INSERT and one UPDATE of the balance, then a refresh of the wallet.
    """
    new_balance = wallet_record.balance + amount
    await billing_repository.update_wallet_balance(
        db, wallet_record.wallet_id, new_balance
    )
    transaction_record = await billing_repository.insert_wallet_transaction(
        db,
        {
            "wallet_id": wallet_record.wallet_id,
            "transaction_type": transaction_type.value,
            "amount": amount,
            "balance_after": new_balance,
            "payment_id": payment_id,
            "charging_session_bill_id": charging_session_bill_id,
            "created_by": created_by,
            "reason": reason,
            "occurred_at": occurred_at or utc_now(),
        },
    )
    await db.refresh(wallet_record)
    return transaction_record


def to_wallet_response(wallet_record: WalletModel) -> WalletResponse:
    """Convert a wallet row into its response (pure mapping).

    Args:
        wallet_record: The wallet.

    Returns:
        The response; money in whole dong.
    """
    return WalletResponse(
        wallet_id=wallet_record.wallet_id,
        user_id=wallet_record.user_id,
        balance=int(wallet_record.balance),
        currency=wallet_record.currency,
        status=WalletStatus(wallet_record.status),
        status_reason=wallet_record.status_reason,
        minimum_balance_to_charge=int(settings.BILLING_MIN_BALANCE_VND),
    )


def to_wallet_transaction_response(
    transaction_record: WalletTransactionModel,
) -> WalletTransactionResponse:
    """Convert a ledger row into its response (pure mapping).

    Args:
        transaction_record: The ledger row.

    Returns:
        The response; money in whole dong.
    """
    return WalletTransactionResponse(
        wallet_transaction_id=transaction_record.wallet_transaction_id,
        transaction_type=WalletTransactionType(transaction_record.transaction_type),
        amount=int(transaction_record.amount),
        balance_after=int(transaction_record.balance_after),
        payment_id=transaction_record.payment_id,
        charging_session_bill_id=transaction_record.charging_session_bill_id,
        reason=transaction_record.reason,
        occurred_at=transaction_record.occurred_at,
    )


async def get_my_wallet(db: AsyncSession, *, principal: Principal) -> WalletResponse:
    """Get the caller's own wallet, creating it on first use (PAY-07).

    Args:
        db: The async session owned by the entry boundary.
        principal: The caller; the wallet is the person's, whatever
            organization the session acts for (BL-13).

    Returns:
        The wallet.

    Side Effects:
        May create the wallet.
    """
    return to_wallet_response(
        await get_or_create_wallet_for_update(db, principal.user_id)
    )


async def _build_transaction_page(
    db: AsyncSession, wallet_record: WalletModel, page: int, page_size: int
) -> WalletTransactionListResponse:
    """Build one page of a wallet's statement.

    Args:
        db: The async session owned by the entry boundary.
        wallet_record: The wallet.
        page: Page number.
        page_size: Rows per page.

    Returns:
        The page, newest first.
    """
    window = normalize_page_window(page, page_size)
    rows, total = await billing_repository.list_wallet_transactions(
        db, wallet_record.wallet_id, offset=window.offset, limit=window.page_size
    )
    return WalletTransactionListResponse(
        items=[to_wallet_transaction_response(row) for row in rows],
        total=total,
        page=window.page,
        page_size=window.page_size,
    )


async def list_my_wallet_transactions(
    db: AsyncSession, *, principal: Principal, page: int, page_size: int
) -> WalletTransactionListResponse:
    """List the caller's own statement, newest first.

    Args:
        db: The async session owned by the entry boundary.
        principal: The caller.
        page: Page number.
        page_size: Rows per page.

    Returns:
        The page; empty for a person with no wallet yet (none is created).
    """
    wallet_record = await billing_repository.find_wallet_by_user_id(
        db, principal.user_id
    )
    if wallet_record is None:
        window = normalize_page_window(page, page_size)
        return WalletTransactionListResponse(
            items=[], total=0, page=window.page, page_size=window.page_size
        )
    return await _build_transaction_page(db, wallet_record, page, page_size)


async def _get_user_wallet(db: AsyncSession, user_id: UUID) -> WalletModel:
    """Find a person's wallet for staff.

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person.

    Returns:
        The wallet.

    Raises:
        WalletNotFoundError: The person has no wallet.
    """
    wallet_record = await billing_repository.find_wallet_by_user_id(db, user_id)
    if wallet_record is None:
        raise WalletNotFoundError(f"User '{user_id}' has no wallet")
    return wallet_record


async def get_user_wallet(db: AsyncSession, user_id: UUID) -> WalletResponse:
    """Get a person's wallet (billing staff).

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person.

    Returns:
        The wallet.

    Raises:
        WalletNotFoundError: The person has no wallet (404).
    """
    return to_wallet_response(await _get_user_wallet(db, user_id))


async def list_user_wallet_transactions(
    db: AsyncSession, user_id: UUID, *, page: int, page_size: int
) -> WalletTransactionListResponse:
    """List a person's statement (billing staff).

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person.
        page: Page number.
        page_size: Rows per page.

    Returns:
        The page, newest first.

    Raises:
        WalletNotFoundError: The person has no wallet (404).
    """
    return await _build_transaction_page(
        db, await _get_user_wallet(db, user_id), page, page_size
    )


async def adjust_wallet(
    db: AsyncSession,
    user_id: UUID,
    adjustment_request: WalletAdjustmentRequest,
    *,
    principal: Principal,
) -> WalletTransactionResponse:
    """Correct a balance by hand: an ``ADJUSTMENT`` line with a reason (BL-14).

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person whose balance changes; the wallet is created if the
            person has none.
        adjustment_request: Signed amount and the reason.
        principal: The staff member; recorded as ``created_by``.

    Returns:
        The ledger line.

    Raises:
        WalletInputError: The amount is zero, or the reason is blank (400).
        WalletNotFoundError: The user does not exist (404).
    """
    reason = adjustment_request.reason.strip()
    if adjustment_request.amount == 0:
        raise WalletInputError("An adjustment cannot be zero")
    if not reason:
        raise WalletInputError("An adjustment needs a reason")
    wallet_record = await get_or_create_wallet_for_update(db, user_id)
    transaction_record = await apply_wallet_movement(
        db,
        wallet_record,
        transaction_type=WalletTransactionType.ADJUSTMENT,
        amount=Decimal(adjustment_request.amount),
        created_by=principal.user_id,
        reason=reason,
    )
    return to_wallet_transaction_response(transaction_record)


async def set_wallet_status(
    db: AsyncSession,
    user_id: UUID,
    status_request: WalletStatusRequest,
    *,
    principal: Principal,
) -> WalletResponse:
    """Block or unblock a wallet with a reason (BL-13, a tracked decision).

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person.
        status_request: The status to set and why.
        principal: The staff member; recorded in the wallet's history.

    Returns:
        The wallet.

    Raises:
        WalletNotFoundError: The person has no wallet (404).
        WalletConflictError: The wallet already has that status (409).
    """
    await _get_user_wallet(db, user_id)
    wallet_record = await get_or_create_wallet_for_update(db, user_id)
    if wallet_record.status == status_request.status.value:
        raise WalletConflictError(f"The wallet is already {wallet_record.status}")
    reason = status_request.reason.strip()
    await set_change_context(db, changed_by=principal.user_id, change_reason=reason)
    wallet_record.status = status_request.status.value
    wallet_record.status_reason = reason
    await db.flush()
    return to_wallet_response(wallet_record)
