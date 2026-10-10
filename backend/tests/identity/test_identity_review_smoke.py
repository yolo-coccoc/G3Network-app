"""Smoke tests from the identity security review, run against an in-memory store.

Two kinds of test live here:

- Guards that pass today and protect rules verified during the review: forged,
  expired or wrong-kind tokens are refused, a logged-out session is dead,
  refresh rotation retires the old refresh token, an ORG_ADMIN cannot reach
  another organization's members, a CO_ADMIN cannot grant administrator roles.
- Findings of the review (``REVIEW ID-n``) written as the correct behaviour and
  marked ``xfail(strict=True)``: they fail while the defect exists, and the fix
  turns them into an unexpected pass that forces the marker off.

The services run unchanged; only the module functions of
`app.domains.identity.repository` are replaced by the methods of
`IdentityStore`, which keeps rows in plain Python collections. No database.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.account_service as account_service
import app.domains.identity.audit_service as audit_service
import app.domains.identity.member_service as member_service
import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import (
    AccessDeniedError,
    CurrentPasswordIncorrectError,
    MembershipNotFoundError,
    OrgAdminProtectedError,
    OrganizationNotFoundError,
    RoleConflictError,
    SessionInvalidError,
    UserConflictError,
)
from app.domains.identity.models import (
    MembershipModel,
    OneTimeCodeModel,
    OrganizationModel,
    UserCredentialModel,
    UserModel,
    UserRoleAssignmentModel,
    UserSessionModel,
    UserStateModel,
)
from app.domains.identity.schemas import (
    AdminHandoverRequest,
    MemberInviteRequest,
    MembershipReasonRequest,
    OneTimeCodeSendRequest,
    PasswordChangeRequest,
    PhoneChangeConfirmRequest,
    PhoneChangeRequest,
    RefreshRequest,
    UserLockRequest,
)
from app.domains.identity.security import (
    create_access_token,
    generate_refresh_token,
    hash_one_time_code,
    hash_password,
    hash_refresh_token,
    normalize_phone_number,
    read_access_token,
)
from app.domains.identity.types import (
    MembershipEndKind,
    MembershipStatus,
    OneTimeCodePurpose,
    Principal,
    SessionIdentity,
    SessionPlatform,
    UserRole,
    UserStatus,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.errors import (
    ConflictError,
    PermissionDeniedError,
    TooManyRequestsError,
)
from tests.builders import build_organization_record

# What a `pytest.raises` block raises when the expected error did not happen;
# an xfail limited to it (or to the assertion) cannot hide a broken fake.
DID_NOT_RAISE = pytest.fail.Exception

# ---------------------------------------------------------------------------
# In-memory identity store
# ---------------------------------------------------------------------------


class FlushSession:
    """Placeholder database session: services only call `flush` on it directly."""

    async def flush(self) -> None:
        """Pretend to write pending changes."""


def _db() -> AsyncSession:
    """Return the placeholder session typed as an `AsyncSession`."""
    return FlushSession()  # type: ignore[return-value]


class SmsRecorder:
    """SMS provider fake that keeps every message instead of sending it.

    Attributes:
        sent: ``(phone_number, text)`` of every message, in order.
    """

    def __init__(self) -> None:
        """Start with no message sent."""
        self.sent: list[tuple[str, str]] = []

    async def send_sms(self, phone_number: str, text: str) -> None:
        """Record one message."""
        self.sent.append((phone_number, text))


class IdentityStore:
    """Rows of the identity tables kept in memory, behind the repository API.

    Each public coroutine has the name and signature of the
    `app.domains.identity.repository` function it replaces; `install` swaps
    them in. Behaviour mirrors the SQL of the real repository (live rows are
    those with `left_at` / `revoked_at` / `deleted_at` unset).

    Attributes:
        organizations: Organizations by ID.
        users: Users by ID.
        user_states: `user_state` rows by user ID.
        memberships: Memberships by ID.
        roles: Every role assignment, revoked ones included.
        sessions: Login sessions by ID.
        credentials: Password credentials, revoked ones included.
        codes: One-time codes, newest last.
        deleted_session_calls: ``(user_id, except_session_id)`` of each
            `delete_sessions_by_user` call.
        sms: Recorder standing in for the SMS provider.
    """

    def __init__(self) -> None:
        """Start empty."""
        self.organizations: dict[UUID, OrganizationModel] = {}
        self.users: dict[UUID, UserModel] = {}
        self.user_states: dict[UUID, UserStateModel] = {}
        self.memberships: dict[UUID, MembershipModel] = {}
        self.roles: list[UserRoleAssignmentModel] = []
        self.sessions: dict[UUID, UserSessionModel] = {}
        self.credentials: list[UserCredentialModel] = []
        self.codes: list[OneTimeCodeModel] = []
        self.deleted_session_calls: list[tuple[UUID, UUID | None]] = []
        self.sms = SmsRecorder()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace the repository functions with this store's methods."""
        for name in dir(self):
            if not name.startswith("_") and hasattr(identity_repository, name):
                monkeypatch.setattr(identity_repository, name, getattr(self, name))

    # Organizations and users -------------------------------------------------

    async def get_organization(
        self, _db: object, organization_id: UUID
    ) -> OrganizationModel | None:
        """Find an organization by ID."""
        return self.organizations.get(organization_id)

    async def get_user(self, _db: object, user_id: UUID) -> UserModel | None:
        """Find a user by ID."""
        return self.users.get(user_id)

    async def find_live_user_by_phone(
        self, _db: object, phone_number: str
    ) -> UserModel | None:
        """Find the live user owning a phone number."""
        return next(
            (
                user
                for user in self.users.values()
                if user.phone_number == phone_number and user.deleted_at is None
            ),
            None,
        )

    async def find_live_user_by_email(
        self, _db: object, email: str
    ) -> UserModel | None:
        """Find the live user owning an e-mail address, ignoring case."""
        return next(
            (
                user
                for user in self.users.values()
                if user.email is not None and user.email.lower() == email.lower()
            ),
            None,
        )

    async def insert_user(self, _db: object, values: dict[str, Any]) -> UserModel:
        """Insert a user with its state row."""
        user_record = UserModel(user_id=uuid4(), created_at=utc_now(), **values)
        self.users[user_record.user_id] = user_record
        self.user_states[user_record.user_id] = UserStateModel(
            user_id=user_record.user_id, failed_login_count=0
        )
        return user_record

    async def apply_user_values(
        self, _db: object, user_record: UserModel, values: dict[str, Any]
    ) -> UserModel:
        """Set new column values on a user."""
        for column_name, value in values.items():
            setattr(user_record, column_name, value)
        return user_record

    async def get_user_state(self, _db: object, user_id: UUID) -> UserStateModel | None:
        """Read a user's state row."""
        return self.user_states.get(user_id)

    async def get_user_state_for_update(
        self, _db: object, user_id: UUID
    ) -> UserStateModel | None:
        """Read a user's state row (no lock needed in memory)."""
        return self.user_states.get(user_id)

    # Credentials -------------------------------------------------------------

    async def find_active_credential(
        self, _db: object, user_id: UUID
    ) -> UserCredentialModel | None:
        """Find the user's unrevoked password."""
        return next(
            (
                credential
                for credential in self.credentials
                if credential.user_id == user_id and credential.revoked_at is None
            ),
            None,
        )

    # Sessions ----------------------------------------------------------------

    async def get_session(
        self, _db: object, session_id: UUID
    ) -> UserSessionModel | None:
        """Find a login session by ID."""
        return self.sessions.get(session_id)

    async def find_session_by_refresh_hash(
        self, _db: object, refresh_token_hash: str
    ) -> UserSessionModel | None:
        """Find the session holding a refresh-token hash."""
        return next(
            (
                session_record
                for session_record in self.sessions.values()
                if session_record.refresh_token_hash == refresh_token_hash
            ),
            None,
        )

    async def delete_session(
        self, _db: object, session_record: UserSessionModel
    ) -> None:
        """End one session."""
        self.sessions.pop(session_record.user_session_id, None)

    async def delete_sessions_by_user(
        self, _db: object, user_id: UUID, *, except_session_id: UUID | None = None
    ) -> None:
        """End every session of a user but one, and remember the call."""
        self.deleted_session_calls.append((user_id, except_session_id))
        for session_id, session_record in list(self.sessions.items()):
            if session_record.user_id == user_id and session_id != except_session_id:
                del self.sessions[session_id]

    async def clear_session_organization(
        self, _db: object, *, user_id: UUID | None, organization_id: UUID
    ) -> None:
        """Unset an organization on the matching sessions."""
        for session_record in self.sessions.values():
            if session_record.organization_id == organization_id and (
                user_id is None or session_record.user_id == user_id
            ):
                session_record.organization_id = None

    # One-time codes ----------------------------------------------------------

    async def delete_stale_one_time_codes(
        self, _db: object, expired_before: datetime
    ) -> None:
        """Drop codes that expired before the given time."""
        self.codes = [code for code in self.codes if code.expires_at >= expired_before]

    async def find_latest_one_time_code_time(
        self, _db: object, *, phone_number: str, purpose: str
    ) -> datetime | None:
        """Return when the newest code of a phone and purpose was created."""
        times = [
            code.created_at
            for code in self.codes
            if code.phone_number == phone_number and code.purpose == purpose
        ]
        return max(times) if times else None

    async def count_one_time_codes_since(
        self, _db: object, phone_number: str, since: datetime
    ) -> int:
        """Count codes sent to a phone number since a time."""
        return sum(
            1
            for code in self.codes
            if code.phone_number == phone_number and code.created_at >= since
        )

    async def insert_one_time_code(
        self, _db: object, values: dict[str, Any]
    ) -> OneTimeCodeModel:
        """Insert a code."""
        code_record = OneTimeCodeModel(**values)
        self.codes.append(code_record)
        return code_record

    async def find_latest_one_time_code(
        self,
        _db: object,
        *,
        phone_number: str,
        purpose: str,
        user_id: UUID | None = None,
    ) -> OneTimeCodeModel | None:
        """Return the newest code of a phone and purpose (and user, if given)."""
        matching = [
            code
            for code in self.codes
            if code.phone_number == phone_number
            and code.purpose == purpose
            and (user_id is None or code.user_id == user_id)
        ]
        return matching[-1] if matching else None

    async def find_latest_one_time_code_for_user(
        self, _db: object, *, user_id: UUID, purpose: str
    ) -> OneTimeCodeModel | None:
        """Return the newest code issued for a user and purpose."""
        matching = [
            code
            for code in self.codes
            if code.user_id == user_id and code.purpose == purpose
        ]
        return matching[-1] if matching else None

    # Memberships and roles ---------------------------------------------------

    async def get_membership(
        self, _db: object, membership_id: UUID
    ) -> MembershipModel | None:
        """Find a membership by ID."""
        return self.memberships.get(membership_id)

    async def find_live_membership(
        self, _db: object, *, organization_id: UUID, user_id: UUID
    ) -> MembershipModel | None:
        """Find the unended membership of a person in an organization."""
        return next(
            (
                membership
                for membership in self.memberships.values()
                if membership.organization_id == organization_id
                and membership.user_id == user_id
                and membership.left_at is None
            ),
            None,
        )

    async def list_live_memberships_with_organizations(
        self, _db: object, user_id: UUID
    ) -> list[tuple[MembershipModel, OrganizationModel]]:
        """List a person's unended memberships with their organizations."""
        return [
            (membership, self.organizations[membership.organization_id])
            for membership in self.memberships.values()
            if membership.user_id == user_id and membership.left_at is None
        ]

    async def list_invited_memberships_by_user(
        self, _db: object, user_id: UUID
    ) -> list[MembershipModel]:
        """List a person's pending invitations."""
        return [
            membership
            for membership in self.memberships.values()
            if membership.user_id == user_id
            and membership.status == MembershipStatus.INVITED.value
            and membership.left_at is None
        ]

    async def insert_membership(
        self, _db: object, values: dict[str, Any]
    ) -> MembershipModel:
        """Insert a membership."""
        membership_record = MembershipModel(
            membership_id=uuid4(), created_at=utc_now(), left_at=None, **values
        )
        self.memberships[membership_record.membership_id] = membership_record
        return membership_record

    async def apply_membership_values(
        self, _db: object, membership_record: MembershipModel, values: dict[str, Any]
    ) -> MembershipModel:
        """Set new column values on a membership."""
        for column_name, value in values.items():
            setattr(membership_record, column_name, value)
        return membership_record

    async def insert_role_assignment(
        self,
        _db: object,
        *,
        membership_record: MembershipModel,
        role: str,
        granted_by: UUID | None,
    ) -> UserRoleAssignmentModel:
        """Grant a role to a membership."""
        assignment_record = UserRoleAssignmentModel(
            user_role_assignment_id=uuid4(),
            organization_id=membership_record.organization_id,
            membership_id=membership_record.membership_id,
            role=role,
            granted_at=utc_now(),
            granted_by=granted_by,
            revoked_at=None,
        )
        self.roles.append(assignment_record)
        return assignment_record

    def _live_roles(self) -> list[UserRoleAssignmentModel]:
        """Return the unrevoked assignments."""
        return [role for role in self.roles if role.revoked_at is None]

    async def list_active_roles_by_membership(
        self, _db: object, membership_id: UUID
    ) -> list[str]:
        """List the roles a membership holds now."""
        return [
            role.role
            for role in self._live_roles()
            if role.membership_id == membership_id
        ]

    async def find_active_role_assignment(
        self, _db: object, *, membership_id: UUID, role: str
    ) -> UserRoleAssignmentModel | None:
        """Find the live assignment of one role to a membership."""
        return next(
            (
                assignment
                for assignment in self._live_roles()
                if assignment.membership_id == membership_id and assignment.role == role
            ),
            None,
        )

    async def find_active_org_admin_assignment(
        self, _db: object, organization_id: UUID
    ) -> UserRoleAssignmentModel | None:
        """Find the live ORG_ADMIN assignment of an organization."""
        return next(
            (
                assignment
                for assignment in self._live_roles()
                if assignment.organization_id == organization_id
                and assignment.role == UserRole.ORG_ADMIN.value
            ),
            None,
        )

    async def count_active_role_holders(self, _db: object, role: str) -> int:
        """Count live holders of a role whose membership and account are ACTIVE."""
        count = 0
        for assignment in self._live_roles():
            membership = self.memberships.get(assignment.membership_id)
            user = None if membership is None else self.users.get(membership.user_id)
            if (
                assignment.role == role
                and membership is not None
                and user is not None
                and membership.status == MembershipStatus.ACTIVE.value
                and membership.left_at is None
                and user.status == UserStatus.ACTIVE.value
                and user.deleted_at is None
            ):
                count += 1
        return count

    async def revoke_role_assignment(
        self,
        _db: object,
        assignment_record: UserRoleAssignmentModel,
        *,
        revoked_at: datetime,
        revoked_by: UUID | None,
    ) -> None:
        """Revoke one assignment."""
        assignment_record.revoked_at = revoked_at
        assignment_record.revoked_by = revoked_by

    async def revoke_all_roles_of_membership(
        self,
        _db: object,
        membership_id: UUID,
        *,
        revoked_at: datetime,
        revoked_by: UUID | None,
    ) -> None:
        """Revoke every live assignment of a membership."""
        for assignment in self._live_roles():
            if assignment.membership_id == membership_id:
                assignment.revoked_at = revoked_at
                assignment.revoked_by = revoked_by

    # Builders ----------------------------------------------------------------

    def add_organization(self, *, is_internal: bool) -> OrganizationModel:
        """Add an active organization."""
        organization_record = build_organization_record()
        organization_record.is_internal = is_internal
        self.organizations[organization_record.organization_id] = organization_record
        return organization_record

    def add_user(
        self,
        *,
        status: UserStatus = UserStatus.ACTIVE,
        phone_number: str | None = None,
        full_name: str = "Test Person",
        email: str | None = None,
        password: str | None = None,
    ) -> UserModel:
        """Add a user with a state row and, optionally, a password."""
        user_record = UserModel(
            user_id=uuid4(),
            phone_number=phone_number or f"+849{uuid4().int % 10**8:08d}",
            full_name=full_name,
            email=email,
            status=status.value,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.users[user_record.user_id] = user_record
        self.user_states[user_record.user_id] = UserStateModel(
            user_id=user_record.user_id, failed_login_count=0
        )
        if password is not None:
            self.credentials.append(
                UserCredentialModel(
                    user_credential_id=uuid4(),
                    user_id=user_record.user_id,
                    credential_type="PASSWORD",
                    secret_hash=hash_password(password),
                    created_at=utc_now(),
                )
            )
        return user_record

    def add_membership(
        self,
        user_record: UserModel,
        organization_record: OrganizationModel,
        *,
        roles: tuple[UserRole, ...] = (),
        status: MembershipStatus = MembershipStatus.ACTIVE,
    ) -> MembershipModel:
        """Add a membership holding the given roles."""
        membership_record = MembershipModel(
            membership_id=uuid4(),
            organization_id=organization_record.organization_id,
            user_id=user_record.user_id,
            status=status.value,
            left_at=None,
            joined_at=utc_now() if status is MembershipStatus.ACTIVE else None,
            created_at=utc_now(),
        )
        self.memberships[membership_record.membership_id] = membership_record
        for role in roles:
            self.roles.append(
                UserRoleAssignmentModel(
                    user_role_assignment_id=uuid4(),
                    organization_id=organization_record.organization_id,
                    membership_id=membership_record.membership_id,
                    role=role.value,
                    granted_at=utc_now(),
                    revoked_at=None,
                )
            )
        return membership_record

    def add_session(
        self, user_record: UserModel, organization_id: UUID | None
    ) -> tuple[UserSessionModel, str]:
        """Add a live session; return it with its plain refresh token."""
        now = utc_now()
        refresh_token = generate_refresh_token()
        session_record = UserSessionModel(
            user_session_id=uuid4(),
            user_id=user_record.user_id,
            organization_id=organization_id,
            platform=SessionPlatform.ANDROID.value,
            refresh_token_hash=hash_refresh_token(refresh_token),
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=1),
        )
        self.sessions[session_record.user_session_id] = session_record
        return session_record, refresh_token


