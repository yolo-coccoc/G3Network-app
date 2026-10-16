"""One-time codes by SMS: issue, send, consume (ACC-03, ACC-06, ACC-09).

Sign-up, invitation, password-reset and phone-change codes: the per-phone
and per-inviter limits, the code an invitation carries for its organization
(ID-52), the public send endpoint that never reveals whether an account
exists, and the one-time consumption with its attempt counter. Internal
module: other domains use ``service.py``.
"""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import (
    InvalidOneTimeCodeError,
    OneTimeCodePurposeError,
    OneTimeCodeRateLimitError,
    UserConflictError,
)
from app.domains.identity.models import (
    OneTimeCodeModel,
)
from app.domains.identity.providers import get_sms_sender
from app.domains.identity.schemas import (
    OneTimeCodeSendRequest,
    OneTimeCodeSendResponse,
)
from app.domains.identity.security import (
    generate_one_time_code,
    hash_one_time_code,
    verify_one_time_code,
)
from app.domains.identity.types import (
    OneTimeCodePurpose,
    UserStatus,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings

# Codes older than this past their expiry are deleted (ID-37).
STALE_CODE_RETENTION = timedelta(days=1)


# Purposes a person may request without being logged in.
_PUBLIC_CODE_PURPOSES = frozenset(
    {
        OneTimeCodePurpose.SIGN_UP,
        OneTimeCodePurpose.INVITE,
        OneTimeCodePurpose.PASSWORD_RESET,
    }
)


# Fixed text for an invitation to an existing account: nothing the inviter typed
# or chose (an organization name, free text) reaches the recipient (RV-ID2).
_INVITE_NOTICE_MESSAGE = (
    "G3 Network: ban co mot loi moi vao mot to chuc. "
    "Dang nhap ung dung de xem va chap nhan."
)


_CODE_MESSAGES = {
    OneTimeCodePurpose.SIGN_UP: "Ma dang ky G3 Network: {code}. Het han sau {minutes} phut.",
    OneTimeCodePurpose.INVITE: (
        "Ban duoc moi vao G3 Network. Ma kich hoat: {code}. Het han sau {minutes} phut."
    ),
    OneTimeCodePurpose.PASSWORD_RESET: (
        "Ma dat lai mat khau G3 Network: {code}. Het han sau {minutes} phut."
    ),
    OneTimeCodePurpose.PHONE_CHANGE: (
        "Ma doi so dien thoai G3 Network: {code}. Het han sau {minutes} phut."
    ),
}


async def _enforce_send_limits(
    db_session: AsyncSession, *, phone_number: str, purpose: OneTimeCodePurpose
) -> None:
    """Refuse a code that comes too soon or too often for a phone number.

    Args:
        db_session: Current database session.
        phone_number: Destination of the code.
        purpose: What the code is for.

    Raises:
        OneTimeCodeRateLimitError: If the resend cooldown has not passed or
            the daily limit of the number is reached (SMS-pumping guard).
    """
    now = utc_now()
    last_sent_at = await identity_repository.find_latest_one_time_code_time(
        db_session, phone_number=phone_number, purpose=purpose.value
    )
    cooldown = timedelta(seconds=settings.IDENTITY_OTP_RESEND_COOLDOWN_SECONDS)
    if last_sent_at is not None and now - last_sent_at < cooldown:
        raise OneTimeCodeRateLimitError(
            "A code was just sent; wait before asking again"
        )
    sent_today = await identity_repository.count_one_time_codes_since(
        db_session, phone_number, now - timedelta(hours=24)
    )
    if sent_today >= settings.IDENTITY_OTP_MAX_PER_PHONE_PER_DAY:
        raise OneTimeCodeRateLimitError("Too many codes for this phone number today")


async def _enforce_inviter_quota(db_session: AsyncSession, issued_by: UUID) -> None:
    """Refuse an invitation SMS when the inviter sent too many today (RV-ID2).

    Args:
        db_session: Current database session.
        issued_by: The person who invites.

    Raises:
        OneTimeCodeRateLimitError: If `IDENTITY_INVITES_PER_USER_PER_DAY` is
            reached.
    """
    sent_today = await identity_repository.count_invitation_codes_by_issuer_since(
        db_session, issued_by=issued_by, since=utc_now() - timedelta(hours=24)
    )
    if sent_today >= settings.IDENTITY_INVITES_PER_USER_PER_DAY:
        raise OneTimeCodeRateLimitError("Too many invitations sent today")


async def issue_one_time_code(
    db_session: AsyncSession,
    *,
    purpose: OneTimeCodePurpose,
    phone_number: str,
    user_id: UUID | None,
    issued_by: UUID | None,
    organization_id: UUID | None = None,
) -> OneTimeCodeModel:
    """Create a code, store its hash and send it by SMS (ID-16).

    Also applies the send limits and deletes codes that expired long ago. An
    `INVITE_NOTICE` row (an invitation to an existing account) carries a code
    nobody is told: it only counts towards the limits, and its SMS is a fixed
    text with no organization name (RV-ID2).

    Args:
        db_session: Session owned by the entry boundary.
        purpose: What the code is for; decides its lifetime (invitations last
            `IDENTITY_INVITE_TTL_HOURS`, the others `IDENTITY_OTP_TTL_MINUTES`).
        phone_number: Destination, E.164.
        user_id: The user the code is for; `None` for a sign-up.
        issued_by: The user who triggered it; `None` when self-requested.
        organization_id: For an invitation, the organization it is for.

    Returns:
        The stored code row (the plain code is only in the SMS).

    Raises:
        OneTimeCodeRateLimitError: If a send limit is reached.

    Side Effects:
        Inserts a `one_time_codes` row and sends an SMS through the provider.
    """
    now = utc_now()
    await identity_repository.delete_stale_one_time_codes(
        db_session, now - STALE_CODE_RETENTION
    )
    await _enforce_send_limits(db_session, phone_number=phone_number, purpose=purpose)
    if issued_by is not None and purpose in (
        OneTimeCodePurpose.INVITE,
        OneTimeCodePurpose.INVITE_NOTICE,
    ):
        await _enforce_inviter_quota(db_session, issued_by)
    ttl = (
        timedelta(hours=settings.IDENTITY_INVITE_TTL_HOURS)
        if purpose in (OneTimeCodePurpose.INVITE, OneTimeCodePurpose.INVITE_NOTICE)
        else timedelta(minutes=settings.IDENTITY_OTP_TTL_MINUTES)
    )
    one_time_code_id = uuid4()
    code = generate_one_time_code()
    code_record = await identity_repository.insert_one_time_code(
        db_session,
        {
            "one_time_code_id": one_time_code_id,
            "purpose": purpose.value,
            "user_id": user_id,
            "phone_number": phone_number,
            "code_hash": hash_one_time_code(one_time_code_id, code),
            "issued_by": issued_by,
            "organization_id": organization_id,
            "created_at": now,
            "expires_at": now + ttl,
            "failed_attempt_count": 0,
        },
    )
    await get_sms_sender().send_sms(
        phone_number,
        _INVITE_NOTICE_MESSAGE
        if purpose is OneTimeCodePurpose.INVITE_NOTICE
        else _CODE_MESSAGES[purpose].format(
            code=code, minutes=int(ttl.total_seconds() // 60)
        ),
    )
    return code_record


async def consume_one_time_code(
    db_session: AsyncSession,
    *,
    phone_number: str,
    purpose: OneTimeCodePurpose,
    code: str,
    user_id: UUID | None = None,
) -> OneTimeCodeModel:
    """Check a typed code against the newest code and mark it used.

    Only the newest code of the phone number and purpose is checked; a wrong
    guess counts against it and the code dies at
    `IDENTITY_OTP_MAX_FAILED_ATTEMPTS` (ID-37).

    Args:
        db_session: Session owned by the entry boundary.
        phone_number: Number the code was sent to.
        purpose: What the code is for.
        code: The code the person typed.
        user_id: Only a code issued for this user, if given.

    Returns:
        The code row, now used.

    Raises:
        InvalidOneTimeCodeError: If there is no live code, it is wrong,
            expired, used, or dead; a wrong guess is counted and flushed.
    """
    now = utc_now()
    code_record = await identity_repository.find_latest_one_time_code(
        db_session, phone_number=phone_number, purpose=purpose.value, user_id=user_id
    )
    if (
        code_record is None
        or code_record.used_at is not None
        or code_record.expires_at <= now
        or code_record.failed_attempt_count >= settings.IDENTITY_OTP_MAX_FAILED_ATTEMPTS
    ):
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    if not verify_one_time_code(
        code_record.one_time_code_id, code, code_record.code_hash
    ):
        code_record.failed_attempt_count += 1
        await db_session.flush()
        raise InvalidOneTimeCodeError("The code is wrong, expired or already used")
    code_record.used_at = now
    await db_session.flush()
    return code_record


async def _resolve_pending_invitation_organization_id(
    db_session: AsyncSession, user_id: UUID
) -> UUID | None:
    """Find the organization a self-requested invitation code must be issued for.

    Since ID-52 an `INVITE` code is tied to one organization and accepting it
    activates only that membership, while only the newest code of a phone is
    ever checked. A code the person asks for themselves (the app's "send me
    the code again") must therefore carry the organization of a pending
    invitation, or it would be refused and would also kill the inviter's code.
    The choice: the only pending invitation; with several, the organization
    of the newest unexpired invitation code when that invitation is still
    pending; otherwise the newest pending invitation.

    Args:
        db_session: Current database session.
        user_id: The invited person.

    Returns:
        The organization ID, or `None` when the person has no pending
        invitation.

    Side Effects:
        With several pending invitations, locks the person's newest
        invitation code row until the transaction ends.
    """
    invited_memberships = await identity_repository.list_invited_memberships_by_user(
        db_session, user_id
    )
    if not invited_memberships:
        return None
    if len(invited_memberships) == 1:
        return invited_memberships[0].organization_id
    pending_organization_ids = {
        membership_record.organization_id for membership_record in invited_memberships
    }
    latest_code_record = await identity_repository.find_latest_one_time_code_by_user_id(
        db_session, user_id=user_id, purpose=OneTimeCodePurpose.INVITE.value
    )
    if (
        latest_code_record is not None
        and latest_code_record.expires_at > utc_now()
        and latest_code_record.organization_id in pending_organization_ids
    ):
        return latest_code_record.organization_id
    newest_membership = max(
        invited_memberships, key=lambda membership_record: membership_record.created_at
    )
    return newest_membership.organization_id


async def send_one_time_code(
    db_session: AsyncSession, send_request: OneTimeCodeSendRequest
) -> OneTimeCodeSendResponse:
    """Send a sign-up, invitation or password-reset code (ACC-05).

    For a password reset or an invitation the answer is the same whether or
    not the phone number has an account, and nothing is sent when it has not,
    so the endpoint cannot be used to find out who is registered. A sign-up
    for a number that already has an account is reported (`409`, the app then
    goes to login). An invitation code is issued for the organization of the
    person's pending invitation (`_resolve_pending_invitation_organization_id`);
    an invited account with no pending invitation gets the same silent answer.

    Args:
        db_session: Session owned by the entry boundary.
        send_request: Phone number and purpose.

    Returns:
        When the code (would) expire.

    Raises:
        OneTimeCodePurposeError: If the purpose needs a login (phone change).
        UserConflictError: For a sign-up when the number is registered.
        OneTimeCodeRateLimitError: If a send limit is reached.

    Side Effects:
        May insert a code row and send an SMS.
    """
    purpose = send_request.purpose
    if purpose not in _PUBLIC_CODE_PURPOSES:
        raise OneTimeCodePurposeError("This code needs a login")
    organization_id: UUID | None = None
    phone_number = send_request.phone_number
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, phone_number
    )
    ttl = (
        timedelta(hours=settings.IDENTITY_INVITE_TTL_HOURS)
        if purpose is OneTimeCodePurpose.INVITE
        else timedelta(minutes=settings.IDENTITY_OTP_TTL_MINUTES)
    )
    silent_response = OneTimeCodeSendResponse(expires_at=utc_now() + ttl)

    if purpose is OneTimeCodePurpose.SIGN_UP:
        # An account that only exists because someone invited the number has
        # no password: the number's real owner may still sign up (RV-ID3).
        if user_record is not None and user_record.status != UserStatus.INVITED.value:
            raise UserConflictError("This phone number already has an account")
        user_id = None
    elif purpose is OneTimeCodePurpose.PASSWORD_RESET:
        if user_record is None or user_record.status != UserStatus.ACTIVE.value:
            return silent_response
        user_id = user_record.user_id
    else:
        if user_record is None or user_record.status != UserStatus.INVITED.value:
            return silent_response
        user_id = user_record.user_id
        organization_id = await _resolve_pending_invitation_organization_id(
            db_session, user_id
        )
        if organization_id is None:
            return silent_response

    code_record = await issue_one_time_code(
        db_session,
        purpose=purpose,
        phone_number=phone_number,
        user_id=user_id,
        issued_by=None,
        organization_id=organization_id,
    )
    return OneTimeCodeSendResponse(expires_at=code_record.expires_at)
