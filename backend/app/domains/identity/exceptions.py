"""Business exceptions raised by the identity domain.

Status codes come from the shared bases in `app.libs.common.errors`. The
login and one-time-code failures also derive from `FailureRecordedError`:
they leave a counter behind (failed attempts, lockout) that must survive the
rollback an exception normally causes, so the router commits before it lets
them propagate (see `router.persist_recorded_failure`).
"""

from app.libs.common.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    LockedError,
    NotFoundError,
    PermissionDeniedError,
    TooManyRequestsError,
    UnauthenticatedError,
)


class IdentityError(DomainError):
    """Base exception for identity business-rule failures."""


class FailureRecordedError(IdentityError):
    """Marker: the service stored state (a counter) before raising this, and
    that state must be committed even though the request fails."""


class OrganizationNotFoundError(IdentityError, NotFoundError):
    """The organization does not exist, or the caller's data reach excludes it."""


class OrganizationConflictError(IdentityError, ConflictError):
    """The tax code is used by another live organization, or the requested
    status is the current one."""


class OrganizationInvalidError(IdentityError, InvalidInputError):
    """An organization field breaks a business rule (tax code length for the
    legal form, an internal organization that is not a company, ...)."""


class OrganizationInactiveError(FailureRecordedError, PermissionDeniedError):
    """The organization is SUSPENDED or CLOSED, so its members cannot log in."""


class AccountManagerInvalidError(IdentityError, InvalidInputError):
    """The proposed account manager is not an active SALES user of an internal
    organization (ID-07)."""


class UserNotFoundError(IdentityError, NotFoundError):
    """The user does not exist."""


class UserConflictError(IdentityError, ConflictError):
    """The phone number or e-mail already belongs to a live account, or the
    account is already in the requested state."""


class AccountLockedError(FailureRecordedError, PermissionDeniedError):
    """The account is LOCKED by an administrator (ID-08)."""


class InvalidCredentialsError(FailureRecordedError, UnauthenticatedError):
    """Wrong phone number or password (HTTP 401, deliberately one message)."""


class LoginLockedError(FailureRecordedError, LockedError):
    """Too many wrong passwords: logins are refused for a short while (ID-23)."""


class OrganizationNotSelectedError(IdentityError, PermissionDeniedError):
    """The session has no organization yet: a person with several
    organizations must pick one first (`POST /auth/organization`)."""


class OneTimeCodePurposeError(IdentityError, InvalidInputError):
    """The purpose cannot be requested this way (a phone change needs a login)."""


class SessionInvalidError(IdentityError, UnauthenticatedError):
    """The access or refresh token is missing, malformed, expired or its
    session ended."""


class SessionNotFoundError(IdentityError, NotFoundError):
    """The login session does not exist or belongs to someone else."""


class WeakPasswordError(IdentityError, InvalidInputError):
    """The new password is too short, or was used recently (ID-31)."""


class CurrentPasswordIncorrectError(FailureRecordedError, InvalidInputError):
    """The current password typed to authorise a password or phone change is
    wrong; the wrong guess is counted towards the login lockout (RV-ID6)."""


class InvalidOneTimeCodeError(FailureRecordedError, InvalidInputError):
    """The code is wrong, expired, used or dead after too many attempts."""


class OneTimeCodeRateLimitError(IdentityError, TooManyRequestsError):
    """A code was requested too soon after the previous one, or the phone
    number reached its daily limit (SMS-pumping guard, ID-16)."""


class InvitationNotFoundError(IdentityError, NotFoundError):
    """No invitation is waiting for this phone number."""


class MembershipNotFoundError(IdentityError, NotFoundError):
    """The membership does not exist, or the caller's data reach excludes it."""


class MembershipConflictError(IdentityError, ConflictError):
    """The person is already a member, or the membership is not in a state that
    allows the change (accept, resend, lock, unlock, leave)."""


class OrgAdminProtectedError(IdentityError, ConflictError):
    """The organization's ORG_ADMIN cannot be locked, removed or have the role
    revoked: hand it over first (ID-33)."""


class RoleNotAllowedError(IdentityError, InvalidInputError):
    """The role cannot be held in this organization or granted by this caller
    (HEAD_ADMIN / CO_ADMIN only in internal organizations, ID-40)."""


class RoleConflictError(IdentityError, ConflictError):
    """The role is already held, is not held, or is the last of its kind."""


class AdminHandoverInvalidError(IdentityError, ConflictError):
    """The handover target is not an active member, or already the ORG_ADMIN."""


class AccessDeniedError(IdentityError, PermissionDeniedError):
    """The caller's role or data reach does not allow the action (HTTP 403)."""


class LegalDocumentNotFoundError(IdentityError, NotFoundError):
    """The legal document does not exist."""


class LegalDocumentConflictError(IdentityError, ConflictError):
    """The purpose and version were already published, or the version accepted
    is no longer the current one."""


class AuditLogInvalidError(IdentityError, InvalidInputError):
    """An audit-log search or entry breaks a rule (range too long, an export
    without a reason)."""