def principal_of(
    membership_record: MembershipModel,
    organization_record: OrganizationModel,
    *roles: UserRole,
) -> Principal:
    """Build the principal of a member acting in their organization."""
    return Principal(
        user_id=membership_record.user_id,
        membership_id=membership_record.membership_id,
        organization_id=organization_record.organization_id,
        session_id=uuid4(),
        roles=frozenset(roles),
        is_internal=organization_record.is_internal,
    )


async def _no_change_context(_db: object, **_values: object) -> None:
    """Stand-in for `set_change_context`, which needs a real database."""


async def _no_audit_event(_db: object, **_values: object) -> None:
    """Stand-in for `record_account_event`, which inserts a row."""


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[IdentityStore]:
    """Install a fresh in-memory store and neutralize database-only helpers.

    Also empties the membership-end hooks (importing ``app.api.main`` elsewhere
    registers the drivers hook, which needs a database) and sends SMS to a
    recorder exposed as ``store.sms``.
    """
    identity_store = IdentityStore()
    identity_store.install(monkeypatch)
    monkeypatch.setattr(member_service, "set_change_context", _no_change_context)
    monkeypatch.setattr(account_service, "set_change_context", _no_change_context)
    monkeypatch.setattr(audit_service, "record_account_event", _no_audit_event)
    monkeypatch.setattr(member_service, "_membership_end_hooks", [])
    monkeypatch.setattr(member_service, "get_sms_sender", lambda: identity_store.sms)
    monkeypatch.setattr(account_service, "get_sms_sender", lambda: identity_store.sms)
    yield identity_store


