"""Repository querying the identity tables; contains no business rules.

Only the read the drivers domain needs today (a membership with its person);
the rest of the identity repository comes with the identity service (WP2).
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import MembershipModel, UserModel


async def find_membership_with_user(
    db_session: AsyncSession, membership_id: UUID
) -> tuple[MembershipModel, UserModel] | None:
    """Find a membership together with its user.

    Args:
        db_session: Current database session.
        membership_id: Internal ID of the membership.

    Returns:
        The membership and its user, or `None` if the membership is unknown.
        A left membership and a soft-deleted user are returned too: the
        caller decides what their status means.
    """
    query_result = await db_session.execute(
        select(MembershipModel, UserModel)
        .join(UserModel, UserModel.user_id == MembershipModel.user_id)
        .where(MembershipModel.membership_id == membership_id)
    )
    row = query_result.one_or_none()
    return (row[0], row[1]) if row is not None else None
