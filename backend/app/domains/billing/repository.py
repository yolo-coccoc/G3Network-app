"""Database queries of the billing domain.

Only what the QR start and the receipt read today (WP8); the tariff, payment
and ledger queries come with WP9. No business rule and no commit.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.billing.models import ChargingSessionBillModel, WalletModel


async def find_wallet_by_user_id(db: AsyncSession, user_id: UUID) -> WalletModel | None:
    """Find the wallet of a person (one per person, BL-13).

    Args:
        db: The current async session.
        user_id: The person holding the wallet.

    Returns:
        The wallet, or ``None`` when the person has none yet.
    """
    query_result = await db.execute(
        select(WalletModel).where(WalletModel.user_id == user_id)
    )
    return query_result.scalar_one_or_none()


async def find_bill_by_session_id(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionBillModel | None:
    """Find the bill of a charging session (one bill per session, BL-10).

    Args:
        db: The current async session.
        session_id: The charging session.

    Returns:
        The bill, or ``None`` when none was created.
    """
    query_result = await db.execute(
        select(ChargingSessionBillModel).where(
            ChargingSessionBillModel.session_id == session_id
        )
    )
    return query_result.scalar_one_or_none()