def _sent(identity_store: IdentityStore) -> list[tuple[str, str]]:
    """Return the SMS the store's recorder captured."""
    return identity_store.sms.sent


# ---------------------------------------------------------------------------
# Guards: tokens and sessions
# ---------------------------------------------------------------------------


def test_access_token_with_a_changed_session_or_signature_is_rejected() -> None:
    """A token whose session ID or signature was altered no longer verifies."""
    session_id = uuid4()
    access_token = create_access_token(session_id, utc_now() + timedelta(minutes=5))
    session_text, expiry_text, signature_text = access_token.split(".")

    assert read_access_token(access_token) == session_id
    assert read_access_token(f"{uuid4()}.{expiry_text}.{signature_text}") is None
    later_expiry = int(expiry_text) + 86_400
    assert read_access_token(f"{session_text}.{later_expiry}.{signature_text}") is None
    flipped = ("A" if signature_text[0] != "A" else "B") + signature_text[1:]
    assert read_access_token(f"{session_text}.{expiry_text}.{flipped}") is None
    assert read_access_token("not-a-token") is None


def test_expired_access_token_is_rejected() -> None:
    """A correctly signed token past its expiry verifies as nothing."""
    access_token = create_access_token(uuid4(), utc_now() - timedelta(seconds=1))

    assert read_access_token(access_token) is None


