"""Repository querying the identity tables; contains no business rules.

Every function takes the session of the entry boundary, never commits or rolls
back, and flushes only to learn a generated value or a constraint error. A
change to a tracked row (`organizations`, `users`, `memberships`,
`organization_settings`) is made by the service, which sets the change context
(`app.libs.db.history.set_change_context`) first; the `update_*` helpers here
only apply the new values and flush, so the history trigger fires inside that
context.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.identity.models import (
    AccessAuditLogModel,
    LegalDocumentModel,
    MembershipModel,
    OneTimeCodeModel,
    OrganizationModel,
    OrganizationSettingModel,
    UserConsentModel,
    UserCredentialModel,
    UserModel,
    UserRoleAssignmentModel,
    UserSessionModel,
    UserStateModel,
)
from app.domains.identity.types import (
    MembershipStatus,
    OneTimeCodePurpose,
    OrganizationStatus,
    UserRole,
    UserStatus,
)

# ---------------------------------------------------------------------------
# Organizations and their settings
# ---------------------------------------------------------------------------


async def insert_organization(
    db_session: AsyncSession, values: dict[str, Any]
) -> OrganizationModel:
    """Insert an organization.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Column values of the new row.

    Returns:
        The inserted organization.
    """
    organization_record = OrganizationModel(**values)
    db_session.add(organization_record)
    await db_session.flush()
    return organization_record


async def get_organization(
    db_session: AsyncSession, organization_id: UUID
) -> OrganizationModel | None:
    """Find an organization by ID, closed (soft-deleted) ones included.

    Args:
        db_session: Current database session.
        organization_id: Internal ID of the organization.

    Returns:
        The organization, or `None` if unknown.
    """
    return await db_session.get(OrganizationModel, organization_id)


async def find_live_organization_by_tax_code(
    db_session: AsyncSession, tax_code: str
) -> OrganizationModel | None:
    """Find the live organization that uses a tax code.

    Args:
        db_session: Current database session.
        tax_code: Tax code to look for.

    Returns:
        The organization not soft-deleted with that tax code, or `None`.
    """
    query_result = await db_session.execute(
        select(OrganizationModel).where(
            OrganizationModel.tax_code == tax_code,
            OrganizationModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


def _organization_conditions(
    *,
    search_text: str | None,
    status: str | None,
    only_organization_id: UUID | None,
) -> list[ColumnElement[bool]]:
    """Build the filters shared by the organization list and its count."""
    conditions: list[ColumnElement[bool]] = []
    if search_text:
        pattern = f"%{search_text}%"
        conditions.append(
            or_(
                OrganizationModel.display_name.ilike(pattern),
                OrganizationModel.legal_name.ilike(pattern),
                OrganizationModel.tax_code.ilike(pattern),
            )
        )
    if status is not None:
        conditions.append(OrganizationModel.status == status)
    if only_organization_id is not None:
        conditions.append(OrganizationModel.organization_id == only_organization_id)
    return conditions


async def list_organizations(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    search_text: str | None,
    status: str | None,
    only_organization_id: UUID | None,
) -> list[OrganizationModel]:
    """List organizations, newest first.

    Args:
        db_session: Current database session.
        offset: Rows to skip.
        limit: Maximum rows.
        search_text: Substring of a name or the tax code, if any.
        status: Only this `OrganizationStatus` value, if given.
        only_organization_id: Restrict to this one organization (data reach of
            a non-internal caller), if given.

    Returns:
        The matching organizations, closed ones included.
    """
    conditions = _organization_conditions(
        search_text=search_text,
        status=status,
        only_organization_id=only_organization_id,
    )
    query_result = await db_session.execute(
        select(OrganizationModel)
        .where(and_(*conditions))
        .order_by(OrganizationModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_organizations(
    db_session: AsyncSession,
    *,
    search_text: str | None,
    status: str | None,
    only_organization_id: UUID | None,
) -> int:
    """Count the organizations `list_organizations` would match.

    Args:
        db_session: Current database session.
        search_text: Substring of a name or the tax code, if any.
        status: Only this `OrganizationStatus` value, if given.
        only_organization_id: Restrict to this one organization, if given.

    Returns:
        The total number of matching rows.
    """
    conditions = _organization_conditions(
        search_text=search_text,
        status=status,
        only_organization_id=only_organization_id,
    )
    query_result = await db_session.execute(
        select(func.count(OrganizationModel.organization_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def apply_organization_values(
    db_session: AsyncSession,
    organization_record: OrganizationModel,
    values: dict[str, Any],
) -> OrganizationModel:
    """Apply new column values to an organization and flush.

    The caller sets the change context first (tracked table).

    Args:
        db_session: Current database session.
        organization_record: The row to change.
        values: Column names and their new values.

    Returns:
        The same row, flushed.
    """
    for column_name, value in values.items():
        setattr(organization_record, column_name, value)
    await db_session.flush()
    return organization_record


async def insert_organization_settings(
    db_session: AsyncSession, organization_id: UUID
) -> OrganizationSettingModel:
    """Create the default settings row of a new organization (ID-45).

    Args:
        db_session: Database session owned by the entry boundary.
        organization_id: The organization that was just created.

    Returns:
        The settings row with its default values.
    """
    settings_record = OrganizationSettingModel(
        organization_id=organization_id,
        telemetry_interval_seconds=10,
        driving_session_auto_end_minutes=120,
    )
    db_session.add(settings_record)
    await db_session.flush()
    return settings_record


async def get_organization_settings(
    db_session: AsyncSession, organization_id: UUID
) -> OrganizationSettingModel | None:
    """Find the settings row of an organization.

    Args:
        db_session: Current database session.
        organization_id: The organization.

    Returns:
        The settings row, or `None` if the organization has none.
    """
    return await db_session.get(OrganizationSettingModel, organization_id)


async def apply_organization_settings_values(
    db_session: AsyncSession,
    settings_record: OrganizationSettingModel,
    values: dict[str, Any],
) -> OrganizationSettingModel:
    """Apply new setting values and flush (tracked table: context set first).

    Args:
        db_session: Current database session.
        settings_record: The row to change.
        values: Column names and their new values.

    Returns:
        The same row, flushed.
    """
    for column_name, value in values.items():
        setattr(settings_record, column_name, value)
    await db_session.flush()
    return settings_record


# ---------------------------------------------------------------------------
# Users, their state and credentials
# ---------------------------------------------------------------------------


async def insert_user(db_session: AsyncSession, values: dict[str, Any]) -> UserModel:
    """Insert a user together with its `user_state` row (ID-18).

    Args:
        db_session: Database session owned by the entry boundary.
        values: Column values of the new user.

    Returns:
        The inserted user.
    """
    user_record = UserModel(**values)
    db_session.add(user_record)
    await db_session.flush()
    db_session.add(UserStateModel(user_id=user_record.user_id, failed_login_count=0))
    await db_session.flush()
    return user_record


async def get_user(db_session: AsyncSession, user_id: UUID) -> UserModel | None:
    """Find a user by ID, soft-deleted accounts included.

    Args:
        db_session: Current database session.
        user_id: Internal ID of the user.

    Returns:
        The user, or `None` if unknown.
    """
    return await db_session.get(UserModel, user_id)


async def find_live_user_by_phone(
    db_session: AsyncSession, phone_number: str
) -> UserModel | None:
    """Find the live account that owns a phone number.

    Args:
        db_session: Current database session.
        phone_number: E.164 phone number.

    Returns:
        The user not soft-deleted with that number, or `None`.
    """
    query_result = await db_session.execute(
        select(UserModel).where(
            UserModel.phone_number == phone_number, UserModel.deleted_at.is_(None)
        )
    )
    return query_result.scalar_one_or_none()


async def find_live_user_by_email(
    db_session: AsyncSession, email: str
) -> UserModel | None:
    """Find the live account that owns an e-mail address, ignoring case.

    Args:
        db_session: Current database session.
        email: The address.

    Returns:
        The user not soft-deleted with that address, or `None`.
    """
    query_result = await db_session.execute(
        select(UserModel).where(
            func.lower(UserModel.email) == email.lower(),
            UserModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


def _user_conditions(
    *, search_text: str | None, status: str | None
) -> list[ColumnElement[bool]]:
    """Build the filters shared by the user list and its count."""
    conditions: list[ColumnElement[bool]] = [UserModel.deleted_at.is_(None)]
    if search_text:
        pattern = f"%{search_text}%"
        conditions.append(
            or_(
                UserModel.full_name.ilike(pattern),
                UserModel.phone_number.ilike(pattern),
                UserModel.email.ilike(pattern),
            )
        )
    if status is not None:
        conditions.append(UserModel.status == status)
    return conditions


async def list_users(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    search_text: str | None,
    status: str | None,
) -> list[UserModel]:
    """List live user accounts, newest first.

    Args:
        db_session: Current database session.
        offset: Rows to skip.
        limit: Maximum rows.
        search_text: Substring of name, phone number or e-mail, if any.
        status: Only this `UserStatus` value, if given.

    Returns:
        The matching users.
    """
    query_result = await db_session.execute(
        select(UserModel)
        .where(and_(*_user_conditions(search_text=search_text, status=status)))
        .order_by(UserModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_users(
    db_session: AsyncSession, *, search_text: str | None, status: str | None
) -> int:
    """Count the users `list_users` would match.

    Args:
        db_session: Current database session.
        search_text: Substring of name, phone number or e-mail, if any.
        status: Only this `UserStatus` value, if given.

    Returns:
        The total number of matching rows.
    """
    query_result = await db_session.execute(
        select(func.count(UserModel.user_id)).where(
            and_(*_user_conditions(search_text=search_text, status=status))
        )
    )
    return query_result.scalar() or 0


async def apply_user_values(
    db_session: AsyncSession, user_record: UserModel, values: dict[str, Any]
) -> UserModel:
    """Apply new column values to a user and flush (tracked table).

    Args:
        db_session: Current database session.
        user_record: The row to change.
        values: Column names and their new values.

    Returns:
        The same row, flushed.
    """
    for column_name, value in values.items():
        setattr(user_record, column_name, value)
    await db_session.flush()
    return user_record


async def get_user_state_for_update(
    db_session: AsyncSession, user_id: UUID
) -> UserStateModel | None:
    """Read a user's state row and lock it until the transaction ends.

    The lock serializes concurrent logins of one account so the failed-attempt
    counter cannot lose an increment.

    Args:
        db_session: Current database session.
        user_id: The user.

    Returns:
        The state row, or `None` if the user has none.
    """
    query_result = await db_session.execute(
        select(UserStateModel)
        .where(UserStateModel.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return query_result.scalar_one_or_none()


async def get_user_state(
    db_session: AsyncSession, user_id: UUID
) -> UserStateModel | None:
    """Read a user's state row.

    Args:
        db_session: Current database session.
        user_id: The user.

    Returns:
        The state row, or `None` if the user has none.
    """
    return await db_session.get(UserStateModel, user_id)


async def find_active_credential(
    db_session: AsyncSession, user_id: UUID
) -> UserCredentialModel | None:
    """Find a user's current password credential.

    Args:
        db_session: Current database session.
        user_id: The user.

    Returns:
        The credential that is not revoked, or `None` (an invited user who has
        not chosen a password yet).
    """
    query_result = await db_session.execute(
        select(UserCredentialModel).where(
            UserCredentialModel.user_id == user_id,
            UserCredentialModel.credential_type == "PASSWORD",
            UserCredentialModel.revoked_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def list_recent_password_hashes(
    db_session: AsyncSession, user_id: UUID, limit: int
) -> list[str]:
    """List a user's newest password hashes, current one included.

    Args:
        db_session: Current database session.
        user_id: The user.
        limit: How many hashes to return.

    Returns:
        Hashes of the latest credentials, newest first (for the reuse check).
    """
    query_result = await db_session.execute(
        select(UserCredentialModel.secret_hash)
        .where(UserCredentialModel.user_id == user_id)
        .order_by(UserCredentialModel.created_at.desc())
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def insert_credential(
    db_session: AsyncSession, *, user_id: UUID, secret_hash: str
) -> UserCredentialModel:
    """Insert a new password credential.

    Args:
        db_session: Database session owned by the entry boundary.
        user_id: The user.
        secret_hash: Hash from `security.hash_password`.

    Returns:
        The inserted credential.
    """
    credential_record = UserCredentialModel(
        user_id=user_id, credential_type="PASSWORD", secret_hash=secret_hash
    )
    db_session.add(credential_record)
    await db_session.flush()
    return credential_record


async def revoke_active_credential(
    db_session: AsyncSession, user_id: UUID, revoked_at: datetime
) -> None:
    """Revoke a user's current password credential.

    Args:
        db_session: Current database session.
        user_id: The user.
        revoked_at: Revocation time.
    """
    await db_session.execute(
        update(UserCredentialModel)
        .where(
            UserCredentialModel.user_id == user_id,
            UserCredentialModel.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at)
    )
    await db_session.flush()


async def prune_revoked_credentials(
    db_session: AsyncSession, user_id: UUID, keep: int
) -> None:
    """Delete all but the newest revoked credentials of a user (ID-31).

    Args:
        db_session: Current database session.
        user_id: The user.
        keep: How many revoked credentials to keep.
    """
    keep_ids = (
        select(UserCredentialModel.user_credential_id)
        .where(
            UserCredentialModel.user_id == user_id,
            UserCredentialModel.revoked_at.is_not(None),
        )
        .order_by(
            UserCredentialModel.revoked_at.desc(), UserCredentialModel.created_at.desc()
        )
        .limit(keep)
    )
    await db_session.execute(
        delete(UserCredentialModel).where(
            UserCredentialModel.user_id == user_id,
            UserCredentialModel.revoked_at.is_not(None),
            UserCredentialModel.user_credential_id.not_in(keep_ids),
        )
    )


# ---------------------------------------------------------------------------
# Login sessions
# ---------------------------------------------------------------------------


async def insert_session(
    db_session: AsyncSession, values: dict[str, Any]
) -> UserSessionModel:
    """Insert a login session.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Column values of the new row.

    Returns:
        The inserted session.
    """
    session_record = UserSessionModel(**values)
    db_session.add(session_record)
    await db_session.flush()
    return session_record


async def get_session(
    db_session: AsyncSession, session_id: UUID
) -> UserSessionModel | None:
    """Find a login session by ID.

    Args:
        db_session: Current database session.
        session_id: Internal ID of the session.

    Returns:
        The session, or `None` if it ended.
    """
    return await db_session.get(UserSessionModel, session_id)


async def find_session_by_refresh_hash(
    db_session: AsyncSession, refresh_token_hash: str
) -> UserSessionModel | None:
    """Find the session a refresh token belongs to, locking the row.

    Args:
        db_session: Current database session.
        refresh_token_hash: Hash from `security.hash_refresh_token`.

    Returns:
        The session, or `None` if the token is unknown or was replaced.
    """
    query_result = await db_session.execute(
        select(UserSessionModel)
        .where(UserSessionModel.refresh_token_hash == refresh_token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return query_result.scalar_one_or_none()


async def list_sessions_by_user(
    db_session: AsyncSession, user_id: UUID
) -> list[UserSessionModel]:
    """List a person's live sessions, most recently used first.

    Args:
        db_session: Current database session.
        user_id: The user.

    Returns:
        Sessions that are not expired.
    """
    query_result = await db_session.execute(
        select(UserSessionModel)
        .where(UserSessionModel.user_id == user_id)
        .order_by(UserSessionModel.last_used_at.desc())
    )
    return list(query_result.scalars().all())


async def delete_session(
    db_session: AsyncSession, session_record: UserSessionModel
) -> None:
    """End one session by deleting its row.

    Args:
        db_session: Current database session.
        session_record: The session to end.
    """
    await db_session.delete(session_record)
    await db_session.flush()


async def delete_sessions_by_user(
    db_session: AsyncSession, user_id: UUID, *, except_session_id: UUID | None = None
) -> None:
    """End every session of a user, optionally sparing one.

    Args:
        db_session: Current database session.
        user_id: The user.
        except_session_id: A session to keep (the one making the request).
    """
    condition = UserSessionModel.user_id == user_id
    if except_session_id is not None:
        condition = and_(
            condition, UserSessionModel.user_session_id != except_session_id
        )
    await db_session.execute(delete(UserSessionModel).where(condition))


async def clear_session_organization(
    db_session: AsyncSession, *, user_id: UUID | None, organization_id: UUID
) -> None:
    """Make sessions stop showing an organization they can no longer act for.

    The sessions stay alive (the person may belong to other organizations) but
    their organization becomes unset, so the next request must pick one.

    Args:
        db_session: Current database session.
        user_id: Only this user's sessions, or every user's when `None`.
        organization_id: The organization to take away.
    """
    condition = UserSessionModel.organization_id == organization_id
    if user_id is not None:
        condition = and_(condition, UserSessionModel.user_id == user_id)
    await db_session.execute(
        update(UserSessionModel).where(condition).values(organization_id=None)
    )


async def clear_push_token(db_session: AsyncSession, push_token: str) -> None:
    """Remove a push token from whichever session holds it.

    A phone used by two people must send the previous person's messages to
    nobody; the token is unique, so the old holder lets go first.

    Args:
        db_session: Current database session.
        push_token: The Firebase token.
    """
    await db_session.execute(
        update(UserSessionModel)
        .where(UserSessionModel.push_token == push_token)
        .values(push_token=None)
    )
    await db_session.flush()


async def delete_expired_sessions(db_session: AsyncSession, now: datetime) -> None:
    """Delete every session whose lifetime ran out.

    Args:
        db_session: Current database session.
        now: The current time.
    """
    await db_session.execute(
        delete(UserSessionModel).where(UserSessionModel.expires_at <= now)
    )


# ---------------------------------------------------------------------------
# One-time codes
# ---------------------------------------------------------------------------


async def insert_one_time_code(
    db_session: AsyncSession, values: dict[str, Any]
) -> OneTimeCodeModel:
    """Insert a one-time code row.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Column values of the new row.

    Returns:
        The inserted row.
    """
    code_record = OneTimeCodeModel(**values)
    db_session.add(code_record)
    await db_session.flush()
    return code_record


async def find_latest_one_time_code(
    db_session: AsyncSession,
    *,
    phone_number: str,
    purpose: str,
    user_id: UUID | None = None,
) -> OneTimeCodeModel | None:
    """Find the newest code for a phone number and purpose, locking the row.

    Only the newest code of a phone and purpose is ever checked (ID-37).

    Args:
        db_session: Current database session.
        phone_number: Phone number the code was sent to.
        purpose: `OneTimeCodePurpose` value.
        user_id: Only a code issued for this user, if given.

    Returns:
        The newest row (used or expired ones included), or `None`.
    """
    conditions = [
        OneTimeCodeModel.phone_number == phone_number,
        OneTimeCodeModel.purpose == purpose,
    ]
    if user_id is not None:
        conditions.append(OneTimeCodeModel.user_id == user_id)
    query_result = await db_session.execute(
        select(OneTimeCodeModel)
        .where(and_(*conditions))
        .order_by(OneTimeCodeModel.created_at.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return query_result.scalar_one_or_none()


async def find_latest_one_time_code_for_user(
    db_session: AsyncSession, *, user_id: UUID, purpose: str
) -> OneTimeCodeModel | None:
    """Find the newest code issued for a user and purpose, locking the row.

    Used where the phone number is not known to the caller (a phone-number
    change: the code went to the new number).

    Args:
        db_session: Current database session.
        user_id: The user the code was issued for.
        purpose: `OneTimeCodePurpose` value.

    Returns:
        The newest row (used or expired ones included), or `None`.
    """
    query_result = await db_session.execute(
        select(OneTimeCodeModel)
        .where(OneTimeCodeModel.user_id == user_id, OneTimeCodeModel.purpose == purpose)
        .order_by(OneTimeCodeModel.created_at.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return query_result.scalar_one_or_none()


async def find_latest_one_time_code_time(
    db_session: AsyncSession, *, phone_number: str, purpose: str
) -> datetime | None:
    """Find when the last code of a phone number and purpose was sent.

    Args:
        db_session: Current database session.
        phone_number: Phone number.
        purpose: `OneTimeCodePurpose` value.

    Returns:
        The creation time of the newest code, or `None` if none was sent.
    """
    query_result = await db_session.execute(
        select(func.max(OneTimeCodeModel.created_at)).where(
            OneTimeCodeModel.phone_number == phone_number,
            OneTimeCodeModel.purpose == purpose,
        )
    )
    return query_result.scalar()


async def count_one_time_codes_since(
    db_session: AsyncSession, phone_number: str, since: datetime
) -> int:
    """Count the codes sent to a phone number since a time (daily limit).

    Args:
        db_session: Current database session.
        phone_number: Phone number.
        since: Start of the window.

    Returns:
        The number of codes created at or after `since`.
    """
    query_result = await db_session.execute(
        select(func.count(OneTimeCodeModel.one_time_code_id)).where(
            OneTimeCodeModel.phone_number == phone_number,
            OneTimeCodeModel.created_at >= since,
        )
    )
    return query_result.scalar() or 0


async def count_invitation_codes_by_issuer_since(
    db_session: AsyncSession, issued_by: UUID, since: datetime
) -> int:
    """Count the invitation SMS one person triggered since a time (inviter quota).

    Args:
        db_session: Current database session.
        issued_by: The person who invited.
        since: Start of the window.

    Returns:
        The number of `INVITE` and `INVITE_NOTICE` rows they created at or
        after `since`.
    """
    query_result = await db_session.execute(
        select(func.count(OneTimeCodeModel.one_time_code_id)).where(
            OneTimeCodeModel.issued_by == issued_by,
            OneTimeCodeModel.purpose.in_(
                (
                    OneTimeCodePurpose.INVITE.value,
                    OneTimeCodePurpose.INVITE_NOTICE.value,
                )
            ),
            OneTimeCodeModel.created_at >= since,
        )
    )
    return query_result.scalar() or 0


async def delete_stale_one_time_codes(
    db_session: AsyncSession, expired_before: datetime
) -> None:
    """Delete codes that expired long ago (ID-37: one day after expiry).

    Args:
        db_session: Current database session.
        expired_before: Delete codes whose `expires_at` is earlier than this.
    """
    await db_session.execute(
        delete(OneTimeCodeModel).where(OneTimeCodeModel.expires_at < expired_before)
    )


# ---------------------------------------------------------------------------
# Memberships and roles
# ---------------------------------------------------------------------------


async def insert_membership(
    db_session: AsyncSession, values: dict[str, Any]
) -> MembershipModel:
    """Insert a membership.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Column values of the new row.

    Returns:
        The inserted membership.
    """
    membership_record = MembershipModel(**values)
    db_session.add(membership_record)
    await db_session.flush()
    return membership_record


async def get_membership(
    db_session: AsyncSession, membership_id: UUID
) -> MembershipModel | None:
    """Find a membership by ID, ended ones included.

    Args:
        db_session: Current database session.
        membership_id: Internal ID of the membership.

    Returns:
        The membership, or `None` if unknown.
    """
    return await db_session.get(MembershipModel, membership_id)


async def find_live_membership(
    db_session: AsyncSession, *, organization_id: UUID, user_id: UUID
) -> MembershipModel | None:
    """Find the membership of a person in an organization that has not ended.

    Args:
        db_session: Current database session.
        organization_id: The organization.
        user_id: The person.

    Returns:
        The membership with `left_at` unset, or `None`.
    """
    query_result = await db_session.execute(
        select(MembershipModel).where(
            MembershipModel.organization_id == organization_id,
            MembershipModel.user_id == user_id,
            MembershipModel.left_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def list_live_memberships_with_organizations(
    db_session: AsyncSession, user_id: UUID
) -> list[tuple[MembershipModel, OrganizationModel]]:
    """List a person's memberships that have not ended, with their organizations.

    Args:
        db_session: Current database session.
        user_id: The person.

    Returns:
        Pairs of membership and organization, oldest membership first;
        closed organizations are included (the caller decides).
    """
    query_result = await db_session.execute(
        select(MembershipModel, OrganizationModel)
        .join(
            OrganizationModel,
            OrganizationModel.organization_id == MembershipModel.organization_id,
        )
        .where(MembershipModel.user_id == user_id, MembershipModel.left_at.is_(None))
        .order_by(MembershipModel.created_at)
    )
    return [(row[0], row[1]) for row in query_result.all()]


async def list_active_membership_ids_by_organization(
    db_session: AsyncSession, organization_id: UUID
) -> list[UUID]:
    """List the memberships of an organization that are active now.

    Args:
        db_session: Current database session.
        organization_id: The organization.

    Returns:
        IDs of memberships in `ACTIVE` status that have not ended.
    """
    query_result = await db_session.execute(
        select(MembershipModel.membership_id).where(
            MembershipModel.organization_id == organization_id,
            MembershipModel.status == MembershipStatus.ACTIVE.value,
            MembershipModel.left_at.is_(None),
        )
    )
    return list(query_result.scalars().all())


async def list_invited_memberships_by_user(
    db_session: AsyncSession, user_id: UUID
) -> list[MembershipModel]:
    """List a person's pending invitations.

    Args:
        db_session: Current database session.
        user_id: The person.

    Returns:
        Memberships in `INVITED` status that have not ended.
    """
    query_result = await db_session.execute(
        select(MembershipModel).where(
            MembershipModel.user_id == user_id,
            MembershipModel.status == MembershipStatus.INVITED.value,
            MembershipModel.left_at.is_(None),
        )
    )
    return list(query_result.scalars().all())


def _member_conditions(
    organization_id: UUID, status: str | None
) -> list[ColumnElement[bool]]:
    """Build the filters shared by the member list and its count."""
    conditions: list[ColumnElement[bool]] = [
        MembershipModel.organization_id == organization_id,
        MembershipModel.left_at.is_(None),
    ]
    if status is not None:
        conditions.append(MembershipModel.status == status)
    return conditions


async def list_members(
    db_session: AsyncSession,
    *,
    organization_id: UUID,
    status: str | None,
    offset: int,
    limit: int,
) -> list[tuple[MembershipModel, UserModel]]:
    """List the current members of an organization with their users.

    Args:
        db_session: Current database session.
        organization_id: The organization.
        status: Only this `MembershipStatus` value, if given.
        offset: Rows to skip.
        limit: Maximum rows.

    Returns:
        Pairs of membership and user, oldest first.
    """
    query_result = await db_session.execute(
        select(MembershipModel, UserModel)
        .join(UserModel, UserModel.user_id == MembershipModel.user_id)
        .where(and_(*_member_conditions(organization_id, status)))
        .order_by(MembershipModel.created_at)
        .offset(offset)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in query_result.all()]


async def count_members(
    db_session: AsyncSession, *, organization_id: UUID, status: str | None
) -> int:
    """Count the members `list_members` would match.

    Args:
        db_session: Current database session.
        organization_id: The organization.
        status: Only this `MembershipStatus` value, if given.

    Returns:
        The total number of matching members.
    """
    query_result = await db_session.execute(
        select(func.count(MembershipModel.membership_id)).where(
            and_(*_member_conditions(organization_id, status))
        )
    )
    return query_result.scalar() or 0


async def apply_membership_values(
    db_session: AsyncSession,
    membership_record: MembershipModel,
    values: dict[str, Any],
) -> MembershipModel:
    """Apply new column values to a membership and flush (tracked table).

    Args:
        db_session: Current database session.
        membership_record: The row to change.
        values: Column names and their new values.

    Returns:
        The same row, flushed.
    """
    for column_name, value in values.items():
        setattr(membership_record, column_name, value)
    await db_session.flush()
    return membership_record


async def insert_role_assignment(
    db_session: AsyncSession,
    *,
    membership_record: MembershipModel,
    role: str,
    granted_by: UUID | None,
) -> UserRoleAssignmentModel:
    """Grant a role to a membership.

    Args:
        db_session: Database session owned by the entry boundary.
        membership_record: The membership that receives the role; its
            organization is copied to the row (DM-24 exception).
        role: `UserRole` value.
        granted_by: Who grants it; `None` when the system does.

    Returns:
        The inserted assignment.
    """
    assignment_record = UserRoleAssignmentModel(
        organization_id=membership_record.organization_id,
        membership_id=membership_record.membership_id,
        role=role,
        granted_by=granted_by,
    )
    db_session.add(assignment_record)
    await db_session.flush()
    return assignment_record


async def list_active_roles_by_membership(
    db_session: AsyncSession, membership_id: UUID
) -> list[str]:
    """List the roles a membership holds now.

    Args:
        db_session: Current database session.
        membership_id: The membership.

    Returns:
        `UserRole` values of the assignments that are not revoked.
    """
    query_result = await db_session.execute(
        select(UserRoleAssignmentModel.role)
        .where(
            UserRoleAssignmentModel.membership_id == membership_id,
            UserRoleAssignmentModel.revoked_at.is_(None),
        )
        .order_by(UserRoleAssignmentModel.granted_at)
    )
    return list(query_result.scalars().all())


async def find_active_role_assignment(
    db_session: AsyncSession, *, membership_id: UUID, role: str
) -> UserRoleAssignmentModel | None:
    """Find the live assignment of one role to a membership.

    Args:
        db_session: Current database session.
        membership_id: The membership.
        role: `UserRole` value.

    Returns:
        The assignment that is not revoked, or `None`.
    """
    query_result = await db_session.execute(
        select(UserRoleAssignmentModel).where(
            UserRoleAssignmentModel.membership_id == membership_id,
            UserRoleAssignmentModel.role == role,
            UserRoleAssignmentModel.revoked_at.is_(None),
        )
    )
    return query_result.scalar_one_or_none()


async def find_active_org_admin_assignment(
    db_session: AsyncSession, organization_id: UUID
) -> UserRoleAssignmentModel | None:
    """Find the live ORG_ADMIN assignment of an organization.

    Args:
        db_session: Current database session.
        organization_id: The organization.

    Returns:
        The assignment, or `None` when the organization has no administrator.
    """
    query_result = await db_session.execute(
        select(UserRoleAssignmentModel)
        .where(
            UserRoleAssignmentModel.organization_id == organization_id,
            UserRoleAssignmentModel.role == UserRole.ORG_ADMIN.value,
            UserRoleAssignmentModel.revoked_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return query_result.scalar_one_or_none()


async def revoke_role_assignment(
    db_session: AsyncSession,
    assignment_record: UserRoleAssignmentModel,
    *,
    revoked_at: datetime,
    revoked_by: UUID | None,
) -> None:
    """Revoke one role assignment.

    Args:
        db_session: Current database session.
        assignment_record: The assignment to close.
        revoked_at: Revocation time.
        revoked_by: Who revokes it; `None` when the system does.
    """
    assignment_record.revoked_at = revoked_at
    assignment_record.revoked_by = revoked_by
    await db_session.flush()


async def revoke_all_roles_of_membership(
    db_session: AsyncSession,
    membership_id: UUID,
    *,
    revoked_at: datetime,
    revoked_by: UUID | None,
) -> None:
    """Revoke every live role of a membership (it ended or is being removed).

    Args:
        db_session: Current database session.
        membership_id: The membership.
        revoked_at: Revocation time.
        revoked_by: Who revokes them; `None` when the system does.
    """
    await db_session.execute(
        update(UserRoleAssignmentModel)
        .where(
            UserRoleAssignmentModel.membership_id == membership_id,
            UserRoleAssignmentModel.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at, revoked_by=revoked_by)
    )
    await db_session.flush()


async def count_active_role_holders(db_session: AsyncSession, role: str) -> int:
    """Count the live holders of a role who can actually use it (RV-ID7).

    A holder counts only when its membership is ACTIVE and not ended and the
    account is ACTIVE and not deleted: a locked or invited administrator
    cannot act, so the "last HEAD_ADMIN" guard must not count them.

    Args:
        db_session: Current database session.
        role: `UserRole` value.

    Returns:
        How many usable memberships hold the role right now.
    """
    query_result = await db_session.execute(
        select(func.count(UserRoleAssignmentModel.user_role_assignment_id))
        .join(
            MembershipModel,
            MembershipModel.membership_id == UserRoleAssignmentModel.membership_id,
        )
        .join(UserModel, UserModel.user_id == MembershipModel.user_id)
        .where(
            UserRoleAssignmentModel.role == role,
            UserRoleAssignmentModel.revoked_at.is_(None),
            MembershipModel.status == "ACTIVE",
            MembershipModel.left_at.is_(None),
            UserModel.status == "ACTIVE",
            UserModel.deleted_at.is_(None),
        )
    )
    return query_result.scalar() or 0


async def list_users_holding_role(
    db_session: AsyncSession, role: str
) -> list[UserModel]:
    """List the active users whose active membership holds a role.

    Args:
        db_session: Current database session.
        role: `UserRole` value.

    Returns:
        Users of memberships in ACTIVE status holding the role, not deleted.
    """
    query_result = await db_session.execute(
        select(UserModel)
        .join(MembershipModel, MembershipModel.user_id == UserModel.user_id)
        .join(
            UserRoleAssignmentModel,
            UserRoleAssignmentModel.membership_id == MembershipModel.membership_id,
        )
        .where(
            UserRoleAssignmentModel.role == role,
            UserRoleAssignmentModel.revoked_at.is_(None),
            MembershipModel.status == MembershipStatus.ACTIVE.value,
            MembershipModel.left_at.is_(None),
            UserModel.deleted_at.is_(None),
            UserModel.status == UserStatus.ACTIVE.value,
        )
        .distinct()
    )
    return list(query_result.scalars().all())


async def list_user_ids_holding_roles_in_organization(
    db_session: AsyncSession, organization_id: UUID, roles: list[str]
) -> list[UUID]:
    """List the active people holding one of the roles in one organization.

    Args:
        db_session: Current database session.
        organization_id: The organization whose members are searched.
        roles: `UserRole` values; a person holding any of them is returned.

    Returns:
        User IDs of ACTIVE, not-left memberships of the organization that hold
        one of the roles right now, for accounts that are active and not
        deleted; each person once.
    """
    query_result = await db_session.execute(
        select(UserModel.user_id)
        .join(MembershipModel, MembershipModel.user_id == UserModel.user_id)
        .join(
            UserRoleAssignmentModel,
            UserRoleAssignmentModel.membership_id == MembershipModel.membership_id,
        )
        .where(
            MembershipModel.organization_id == organization_id,
            UserRoleAssignmentModel.role.in_(roles),
            UserRoleAssignmentModel.revoked_at.is_(None),
            MembershipModel.status == MembershipStatus.ACTIVE.value,
            MembershipModel.left_at.is_(None),
            UserModel.deleted_at.is_(None),
            UserModel.status == UserStatus.ACTIVE.value,
        )
        .distinct()
    )
    return list(query_result.scalars().all())


async def list_role_holders_in_organization(
    db_session: AsyncSession, organization_id: UUID, roles: list[str]
) -> list[tuple[UUID, UUID]]:
    """List the active people holding one of the roles in one organization.

    Args:
        db_session: Current database session.
        organization_id: The organization whose members are searched.
        roles: `UserRole` values; a person holding any of them is returned.

    Returns:
        One ``(user_id, membership_id)`` pair per qualifying active
        membership (ACTIVE, not left, account active and not deleted).
    """
    query_result = await db_session.execute(
        select(UserModel.user_id, MembershipModel.membership_id)
        .join(MembershipModel, MembershipModel.user_id == UserModel.user_id)
        .join(
            UserRoleAssignmentModel,
            UserRoleAssignmentModel.membership_id == MembershipModel.membership_id,
        )
        .where(
            MembershipModel.organization_id == organization_id,
            UserRoleAssignmentModel.role.in_(roles),
            UserRoleAssignmentModel.revoked_at.is_(None),
            MembershipModel.status == MembershipStatus.ACTIVE.value,
            MembershipModel.left_at.is_(None),
            UserModel.deleted_at.is_(None),
            UserModel.status == UserStatus.ACTIVE.value,
        )
        .distinct()
    )
    return [(row.user_id, row.membership_id) for row in query_result.all()]


async def list_roles_by_membership_id(
    db_session: AsyncSession, membership_id: UUID
) -> list[str]:
    """List the roles a membership holds right now.

    Args:
        db_session: Current database session.
        membership_id: Internal ID of the membership.

    Returns:
        `UserRole` values of its open role assignments.
    """
    query_result = await db_session.execute(
        select(UserRoleAssignmentModel.role).where(
            UserRoleAssignmentModel.membership_id == membership_id,
            UserRoleAssignmentModel.revoked_at.is_(None),
        )
    )
    return list(query_result.scalars().all())


async def list_user_ids_holding_roles_in_internal_organizations(
    db_session: AsyncSession, roles: list[str]
) -> list[UUID]:
    """List the active people of our own organizations holding one of the roles.

    Args:
        db_session: Current database session.
        roles: `UserRole` values; a person holding any of them is returned.

    Returns:
        User IDs of ACTIVE, not-left memberships in organizations flagged
        ``is_internal`` that hold a role right now; each person once.
    """
    query_result = await db_session.execute(
        select(UserModel.user_id)
        .join(MembershipModel, MembershipModel.user_id == UserModel.user_id)
        .join(
            OrganizationModel,
            OrganizationModel.organization_id == MembershipModel.organization_id,
        )
        .join(
            UserRoleAssignmentModel,
            UserRoleAssignmentModel.membership_id == MembershipModel.membership_id,
        )
        .where(
            OrganizationModel.is_internal.is_(True),
            OrganizationModel.deleted_at.is_(None),
            UserRoleAssignmentModel.role.in_(roles),
            UserRoleAssignmentModel.revoked_at.is_(None),
            MembershipModel.status == MembershipStatus.ACTIVE.value,
            MembershipModel.left_at.is_(None),
            UserModel.deleted_at.is_(None),
            UserModel.status == UserStatus.ACTIVE.value,
        )
        .distinct()
    )
    return list(query_result.scalars().all())


async def list_active_push_targets(
    db_session: AsyncSession, user_ids: list[UUID], now: datetime
) -> list[tuple[UUID, str]]:
    """List the push tokens of the people's unexpired login sessions.

    Args:
        db_session: Current database session.
        user_ids: The people.
        now: Sessions that expire at or before this time are ignored.

    Returns:
        ``(user_id, push_token)`` per session that registered a token.
    """
    if not user_ids:
        return []
    query_result = await db_session.execute(
        select(UserSessionModel.user_id, UserSessionModel.push_token).where(
            UserSessionModel.user_id.in_(user_ids),
            UserSessionModel.push_token.is_not(None),
            UserSessionModel.expires_at > now,
        )
    )
    return [
        (row.user_id, row.push_token)
        for row in query_result.all()
        if row.push_token is not None
    ]


async def list_email_targets(
    db_session: AsyncSession, user_ids: list[UUID]
) -> list[tuple[UUID, str, str]]:
    """List the e-mail addresses on file of active people.

    Args:
        db_session: Current database session.
        user_ids: The people.

    Returns:
        ``(user_id, email, full_name)`` for each active, not-deleted account
        with an address.
    """
    if not user_ids:
        return []
    query_result = await db_session.execute(
        select(UserModel.user_id, UserModel.email, UserModel.full_name).where(
            UserModel.user_id.in_(user_ids),
            UserModel.email.is_not(None),
            UserModel.deleted_at.is_(None),
            UserModel.status == UserStatus.ACTIVE.value,
        )
    )
    return [
        (row.user_id, row.email, row.full_name)
        for row in query_result.all()
        if row.email is not None
    ]


async def is_active_internal_sales_user(
    db_session: AsyncSession, user_id: UUID
) -> bool:
    """Tell whether a user is an active SALES member of an internal organization.

    Args:
        db_session: Current database session.
        user_id: The candidate account manager (ID-07).

    Returns:
        `True` if the user is active, holds the SALES role through an active
        membership of a live internal organization.
    """
    query_result = await db_session.execute(
        select(func.count(UserRoleAssignmentModel.user_role_assignment_id))
        .join(
            MembershipModel,
            MembershipModel.membership_id == UserRoleAssignmentModel.membership_id,
        )
        .join(
            OrganizationModel,
            OrganizationModel.organization_id == MembershipModel.organization_id,
        )
        .join(UserModel, UserModel.user_id == MembershipModel.user_id)
        .where(
            MembershipModel.user_id == user_id,
            MembershipModel.status == MembershipStatus.ACTIVE.value,
            MembershipModel.left_at.is_(None),
            OrganizationModel.is_internal.is_(True),
            OrganizationModel.status == OrganizationStatus.ACTIVE.value,
            UserModel.status == UserStatus.ACTIVE.value,
            UserModel.deleted_at.is_(None),
            UserRoleAssignmentModel.role == UserRole.SALES.value,
            UserRoleAssignmentModel.revoked_at.is_(None),
        )
    )
    return (query_result.scalar() or 0) > 0


async def find_internal_organization_by_name(
    db_session: AsyncSession, display_name: str
) -> OrganizationModel | None:
    """Find a live internal organization by display name (bootstrap re-runs).

    Args:
        db_session: Current database session.
        display_name: Exact display name.

    Returns:
        The organization, or `None`.
    """
    query_result = await db_session.execute(
        select(OrganizationModel).where(
            OrganizationModel.is_internal.is_(True),
            OrganizationModel.display_name == display_name,
            OrganizationModel.deleted_at.is_(None),
        )
    )
    return query_result.scalars().first()


# ---------------------------------------------------------------------------
# Legal documents and consents
# ---------------------------------------------------------------------------


async def insert_legal_document(
    db_session: AsyncSession, values: dict[str, Any]
) -> LegalDocumentModel:
    """Publish a legal document version.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Column values of the new row.

    Returns:
        The inserted document.
    """
    document_record = LegalDocumentModel(**values)
    db_session.add(document_record)
    await db_session.flush()
    return document_record


async def get_legal_document(
    db_session: AsyncSession, legal_document_id: UUID
) -> LegalDocumentModel | None:
    """Find a legal document by ID.

    Args:
        db_session: Current database session.
        legal_document_id: Internal ID of the document.

    Returns:
        The document, or `None` if unknown.
    """
    return await db_session.get(LegalDocumentModel, legal_document_id)


async def find_legal_document_by_version(
    db_session: AsyncSession, *, purpose: str, version: str
) -> LegalDocumentModel | None:
    """Find a legal document by purpose and version label.

    Args:
        db_session: Current database session.
        purpose: `LegalDocumentPurpose` value.
        version: Version label.

    Returns:
        The document, or `None`.
    """
    query_result = await db_session.execute(
        select(LegalDocumentModel).where(
            LegalDocumentModel.purpose == purpose,
            LegalDocumentModel.version == version,
        )
    )
    return query_result.scalar_one_or_none()


async def list_legal_documents(
    db_session: AsyncSession, purpose: str | None
) -> list[LegalDocumentModel]:
    """List legal document versions, newest first.

    Args:
        db_session: Current database session.
        purpose: Only this `LegalDocumentPurpose` value, if given.

    Returns:
        The matching documents.
    """
    statement = select(LegalDocumentModel).order_by(
        LegalDocumentModel.created_at.desc()
    )
    if purpose is not None:
        statement = statement.where(LegalDocumentModel.purpose == purpose)
    query_result = await db_session.execute(statement)
    return list(query_result.scalars().all())


async def find_current_legal_document(
    db_session: AsyncSession, purpose: str
) -> LegalDocumentModel | None:
    """Find the newest published version of a purpose (the current one).

    Args:
        db_session: Current database session.
        purpose: `LegalDocumentPurpose` value.

    Returns:
        The newest document, or `None` if nothing was published.
    """
    query_result = await db_session.execute(
        select(LegalDocumentModel)
        .where(LegalDocumentModel.purpose == purpose)
        .order_by(LegalDocumentModel.created_at.desc())
        .limit(1)
    )
    return query_result.scalar_one_or_none()


async def find_consent(
    db_session: AsyncSession,
    *,
    user_id: UUID | None,
    legal_document_id: UUID,
    organization_id: UUID | None,
) -> UserConsentModel | None:
    """Find an acceptance of a document by a person or on behalf of an organization.

    Args:
        db_session: Current database session.
        user_id: The person; required for a personal acceptance, ignored when
            `organization_id` is given.
        legal_document_id: The document version.
        organization_id: The organization accepted for, or `None` for the
            person's own acceptance.

    Returns:
        The consent row, or `None`.
    """
    conditions = [UserConsentModel.legal_document_id == legal_document_id]
    if organization_id is None:
        if user_id is None:
            return None
        conditions.append(UserConsentModel.user_id == user_id)
        conditions.append(UserConsentModel.organization_id.is_(None))
    else:
        conditions.append(UserConsentModel.organization_id == organization_id)
    query_result = await db_session.execute(
        select(UserConsentModel).where(and_(*conditions))
    )
    return query_result.scalar_one_or_none()


async def insert_consent(
    db_session: AsyncSession, values: dict[str, Any]
) -> UserConsentModel:
    """Record an acceptance (append-only).

    Args:
        db_session: Database session owned by the entry boundary.
        values: Column values of the new row.

    Returns:
        The inserted consent.
    """
    consent_record = UserConsentModel(**values)
    db_session.add(consent_record)
    await db_session.flush()
    return consent_record


async def list_consents_by_user(
    db_session: AsyncSession, user_id: UUID
) -> list[UserConsentModel]:
    """List what a person accepted, plus what they accepted for organizations.

    Args:
        db_session: Current database session.
        user_id: The person.

    Returns:
        The person's consent rows, newest first.
    """
    query_result = await db_session.execute(
        select(UserConsentModel)
        .where(UserConsentModel.user_id == user_id)
        .order_by(UserConsentModel.accepted_at.desc())
    )
    return list(query_result.scalars().all())


async def organization_has_consent(
    db_session: AsyncSession, *, organization_id: UUID, legal_document_id: UUID
) -> bool:
    """Tell whether an organization accepted a document version.

    Args:
        db_session: Current database session.
        organization_id: The organization.
        legal_document_id: The document version.

    Returns:
        `True` if a consent row exists.
    """
    return (
        await find_consent(
            db_session,
            user_id=None,
            legal_document_id=legal_document_id,
            organization_id=organization_id,
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Access audit log
# ---------------------------------------------------------------------------


async def insert_audit_log(
    db_session: AsyncSession, values: dict[str, Any]
) -> AccessAuditLogModel:
    """Append an access audit entry.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Column values of the new row.

    Returns:
        The inserted entry.
    """
    audit_record = AccessAuditLogModel(**values)
    db_session.add(audit_record)
    await db_session.flush()
    return audit_record


def _audit_conditions(
    *,
    user_id: UUID | None,
    organization_id: UUID | None,
    action: str | None,
    resource_type: str | None,
    occurred_from: datetime,
    occurred_to: datetime,
) -> list[ColumnElement[bool]]:
    """Build the filters shared by the audit-log search and its count."""
    conditions: list[ColumnElement[bool]] = [
        AccessAuditLogModel.occurred_at >= occurred_from,
        AccessAuditLogModel.occurred_at <= occurred_to,
    ]
    if user_id is not None:
        conditions.append(AccessAuditLogModel.user_id == user_id)
    if organization_id is not None:
        conditions.append(AccessAuditLogModel.organization_id == organization_id)
    if action is not None:
        conditions.append(AccessAuditLogModel.action == action)
    if resource_type is not None:
        conditions.append(AccessAuditLogModel.resource_type == resource_type)
    return conditions


async def list_audit_logs(
    db_session: AsyncSession,
    *,
    user_id: UUID | None,
    organization_id: UUID | None,
    action: str | None,
    resource_type: str | None,
    occurred_from: datetime,
    occurred_to: datetime,
    offset: int,
    limit: int,
) -> list[AccessAuditLogModel]:
    """Search the audit log, newest first.

    Args:
        db_session: Current database session.
        user_id: Only entries about or by this user, if given.
        organization_id: Only entries of this organization, if given.
        action: Only this `AccessAuditAction` value, if given.
        resource_type: Only this resource type, if given.
        occurred_from: Start of the time range (inclusive).
        occurred_to: End of the time range (inclusive).
        offset: Rows to skip.
        limit: Maximum rows.

    Returns:
        The matching entries.
    """
    query_result = await db_session.execute(
        select(AccessAuditLogModel)
        .where(
            and_(
                *_audit_conditions(
                    user_id=user_id,
                    organization_id=organization_id,
                    action=action,
                    resource_type=resource_type,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                )
            )
        )
        .order_by(AccessAuditLogModel.occurred_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_audit_logs(
    db_session: AsyncSession,
    *,
    user_id: UUID | None,
    organization_id: UUID | None,
    action: str | None,
    resource_type: str | None,
    occurred_from: datetime,
    occurred_to: datetime,
) -> int:
    """Count the entries `list_audit_logs` would match.

    Args:
        db_session: Current database session.
        user_id: Only entries about or by this user, if given.
        organization_id: Only entries of this organization, if given.
        action: Only this `AccessAuditAction` value, if given.
        resource_type: Only this resource type, if given.
        occurred_from: Start of the time range (inclusive).
        occurred_to: End of the time range (inclusive).

    Returns:
        The total number of matching entries.
    """
    query_result = await db_session.execute(
        select(func.count(AccessAuditLogModel.access_audit_log_id)).where(
            and_(
                *_audit_conditions(
                    user_id=user_id,
                    organization_id=organization_id,
                    action=action,
                    resource_type=resource_type,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                )
            )
        )
    )
    return query_result.scalar() or 0


# ---------------------------------------------------------------------------
# Lookups kept for other domains' use through the service
# ---------------------------------------------------------------------------


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


async def search_membership_ids_by_person(
    db_session: AsyncSession,
    *,
    search_text: str,
    organization_id: UUID | None,
    limit: int,
) -> list[UUID]:
    """Find memberships whose person's name or phone number contains a text.

    Args:
        db_session: Current database session.
        search_text: Text matched case-insensitively against the user's full
            name and phone number; ``%``, ``_`` and ``\\`` are literal.
        organization_id: Only memberships of this organization; `None` means
            every organization.
        limit: Largest number of IDs returned.

    Returns:
        Membership IDs, newest first (a left membership is included: the
        caller filters by the profile it joins).
    """
    escaped_text = (
        search_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    pattern = f"%{escaped_text}%"
    conditions: list[ColumnElement[bool]] = [
        or_(
            UserModel.full_name.ilike(pattern, escape="\\"),
            UserModel.phone_number.ilike(pattern, escape="\\"),
        )
    ]
    if organization_id is not None:
        conditions.append(MembershipModel.organization_id == organization_id)
    query_result = await db_session.execute(
        select(MembershipModel.membership_id)
        .join(UserModel, UserModel.user_id == MembershipModel.user_id)
        .where(and_(*conditions))
        .order_by(MembershipModel.created_at.desc())
        .limit(limit)
    )
    return list(query_result.scalars().all())
