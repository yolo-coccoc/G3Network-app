"""Pydantic schemas of the identity domain's HTTP API.

Phone numbers are normalized to E.164 on input (`security.normalize_phone_number`)
so every service compares the stored form. Passwords, tokens and one-time
codes appear only in request bodies and token responses, never in any other
response.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.domains.identity.security import normalize_phone_number
from app.domains.identity.types import (
    LegalDocumentPurpose,
    OneTimeCodePurpose,
    OrganizationLegalForm,
    OrganizationStatus,
    SessionPlatform,
    UserRole,
)
from app.libs.common.config import settings

PhoneNumber = Annotated[
    str,
    Field(min_length=8, max_length=24, description="Phone number; E.164 or 0xxxxxxxxx"),
    AfterValidator(normalize_phone_number),
]
Password = Annotated[str, Field(min_length=1, max_length=128)]
NewPassword = Annotated[
    str,
    Field(
        min_length=settings.IDENTITY_PASSWORD_MIN_LENGTH,
        max_length=128,
        description="New password",
    ),
]
# A plain pattern instead of `EmailStr`: that type needs the third-party
# `email-validator` package, which was not approved.
EmailAddress = Annotated[
    str,
    Field(
        min_length=3,
        max_length=255,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="E-mail address",
    ),
]
Reason = Annotated[
    str, Field(min_length=1, max_length=200, description="Why (kept in the history)")
]


class _ResponseModel(BaseModel):
    """Base for responses built straight from ORM rows."""

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class OneTimeCodeSendRequest(BaseModel):
    """Ask for an SMS code (ACC-05)."""

    phone_number: PhoneNumber
    purpose: OneTimeCodePurpose = Field(
        ..., description="SIGN_UP, INVITE or PASSWORD_RESET"
    )


class OneTimeCodeSendResponse(BaseModel):
    """Answer to a code request; identical whether or not a code was sent."""

    expires_at: datetime = Field(..., description="When the code stops working")


class DeviceFields(BaseModel):
    """The device a login comes from (ACC-04, ACC-16)."""

    platform: SessionPlatform
    app_version: str | None = Field(default=None, max_length=20)
    device_label: str | None = Field(default=None, max_length=100)
    push_token: str | None = Field(default=None, min_length=1, max_length=512)


class SignUpRequest(DeviceFields):
    """Self-registration of an individual with a phone code (ACC-07)."""

    phone_number: PhoneNumber
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    full_name: str = Field(..., min_length=1, max_length=100)
    email: EmailAddress | None = None
    password: NewPassword


class InvitationAcceptRequest(DeviceFields):
    """An invited person sets a password and joins (ACC-09, ACC-10)."""

    phone_number: PhoneNumber
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    password: NewPassword


class LoginRequest(DeviceFields):
    """Login by phone number and password (ACC-04)."""

    phone_number: PhoneNumber
    password: Password


class RefreshRequest(BaseModel):
    """Exchange a refresh token for a new pair."""

    refresh_token: str = Field(..., min_length=1, max_length=256)


class LogoutRequest(BaseModel):
    """Log out this device, or every device."""

    all_devices: bool = False


class SwitchOrganizationRequest(BaseModel):
    """Pick the organization the session acts for."""

    organization_id: UUID


class PasswordResetRequest(BaseModel):
    """Set a new password with a PASSWORD_RESET code (ACC-06)."""

    phone_number: PhoneNumber
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: NewPassword


class PasswordChangeRequest(BaseModel):
    """Change the password of a logged-in person (ACC-06)."""

    current_password: Password
    new_password: NewPassword


class PhoneChangeRequest(BaseModel):
    """Start a phone-number change; a code goes to the new number (ACC-06)."""

    new_phone_number: PhoneNumber


class PhoneChangeConfirmRequest(BaseModel):
    """Finish a phone-number change with the code sent to the new number."""

    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class PushTokenRequest(BaseModel):
    """Register or replace the Firebase token of this device (ACC-16)."""

    push_token: str = Field(..., min_length=1, max_length=512)


class ProfileUpdateRequest(BaseModel):
    """Change the caller's own name or e-mail; a null field is left unchanged."""

    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailAddress | None = None


class MembershipSummaryResponse(BaseModel):
    """One of the caller's memberships, for the organization picker."""

    membership_id: UUID
    organization_id: UUID
    organization_name: str
    organization_status: str
    is_internal: bool
    status: str
    roles: list[UserRole]


class TokenResponse(BaseModel):
    """Result of a login, sign-up, invitation or refresh."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds")
    refresh_token: str
    session_id: UUID
    organization_id: UUID | None = Field(
        default=None, description="Organization the session acts for; null until picked"
    )
    memberships: list[MembershipSummaryResponse]


class UserResponse(_ResponseModel):
    """A user account."""

    user_id: UUID
    phone_number: str
    email: str | None
    full_name: str
    status: str
    status_reason: str | None
    created_at: datetime


class UserListResponse(BaseModel):
    """Paginated user accounts (internal staff only)."""

    items: list[UserResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)


class MeResponse(BaseModel):
    """Who is calling and what they may do (ACC-14)."""

    user: UserResponse
    session_id: UUID
    organization_id: UUID | None
    is_internal: bool
    roles: list[UserRole]
    features: list[str]
    memberships: list[MembershipSummaryResponse]


class SessionResponse(BaseModel):
    """One login of the caller, for the "my devices" list (ACC-16)."""

    user_session_id: UUID
    platform: str
    app_version: str | None
    device_label: str | None
    has_push_token: bool
    organization_id: UUID | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    is_current: bool


class UserLockRequest(BaseModel):
    """Lock or unlock a whole account (internal administrators only)."""

    reason: Reason


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


class FirstAdminRequest(BaseModel):
    """The person who becomes the new organization's ORG_ADMIN (ID-15)."""

    phone_number: PhoneNumber
    full_name: str = Field(..., min_length=1, max_length=100)