@pytest.mark.asyncio
async def test_refresh_token_is_not_accepted_as_a_bearer_token(
    store: IdentityStore,
) -> None:
    """Presenting the refresh token as the access token gives 401."""
    user_record = store.add_user()
    _session_record, refresh_token = store.add_session(user_record, None)

    with pytest.raises(SessionInvalidError):
        await account_service.authenticate_session(_db(), refresh_token)


@pytest.mark.asyncio
async def test_access_token_is_not_accepted_as_a_refresh_token(
    store: IdentityStore,
) -> None:
    """Presenting the access token to the refresh endpoint gives 401."""
    user_record = store.add_user()
    session_record, _refresh_token = store.add_session(user_record, None)
    access_token = create_access_token(
        session_record.user_session_id, utc_now() + timedelta(minutes=5)
    )

    with pytest.raises(SessionInvalidError):
        await account_service.refresh_session(
            _db(), RefreshRequest(refresh_token=access_token)
        )


@pytest.mark.asyncio
async def test_access_token_of_a_logged_out_session_is_rejected(
    store: IdentityStore,
) -> None:
    """After logout the still-unexpired access token of that session is dead."""
    user_record = store.add_user()
    session_record, _refresh_token = store.add_session(user_record, None)
    access_token = create_access_token(
        session_record.user_session_id, utc_now() + timedelta(minutes=5)
    )
    session_identity = await account_service.authenticate_session(_db(), access_token)

    await account_service.logout(
        _db(), session_identity, all_devices=False, client_context=None
    )

    with pytest.raises(SessionInvalidError):
        await account_service.authenticate_session(_db(), access_token)


