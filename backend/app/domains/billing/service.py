"""Public service of the billing domain (WP8 slice).

Billing is built in WP9 (tariffs, bills, wallet ledger, VietQR top-up). This
module holds the two reads the QR charging flow already needs, so WP9 extends
the module instead of replacing a stand-in:

* the wallet minimum-balance rule of BL-14, driven by the setting
  ``BILLING_MIN_BALANCE_VND`` (``0`` turns it off), and
* the bill link on a session receipt (CHG-03).

Functions run inside the caller's transaction and never commit or roll back.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.billing.repository as billing_repository
from app.domains.billing.models import ChargingSessionBillModel
from app.domains.billing.types import (
    ChargingSessionBillStatus,
    SessionBillReference,
    WalletStanding,
    WalletStatus,
)
from app.libs.common.config import settings


async def resolve_wallet_standing(db: AsyncSession, user_id: UUID) -> WalletStanding:
    """Read a person's balance and whether the wallet is blocked.

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person whose wallet pays (BL-13).

    Returns:
        The balance (``0`` when the person has no wallet) and the blocked flag.

    Side Effects:
        One read query; a missing wallet is not created.
    """
    wallet_record = await billing_repository.find_wallet_by_user_id(db, user_id)
    if wallet_record is None:
        return WalletStanding(balance=Decimal(0), is_blocked=False)
    return WalletStanding(
        balance=wallet_record.balance,
        is_blocked=wallet_record.status == WalletStatus.BLOCKED.value,
    )


async def has_minimum_balance(db: AsyncSession, user_id: UUID) -> bool:
    """Tell whether a person's wallet may start a charge (BL-14).

    The minimum is the platform setting ``BILLING_MIN_BALANCE_VND``; ``0``
    disables the check. A person without a wallet counts as a zero balance. A
    blocked wallet is not judged here: the caller reads
    ``resolve_wallet_standing`` for that.

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person whose wallet pays.

    Returns:
        ``True`` when the check is off or the balance is at or above the
        minimum.
    """
    minimum_balance = settings.BILLING_MIN_BALANCE_VND
    if minimum_balance <= 0:
        return True
    standing = await resolve_wallet_standing(db, user_id)
    return standing.balance >= minimum_balance


def to_session_bill_reference(
    bill_record: ChargingSessionBillModel,
) -> SessionBillReference:
    """Convert a bill row into the DTO other domains and the receipt use.

    Args:
        bill_record: The bill queried by the repository.

    Returns:
        The frozen reference.
    """
    return SessionBillReference(
        status=ChargingSessionBillStatus(bill_record.status),
        price_per_kwh=bill_record.price_per_kwh,
        vat_rate_percent=bill_record.vat_rate_percent,
        energy_wh=bill_record.energy_wh,
        amount_before_vat=bill_record.amount_before_vat,
        vat_amount=bill_record.vat_amount,
        billed_at=bill_record.billed_at,
    )


async def find_session_bill_reference(
    db: AsyncSession, session_id: UUID
) -> SessionBillReference | None:
    """Find the bill of a charging session, if one exists (CHG-03).

    Args:
        db: The async session owned by the entry boundary.
        session_id: The charging session.

    Returns:
        The bill reference, or ``None`` until WP9 creates bills at the scan.
    """
    bill_record = await billing_repository.find_bill_by_session_id(db, session_id)
    return None if bill_record is None else to_session_bill_reference(bill_record)
