"""Password handling shared by sign-up, login and the password changes (ACC-03, ACC-06).

Strength check, hashing and verification off the event loop (scrypt is
CPU-bound), setting a person's password with its history, and the counting
of a wrong current password towards the login lockout. Internal module:
other domains use ``service.py``.
"""

import asyncio
from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.audit_service as audit_service
import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import (
    CurrentPasswordIncorrectError,
    LoginLockedError,
    WeakPasswordError,
)
from app.domains.identity.models import (
    UserStateModel,
)
from app.domains.identity.security import (
    hash_password,
    verify_password,
)
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    SessionIdentity,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings

# ID-31: this many revoked password hashes are kept for the reuse check.
PASSWORD_HISTORY_REVOKED_KEPT = 5


async def hash_password_async(password: str) -> str:
    """Hash a password on a worker thread so the event loop keeps serving."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, stored_hash: str) -> bool:
    """Verify a password on a worker thread so the event loop keeps serving."""
    return await asyncio.to_thread(verify_password, password, stored_hash)


def check_password_strength(password: str) -> None:
    """Refuse a password that is too short or only spaces.

    Raises:
        WeakPasswordError: If the password breaks the policy.
    """
    if len(password) < settings.IDENTITY_PASSWORD_MIN_LENGTH or not password.strip():
        raise WeakPasswordError(
            f"The password needs at least {settings.IDENTITY_PASSWORD_MIN_LENGTH} "
            "characters"
        )


async def set_password(
    db_session: AsyncSession, user_id: UUID, new_password: str
) -> None:
    """Replace a user's password: revoke the old credential, add the new (ID-17).

    Refuses a password equal to one of the last ones (ID-31) and prunes revoked
    credentials beyond the last 5.

    Args:
        db_session: Session owned by the entry boundary.
        user_id: The user.
        new_password: The plain new password.

    Raises:
        WeakPasswordError: If the password breaks the policy or was used
            recently.

    Side Effects:
        Revokes and inserts `user_credentials` rows.
    """
    check_password_strength(new_password)
    recent_hashes = await identity_repository.list_recent_password_hashes(
        db_session, user_id, PASSWORD_HISTORY_REVOKED_KEPT + 1
    )
    for recent_hash in recent_hashes:
        if await verify_password_async(new_password, recent_hash):
            raise WeakPasswordError("This password was used recently; choose another")
    new_hash = await hash_password_async(new_password)
    await identity_repository.revoke_active_credential(db_session, user_id, utc_now())
    await identity_repository.insert_credential(
        db_session, user_id=user_id, secret_hash=new_hash
    )
    await identity_repository.prune_revoked_credentials(
        db_session, user_id, PASSWORD_HISTORY_REVOKED_KEPT
    )


async def count_failed_password(
    db_session: AsyncSession,
    *,
    user_id: UUID,
    state_record: UserStateModel | None,
    client_context: ClientContext | None,
) -> None:
    """Count a wrong password towards the login lockout (ID-23).

    Shared by the login and by the checks that ask for the current password
    (password change, phone change), so guessing through a stolen session is
    limited like guessing at the login screen (RV-ID6). The caller flushes.

    Args:
        db_session: Current database session.
        user_id: The person whose password was guessed.
        state_record: The person's locked `user_state` row, if it exists.
        client_context: IP address and user agent of the request.

    Side Effects:
        Raises the failure counter; at the limit the counter restarts and the
        lockout window opens (with an audit row).
    """
    if state_record is None:
        return
    state_record.failed_login_count += 1
    if state_record.failed_login_count >= settings.IDENTITY_LOGIN_MAX_FAILED_ATTEMPTS:
        # The counter restarts so the next burst of guesses after the lockout
        # needs the full number of attempts again.
        state_record.failed_login_count = 0
        state_record.login_locked_until = utc_now() + timedelta(
            minutes=settings.IDENTITY_LOGIN_LOCKOUT_MINUTES
        )
        await audit_service.record_account_event(
            db_session,
            user_id=user_id,
            organization_id=None,
            action=AccessAuditAction.LOGIN_LOCKED,
            details={"locked_minutes": settings.IDENTITY_LOGIN_LOCKOUT_MINUTES},
            client_context=client_context,
        )


async def require_current_password(
    db_session: AsyncSession,
    session_identity: SessionIdentity,
    current_password: str,
    client_context: ClientContext | None,
) -> None:
    """Check the current password of a logged-in person, counting a wrong one.

    Args:
        db_session: Session owned by the entry boundary.
        session_identity: The validated session.
        current_password: The password the person typed.
        client_context: IP address and user agent of the request.

    Raises:
        LoginLockedError: The account is in its temporary lockout.
        CurrentPasswordIncorrectError: The password is wrong; the guess is
            counted and flushed, and the router commits it.
    """
    state_record = await identity_repository.get_user_state_for_update(
        db_session, session_identity.user_id
    )
    if (
        state_record is not None
        and state_record.login_locked_until is not None
        and state_record.login_locked_until > utc_now()
    ):
        raise LoginLockedError("Too many wrong passwords; try again later")
    credential_record = await identity_repository.find_active_credential(
        db_session, session_identity.user_id
    )
    if credential_record is None or not await verify_password_async(
        current_password, credential_record.secret_hash
    ):
        await count_failed_password(
            db_session,
            user_id=session_identity.user_id,
            state_record=state_record,
            client_context=client_context,
        )
        await db_session.flush()
        raise CurrentPasswordIncorrectError("The current password is wrong")