@pytest.mark.asyncio
async def test_access_token_of_a_locked_account_is_rejected(
    store: IdentityStore,
) -> None:
    """A session whose account an administrator locked is no longer valid."""
    user_record = store.add_user(status=UserStatus.LOCKED)
    session_record, _refresh_token = store.add_session(user_record, None)
    access_token = create_access_token(
        session_record.user_session_id, utc_now() + timedelta(minutes=5)
    )

    with pytest.raises(SessionInvalidError):
        await account_service.authenticate_session(_db(), access_token)


@pytest.mark.asyncio
async def test_refresh_rotation_makes_the_old_refresh_token_unusable(
    store: IdentityStore,
) -> None:
    """A refresh hands out a new refresh token; the old one is refused after."""
    user_record = store.add_user()
    _session_record, first_refresh_token = store.add_session(user_record, None)

    token_response = await account_service.refresh_session(
        _db(), RefreshRequest(refresh_token=first_refresh_token)
    )

    assert token_response.refresh_token != first_refresh_token
    with pytest.raises(SessionInvalidError):
        await account_service.refresh_session(
            _db(), RefreshRequest(refresh_token=first_refresh_token)
        )
    await account_service.refresh_session(
        _db(), RefreshRequest(refresh_token=token_response.refresh_token)
    )