class OrganizationCreateRequest(BaseModel):
    """Create a customer or partner organization (ACC-01, ACC-08)."""

    legal_form: OrganizationLegalForm
    display_name: str = Field(..., min_length=1, max_length=200)
    legal_name: str = Field(..., min_length=1, max_length=255)
    tax_code: str | None = Field(default=None, min_length=1, max_length=20)
    address: str | None = Field(default=None, min_length=1, max_length=500)
    is_internal: bool = Field(
        default=False, description="One of our own organizations (HEAD_ADMIN only)"
    )
    first_admin: FirstAdminRequest


class OrganizationUpdateRequest(BaseModel):
    """Edit an organization's profile; a null field is left unchanged."""

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, min_length=1, max_length=255)
    tax_code: str | None = Field(default=None, min_length=1, max_length=20)
    address: str | None = Field(default=None, min_length=1, max_length=500)
    reason: Reason


class OrganizationStatusRequest(BaseModel):
    """Suspend, close or reactivate an organization (ID-06)."""

    status: OrganizationStatus
    reason: Reason


class AccountManagerRequest(BaseModel):
    """Assign (or clear, with null) the account manager (ID-07)."""

    account_manager_id: UUID | None
    reason: Reason


class OrganizationResponse(_ResponseModel):
    """An organization."""

    organization_id: UUID
    is_internal: bool
    legal_form: str
    display_name: str
    legal_name: str
    tax_code: str | None
    address: str | None
    status: str
    status_reason: str | None
    account_manager_id: UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class OrganizationListResponse(BaseModel):
    """Paginated organizations."""

    items: list[OrganizationResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)


class OrganizationSettingsResponse(_ResponseModel):
    """The settings an organization chose for itself (ID-45)."""

    organization_id: UUID
    telemetry_interval_seconds: int
    driving_session_auto_end_minutes: int
    updated_at: datetime


class OrganizationSettingsUpdateRequest(BaseModel):
    """Change settings; a null field is left unchanged."""

    telemetry_interval_seconds: int | None = Field(
        default=None,
        ge=settings.TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS,
        le=settings.TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS,
    )
    driving_session_auto_end_minutes: int | None = Field(default=None, ge=5, le=1440)
    reason: Reason


# ---------------------------------------------------------------------------
# Members and roles
# ---------------------------------------------------------------------------


class MemberInviteRequest(BaseModel):
    """Invite a person by phone number into an organization (ACC-09, ACC-10)."""

    phone_number: PhoneNumber
    full_name: str = Field(..., min_length=1, max_length=100)
    email: EmailAddress | None = None
    roles: list[UserRole] = Field(default_factory=list, max_length=12)


class MemberResponse(BaseModel):
    """A person in an organization with their roles."""

    membership_id: UUID
    organization_id: UUID
    user_id: UUID
    full_name: str
    phone_number: str
    email: str | None
    user_status: str
    status: str
    status_reason: str | None
    roles: list[UserRole]
    joined_at: datetime | None
    left_at: datetime | None
    created_at: datetime


class MemberListResponse(BaseModel):
    """Paginated members."""

    items: list[MemberResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)


class MembershipReasonRequest(BaseModel):
    """A reason for locking a member."""

    reason: Reason


class RoleGrantRequest(BaseModel):
    """Grant a role to a member."""

    role: UserRole


class RoleAssignmentResponse(BaseModel):
    """A role held by a membership."""

    membership_id: UUID
    role: UserRole
    granted_at: datetime
    granted_by: UUID | None
    revoked_at: datetime | None


class AdminHandoverRequest(BaseModel):
    """Hand the ORG_ADMIN role to another active member (ID-33)."""

    to_membership_id: UUID
    reason: Reason
    force: bool = Field(
        default=False,
        description="Internal administrator appoints when the ORG_ADMIN is gone",
    )


# ---------------------------------------------------------------------------
# Legal documents, consent and audit
# ---------------------------------------------------------------------------


class LegalDocumentCreateRequest(BaseModel):
    """Publish a final legal text (ID-39)."""

    purpose: LegalDocumentPurpose
    version: str = Field(..., min_length=1, max_length=20)
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)


class LegalDocumentSummaryResponse(_ResponseModel):
    """A legal document without its text, for lists."""

    legal_document_id: UUID
    purpose: str
    version: str
    title: str
    created_at: datetime


class LegalDocumentResponse(LegalDocumentSummaryResponse):
    """A legal document with its full text."""

    content: str


class ConsentCreateRequest(BaseModel):
    """Accept a legal document version (ACC-17)."""

    legal_document_id: UUID
    on_behalf_of_organization: bool = Field(
        default=False,
        description="Accept for the session's organization (data processing agreement)",
    )


class ConsentResponse(_ResponseModel):
    """A recorded acceptance."""

    user_consent_id: UUID
    user_id: UUID
    organization_id: UUID | None
    legal_document_id: UUID
    accepted_at: datetime


class AccessAuditLogResponse(_ResponseModel):
    """One audit entry."""

    access_audit_log_id: int
    occurred_at: datetime
    user_id: UUID | None
    organization_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    details: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None

    @field_validator("ip_address", mode="before")
    @classmethod
    def _ip_address_as_text(cls, value: object) -> str | None:
        """Return the INET column value as text whatever driver type it has."""
        return None if value is None else str(value)


class AccessAuditLogListResponse(BaseModel):
    """Paginated audit entries."""

    items: list[AccessAuditLogResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)
