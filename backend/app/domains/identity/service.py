"""Public service of the identity domain.

Only the lookups other domains already need are here; accounts, login and
roles come with WP2. Other domains call these functions and never import the
identity models or repository.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.repository as identity_repository
from app.domains.identity.types import MembershipPersonReference


async def resolve_membership_person_reference(
    db_session: AsyncSession,
    membership_id: UUID,
) -> MembershipPersonReference | None:
    """Find a membership and the person behind it, as an internal DTO.

    Args:
        db_session: Database session owned by the entry boundary.
        membership_id: Internal ID of the membership.

    Returns:
        `MembershipPersonReference`, or `None` if the membership is unknown.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    found = await identity_repository.find_membership_with_user(
        db_session, membership_id
    )
    if found is None:
        return None
    membership_record, user_record = found
    return MembershipPersonReference(
        membership_id=membership_record.membership_id,
        organization_id=membership_record.organization_id,
        user_id=user_record.user_id,
        full_name=user_record.full_name,
        phone_number=user_record.phone_number,
        membership_status=membership_record.status,
        user_status=user_record.status,
        left_at=membership_record.left_at,
    )