# ---------------------------------------------------------------------------
# Guards: member management reach and role grants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_org_admin_cannot_invite_into_or_lock_members_of_another_organization(
    store: IdentityStore,
) -> None:
    """Another organization's members are out of reach: 404, nothing changes."""
    organization_a = store.add_organization(is_internal=False)
    organization_b = store.add_organization(is_internal=False)
    admin_a = store.add_membership(
        store.add_user(), organization_a, roles=(UserRole.ORG_ADMIN,)
    )
    member_b = store.add_membership(
        store.add_user(), organization_b, roles=(UserRole.DRIVER,)
    )
    caller = principal_of(admin_a, organization_a, UserRole.ORG_ADMIN)

    with pytest.raises(OrganizationNotFoundError):
        await member_service.invite_member(
            _db(),
            caller,
            organization_b.organization_id,
            MemberInviteRequest(phone_number="0901111111", full_name="Someone"),
        )
    with pytest.raises(MembershipNotFoundError):
        await member_service.lock_member(
            _db(), caller, member_b.membership_id, "not mine"
        )
    with pytest.raises(MembershipNotFoundError):
        await member_service.grant_role(
            _db(), caller, member_b.membership_id, UserRole.FLEET_MANAGER
        )
    assert member_b.status == MembershipStatus.ACTIVE.value
    assert _sent(store) == []


@pytest.mark.asyncio
async def test_co_admin_cannot_grant_head_admin_or_co_admin(
    store: IdentityStore,
) -> None:
    """Only a HEAD_ADMIN grants the administrator roles (ID-12)."""
    internal = store.add_organization(is_internal=True)
    co_admin = store.add_membership(
        store.add_user(), internal, roles=(UserRole.CO_ADMIN,)
    )
    colleague = store.add_membership(
        store.add_user(), internal, roles=(UserRole.SALES,)
    )
    caller = principal_of(co_admin, internal, UserRole.CO_ADMIN)

    for role in (UserRole.HEAD_ADMIN, UserRole.CO_ADMIN):
        with pytest.raises(AccessDeniedError):
            await member_service.grant_role(
                _db(), caller, colleague.membership_id, role
            )
    assert await store.list_active_roles_by_membership(
        None, colleague.membership_id
    ) == [UserRole.SALES.value]


# ---------------------------------------------------------------------------
# Findings (xfail strict until fixed)
# ---------------------------------------------------------------------------


def _internal_with_head_admins(
    identity_store: IdentityStore,
) -> tuple[OrganizationModel, MembershipModel, MembershipModel, Principal]:
    """Build the bootstrap shape: HEAD_ADMIN + ORG_ADMIN, an emergency HEAD_ADMIN.

    Returns:
        The internal organization, the second HEAD_ADMIN's membership, a
        CO_ADMIN's membership and the CO_ADMIN's principal.
    """
    internal = identity_store.add_organization(is_internal=True)
    identity_store.add_membership(
        identity_store.add_user(),
        internal,
        roles=(UserRole.HEAD_ADMIN, UserRole.ORG_ADMIN),
    )
    second_head = identity_store.add_membership(
        identity_store.add_user(), internal, roles=(UserRole.HEAD_ADMIN,)
    )
    co_admin = identity_store.add_membership(
        identity_store.add_user(), internal, roles=(UserRole.CO_ADMIN,)
    )
    return (
        internal,
        second_head,
        co_admin,
        principal_of(co_admin, internal, UserRole.CO_ADMIN),
    )


@pytest.mark.asyncio
async def test_co_admin_cannot_lock_a_head_admin_membership(
    store: IdentityStore,
) -> None:
    """Locking a HEAD_ADMIN's membership is managing an admin: CO_ADMIN gets 403."""
    _internal, second_head, _co_admin, caller = _internal_with_head_admins(store)

    with pytest.raises(PermissionDeniedError):
        await member_service.lock_member(
            _db(), caller, second_head.membership_id, "takeover"
        )


@pytest.mark.asyncio
async def test_co_admin_cannot_remove_a_head_admin_membership(
    store: IdentityStore,
) -> None:
    """Removing a HEAD_ADMIN strips the role; a CO_ADMIN may not (403)."""
    _internal, second_head, _co_admin, caller = _internal_with_head_admins(store)

    with pytest.raises(PermissionDeniedError):
        await member_service.remove_member(
            _db(), caller, second_head.membership_id, "takeover"
        )


@pytest.mark.asyncio
async def test_co_admin_cannot_lock_another_co_admin_account(
    store: IdentityStore,
) -> None:
    """A CO_ADMIN cannot manage admins, so locking a CO_ADMIN account is 403."""
    internal, _second_head, _co_admin, caller = _internal_with_head_admins(store)
    other_co_admin_user = store.add_user()
    store.add_membership(other_co_admin_user, internal, roles=(UserRole.CO_ADMIN,))

    with pytest.raises(PermissionDeniedError):
        await account_service.lock_user(
            _db(), caller, other_co_admin_user.user_id, "takeover"
        )


@pytest.mark.asyncio
async def test_force_handover_is_refused_while_the_org_admin_is_active(
    store: IdentityStore,
) -> None:
    """ID-33: our admin appoints with force only when the ORG_ADMIN is gone."""
    internal = store.add_organization(is_internal=True)
    co_admin = store.add_membership(
        store.add_user(), internal, roles=(UserRole.CO_ADMIN,)
    )
    customer = store.add_organization(is_internal=False)
    store.add_membership(store.add_user(), customer, roles=(UserRole.ORG_ADMIN,))
    target = store.add_membership(store.add_user(), customer)

    with pytest.raises((PermissionDeniedError, ConflictError)):
        await member_service.handover_org_admin(
            _db(),
            principal_of(co_admin, internal, UserRole.CO_ADMIN),
            customer.organization_id,
            AdminHandoverRequest(
                to_membership_id=target.membership_id, reason="x", force=True
            ),
        )


@pytest.mark.asyncio
async def test_the_last_head_admin_guard_counts_only_active_holders(
    store: IdentityStore,
) -> None:
    """A locked HEAD_ADMIN is not a spare: removing the only usable one is refused (RV-ID7)."""
    internal = store.add_organization(is_internal=True)
    usable = store.add_membership(
        store.add_user(), internal, roles=(UserRole.HEAD_ADMIN,)
    )
    store.add_membership(
        store.add_user(),
        internal,
        roles=(UserRole.HEAD_ADMIN,),
        status=MembershipStatus.LOCKED,
    )
    caller = principal_of(usable, internal, UserRole.HEAD_ADMIN)

    with pytest.raises(RoleConflictError):
        await member_service.revoke_role(
            _db(), caller, usable.membership_id, UserRole.HEAD_ADMIN
        )


def _self_registered_attacker(
    identity_store: IdentityStore,
) -> tuple[OrganizationModel, Principal]:
    """Build a guest's personal organization where the guest is ORG_ADMIN."""
    personal = identity_store.add_organization(is_internal=False)
    personal.display_name = "Your bank account is frozen, call 1900-0000"
    guest = identity_store.add_membership(
        identity_store.add_user(),
        personal,
        roles=(UserRole.ORG_ADMIN, UserRole.DRIVER),
    )
    return personal, principal_of(guest, personal, UserRole.ORG_ADMIN, UserRole.DRIVER)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RV-ID2: invitation SMS to an existing account has no send limit",
)
@pytest.mark.asyncio
async def test_repeated_invitation_resends_stay_within_the_daily_sms_limit(
    store: IdentityStore,
) -> None:
    """Resending an invitation is rate limited like any other SMS to a number."""
    victim = store.add_user(phone_number="+84907777777")
    personal, attacker = _self_registered_attacker(store)
    member_response = await member_service.invite_member(
        _db(),
        attacker,
        personal.organization_id,
        MemberInviteRequest(phone_number="0907777777", full_name="Anyone"),
    )

    for _attempt in range(settings.IDENTITY_OTP_MAX_PER_PHONE_PER_DAY + 10):
        try:
            await member_service.resend_invitation(
                _db(), attacker, personal.organization_id, member_response.membership_id
            )
        except TooManyRequestsError:
            pass

    to_victim = [sms for sms in _sent(store) if sms[0] == victim.phone_number]
    assert len(to_victim) <= settings.IDENTITY_OTP_MAX_PER_PHONE_PER_DAY


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RV-ID2: inviting a phone returns the account's real name/e-mail",
)
@pytest.mark.asyncio
async def test_inviting_an_existing_account_does_not_reveal_its_name_or_email(
    store: IdentityStore,
) -> None:
    """A not-yet-accepted invite must not leak who owns the phone number."""
    store.add_user(
        phone_number="+84907777777",
        full_name="Real Victim Name",
        email="victim@example.vn",
    )
    personal, attacker = _self_registered_attacker(store)

    member_response = await member_service.invite_member(
        _db(),
        attacker,
        personal.organization_id,
        MemberInviteRequest(phone_number="0907777777", full_name="Typed Name"),
    )

    response_text = member_response.model_dump_json()
    assert "Real Victim Name" not in response_text
    assert "victim@example.vn" not in response_text


@pytest.mark.xfail(
    strict=True,
    raises=UserConflictError,
    reason="RV-ID3: an invite squats the number; its owner cannot sign up",
)
@pytest.mark.asyncio
async def test_sign_up_code_is_sent_to_an_invited_number_without_a_password(
    store: IdentityStore,
) -> None:
    """An INVITED account with no password does not block the owner's sign-up."""
    store.add_user(status=UserStatus.INVITED, phone_number="+84907777777")

    await account_service.send_one_time_code(
        _db(),
        OneTimeCodeSendRequest(
            phone_number="0907777777", purpose=OneTimeCodePurpose.SIGN_UP
        ),
    )

    assert [sms[0] for sms in _sent(store)] == ["+84907777777"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RV-ID5: '+84 0901...' normalizes to a second login ID +840901...",
)
def test_country_code_followed_by_trunk_zero_normalizes_to_the_same_number() -> None:
    """``+84 0901234567`` is the same phone as ``0901234567`` (ID-08)."""
    assert normalize_phone_number("+84 090 123 4567") == "+84901234567"
    assert normalize_phone_number("(+84) 0901234567") == "+84901234567"


@pytest.mark.xfail(
    strict=True,
    raises=DID_NOT_RAISE,
    reason="RV-ID6: a phone change needs no password (session-only takeover)",
)
def test_phone_change_request_requires_the_current_password() -> None:
    """Moving the login number to another SIM asks for the current password."""
    with pytest.raises(ValidationError):
        PhoneChangeRequest.model_validate({"new_phone_number": "0907654321"})


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RV-ID6: confirming a phone change keeps every other session",
)
@pytest.mark.asyncio
async def test_confirming_a_phone_change_ends_the_other_sessions(
    store: IdentityStore,
) -> None:
    """After the login number moves, other devices must log in again."""
    user_record = store.add_user()
    session_record, _refresh_token = store.add_session(user_record, None)
    store.add_session(user_record, None)
    code_id = uuid4()
    store.codes.append(
        OneTimeCodeModel(
            one_time_code_id=code_id,
            purpose=OneTimeCodePurpose.PHONE_CHANGE.value,
            user_id=user_record.user_id,
            phone_number="+84907654321",
            code_hash=hash_one_time_code(code_id, "123456"),
            created_at=utc_now(),
            expires_at=utc_now() + timedelta(minutes=5),
            failed_attempt_count=0,
        )
    )
    session_identity = SessionIdentity(
        user_id=user_record.user_id,
        session_id=session_record.user_session_id,
        organization_id=None,
    )

    await account_service.confirm_phone_change(
        _db(), session_identity, PhoneChangeConfirmRequest(code="123456"), None
    )

    assert user_record.phone_number == "+84907654321"
    assert list(store.sessions) == [session_record.user_session_id]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RV-ID6: wrong current passwords on /password/change are unlimited",
)
@pytest.mark.asyncio
async def test_wrong_current_password_counts_towards_the_login_lockout(
    store: IdentityStore,
) -> None:
    """Guessing the current password through a session is counted like a login."""
    user_record = store.add_user(password="right password")
    session_record, _refresh_token = store.add_session(user_record, None)
    session_identity = SessionIdentity(
        user_id=user_record.user_id,
        session_id=session_record.user_session_id,
        organization_id=None,
    )

    with pytest.raises(CurrentPasswordIncorrectError):
        await account_service.change_password(
            _db(),
            session_identity,
            PasswordChangeRequest(
                current_password="guess", new_password="another password"
            ),
            None,
        )

    assert store.user_states[user_record.user_id].failed_login_count == 1


@pytest.mark.xfail(
    strict=True,
    raises=DID_NOT_RAISE,
    reason="RV-ID8: a blank reason passes the schema, then 500s in the trigger",
)
def test_blank_reason_is_rejected_by_the_request_schemas() -> None:
    """A reason of spaces is refused at validation (422), never reaching the DB."""
    with pytest.raises(ValidationError):
        MembershipReasonRequest.model_validate({"reason": "   "})
    with pytest.raises(ValidationError):
        UserLockRequest.model_validate({"reason": "   "})


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="RV-ID11: locking an account skips the membership-end hooks (DR-10)",
)
@pytest.mark.asyncio
async def test_locking_an_account_runs_the_membership_end_hooks(
    store: IdentityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked account's driver must not stay checked in to a truck."""
    customer = store.add_organization(is_internal=False)
    driver_user = store.add_user()
    driver_membership = store.add_membership(
        driver_user, customer, roles=(UserRole.DRIVER,)
    )
    internal = store.add_organization(is_internal=True)
    head_admin = store.add_membership(
        store.add_user(), internal, roles=(UserRole.HEAD_ADMIN,)
    )
    hook_calls: list[tuple[UUID, MembershipEndKind]] = []

    async def record_hook(
        _db: object, *, membership_id: UUID, kind: MembershipEndKind, **_rest: object
    ) -> None:
        hook_calls.append((membership_id, kind))

    monkeypatch.setattr(member_service, "_membership_end_hooks", [record_hook])

    await account_service.lock_user(
        _db(),
        principal_of(head_admin, internal, UserRole.HEAD_ADMIN),
        driver_user.user_id,
        "fraud",
    )

    assert hook_calls == [(driver_membership.membership_id, MembershipEndKind.LOCKED)]


@pytest.mark.xfail(
    strict=True,
    raises=OrgAdminProtectedError,
    reason="RV-ID12: a pending first-admin invitation cannot be cancelled",
)
@pytest.mark.asyncio
async def test_internal_admin_can_cancel_a_pending_first_admin_invitation(
    store: IdentityStore,
) -> None:
    """A mistyped first administrator's invitation can be withdrawn by our staff."""
    internal = store.add_organization(is_internal=True)
    co_admin = store.add_membership(
        store.add_user(), internal, roles=(UserRole.CO_ADMIN,)
    )
    customer = store.add_organization(is_internal=False)
    pending_admin = store.add_membership(
        store.add_user(status=UserStatus.INVITED),
        customer,
        roles=(UserRole.ORG_ADMIN,),
        status=MembershipStatus.INVITED,
    )

    await member_service.remove_member(
        _db(),
        principal_of(co_admin, internal, UserRole.CO_ADMIN),
        pending_admin.membership_id,
        "Wrong phone number",
    )

    assert pending_admin.left_at is not None
    assert (
        await store.find_active_org_admin_assignment(None, customer.organization_id)
        is None
    )
