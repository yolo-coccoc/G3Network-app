"""SQLAlchemy ORM models for the identity domain (F-F1).

Scope: the tables of the DBML ``identity`` group. Every foreign key is
``RESTRICT``: identity rows are soft-deleted or closed, never removed. The
change-history tables of the tracked tables here (``organization_history``,
``user_history``, ``membership_history``, ``organization_setting_history``) are
not modelled: the baseline migration creates them with
``app.libs.db.history`` and a database trigger fills them.
Statuses and other closed lists are plain ``varchar`` columns; their allowed
values are the enums in ``types.py``.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class OrganizationModel(Base):
    """A party that people act for: a customer, an internal organization, a partner.

    Change history is on (``organization_history``, DM-25 soft delete).

    Attributes:
        organization_id: Primary key (UUID).
        is_internal: TRUE for our own organization(s); their users see every
            organization. The most security-sensitive column.
        legal_form: ``COMPANY`` or ``INDIVIDUAL`` (``OrganizationLegalForm``).
        display_name: Short name shown in the app and the portal.
        legal_name: Full registered name, printed on invoices.
        tax_code: Tax code for e-invoices; unique among live organizations.
        address: Registered address.
        status: ``ACTIVE`` / ``SUSPENDED`` / ``CLOSED`` (``OrganizationStatus``).
        status_reason: Why the organization is in its status; ``None`` when
            ``ACTIVE``.
        account_manager_id: Our SALES user responsible for the customer.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete time; set exactly when ``status`` is CLOSED.
    """

    __tablename__ = "organizations"

    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    is_internal: Mapped[bool] = mapped_column(nullable=False)
    legal_form: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_manager_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "uq_organizations_active_tax_code",
            "tax_code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # A closed organization is always soft-deleted, and only then (DM-25).
        CheckConstraint(
            "(status = 'CLOSED') = (deleted_at IS NOT NULL)",
            name="ck_organizations_closed_iff_deleted",
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging an organization."""
        return f"<OrganizationModel {self.display_name} ({self.status})>"


class UserModel(Base):
    """A person: one account across every organization they work for.

    Change history is on (``user_history``). The password hash lives in
    ``user_credentials`` so it is never copied into the history.

    Attributes:
        user_id: Primary key (UUID).
        email: Optional e-mail; unique (case-insensitive) among live accounts.
        phone_number: E.164 phone number, the login ID; unique among live
            accounts.
        full_name: The person's full name.
        status: ``INVITED`` / ``ACTIVE`` / ``LOCKED`` (``UserStatus``).
        status_reason: Why the account has its status.
        created_by: User who created the account; ``None`` for self sign-up.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete time; a deleted account is also ``LOCKED``.
    """

    __tablename__ = "users"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "uq_users_active_phone_number",
            "phone_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_users_active_email",
            func.lower(email),
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND email IS NOT NULL"),
        ),
        # A deleted account is always locked (DM-25).
        CheckConstraint(
            "deleted_at IS NULL OR status = 'LOCKED'",
            name="ck_users_deleted_is_locked",
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging a user."""
        return f"<UserModel {self.full_name} ({self.status})>"


class MembershipModel(Base):
    """One person in one organization.

    Roles, fleet limits and job profiles hang off the membership. Change
    history is on (``membership_history``).

    Attributes:
        membership_id: Primary key (UUID).
        organization_id: The organization the person belongs to.
        user_id: The person.
        status: ``INVITED`` / ``ACTIVE`` / ``LOCKED`` (``MembershipStatus``).
        status_reason: Why the membership has its status or why it ended.
        joined_at: When the person accepted; ``None`` while ``INVITED``.
        left_at: When the person left; ``None`` while a member.
        created_by: User who added the person; ``None`` for a personal
            organization created by the person.
        created_at: When the person was added or invited.
        updated_at: Last update time.
    """

    __tablename__ = "memberships"

    membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    left_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        Index(
            "uq_memberships_active",
            "organization_id",
            "user_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
        Index("ix_memberships_user_id", "user_id"),
        # Target of the two-column foreign key from user_role_assignments.
        UniqueConstraint(
            "membership_id", "organization_id", name="uq_memberships_id_organization"
        ),
    )


class UserStateModel(Base):
    """Live activity of a user, recorded by the system (observations only).

    One row per user, created together with the user; never history-tracked.

    Attributes:
        user_id: Primary key and foreign key to the user (1:1).
        last_login_at: Latest successful login; ``None`` if never.
        last_organization_id: Organization the user last acted for.
        failed_login_count: Consecutive failed password attempts.
        login_locked_until: Logins are refused until this time; ``None`` when
            not locked.
        last_active_at: Latest request or action (written at most every 5 min).
    """

    __tablename__ = "user_state"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=True,
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer(), default=0, server_default=text("0"), nullable=False
    )
    login_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserCredentialModel(Base):
    """How a user logs in; kept out of ``users`` so a hash is never in history.

    A password change revokes the old row and adds a new one.

    Attributes:
        user_credential_id: Primary key (UUID).
        user_id: The user the credential belongs to.
        credential_type: ``PASSWORD`` (``CredentialType``).
        secret_hash: One-way hash of the secret, never the secret itself.
        created_at: When the credential was set.
        revoked_at: When it stopped being valid; ``None`` while valid.
    """

    __tablename__ = "user_credentials"

    user_credential_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    credential_type: Mapped[str] = mapped_column(String(20), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "uq_user_credentials_active_type",
            "user_id",
            "credential_type",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


class UserSessionModel(Base):
    """One login of one person on one device or browser (also its push token).

    Ending a session deletes its row. Not history-tracked.

    Attributes:
        user_session_id: Primary key (UUID).
        user_id: The person logged in.
        organization_id: Organization shown on the device; ``None`` until one
            is picked.
        platform: ``ANDROID`` / ``IOS`` / ``WEB`` (``SessionPlatform``).
        app_version: App version on the device; ``None`` for a browser.
        device_label: Readable device name for the "my devices" list.
        refresh_token_hash: One-way hash of the refresh token (unique).
        push_token: Firebase token of the device (unique); ``None`` if none.
        created_at: When the person logged in.
        last_used_at: Last time the session was used.
        expires_at: When the session ends if not used.
    """

    __tablename__ = "user_sessions"

    user_session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=True,
    )
    platform: Mapped[str] = mapped_column(String(10), nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    device_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    refresh_token_hash: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    push_token: Mapped[str | None] = mapped_column(
        String(512), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_expires_at", "expires_at"),
    )


class OneTimeCodeModel(Base):
    """A one-time code sent by SMS to prove a person holds a phone number.

    A short-lived working table: stored only as a hash, works once, dies after
    5 wrong attempts; the permanent record is in ``access_audit_logs``.

    Attributes:
        one_time_code_id: Primary key (UUID).
        purpose: ``INVITE`` / ``INVITE_NOTICE`` / ``SIGN_UP`` /
            ``PASSWORD_RESET`` / ``PHONE_CHANGE`` (``OneTimeCodePurpose``).
        user_id: The user the code is for; ``None`` for ``SIGN_UP``.
        phone_number: Phone number the code was sent to (E.164).
        code_hash: One-way hash of the code or link token.
        issued_by: User who triggered the code; ``None`` when self-requested.
        organization_id: The organization whose invitation the code is for
            (``INVITE`` / ``INVITE_NOTICE``); ``None`` for the other purposes.
        created_at: When the code was sent.
        expires_at: When the code stops working.
        failed_attempt_count: Wrong codes typed against this code.
        used_at: When the code was used; ``None`` while unused.
    """

    __tablename__ = "one_time_codes"

    one_time_code_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    failed_attempt_count: Mapped[int] = mapped_column(
        Integer(), default=0, server_default=text("0"), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_one_time_codes_phone_time", "phone_number", "created_at"),
    )


class LegalDocumentModel(Base):
    """One published version of a legal text; never edited or deleted.

    Attributes:
        legal_document_id: Primary key (UUID).
        purpose: Which text it is (``LegalDocumentPurpose``).
        version: Version label, unique per purpose.
        title: Title shown to the person.
        content: The full text exactly as shown (Vietnamese).
        created_by: HEAD_ADMIN or CO_ADMIN who published this version.
        created_at: When it was published and took effect.
    """

    __tablename__ = "legal_documents"

    legal_document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text(), nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "purpose", "version", name="uq_legal_documents_purpose_version"
        ),
    )


class UserConsentModel(Base):
    """Proof of who accepted which version of which legal text (append-only).

    Attributes:
        user_consent_id: Primary key (UUID).
        user_id: Person who accepted.
        organization_id: Organization on whose behalf it was accepted; ``None``
            for a person's own acceptance.
        legal_document_id: The exact version accepted.
        ip_address: IP address the acceptance came from.
        device_label: Device and app or browser used, as text.
        accepted_at: When it was accepted.
    """

    __tablename__ = "user_consents"

    user_consent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=True,
    )
    legal_document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("legal_documents.legal_document_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ip_address: Mapped[str | None] = mapped_column(INET(), nullable=True)
    device_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index(
            "uq_user_consents_person_legal_document",
            "user_id",
            "legal_document_id",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
        Index(
            "uq_user_consents_organization_legal_document",
            "organization_id",
            "legal_document_id",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
    )


class UserRoleAssignmentModel(Base):
    """A role held by a member of an organization (open/close, kept as history).

    ``organization_id`` is a copy of the membership's organization, kept only
    because the one-ORG_ADMIN unique index needs it on the same row (the one
    DM-24 exception); the two-column foreign key makes a mismatch impossible.

    Attributes:
        user_role_assignment_id: Primary key (UUID).
        organization_id: The membership's organization.
        membership_id: The membership that holds the role.
        role: A job title (``UserRole``).
        granted_at: When the role was granted.
        granted_by: Who granted it; ``None`` when the system did.
        revoked_at: When the role was revoked; ``None`` while held.
        revoked_by: Who revoked it; ``None`` while held or when the system did.
    """

    __tablename__ = "user_role_assignments"

    user_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    membership_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    granted_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "organization_id"],
            ["memberships.membership_id", "memberships.organization_id"],
            ondelete="RESTRICT",
            name="fk_user_role_assignments_membership_organization",
        ),
        ForeignKeyConstraint(
            ["membership_id"],
            ["memberships.membership_id"],
            ondelete="RESTRICT",
        ),
        Index(
            "uq_user_role_assignments_active_role",
            "membership_id",
            "role",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        # Exactly one organization administrator per organization (handover
        # grants the new one and revokes the old one in one transaction).
        Index(
            "uq_user_role_assignments_one_org_admin",
            "organization_id",
            unique=True,
            postgresql_where=text("role = 'ORG_ADMIN' AND revoked_at IS NULL"),
        ),
    )


class AccessAuditLogModel(Base):
    """Append-only record of access to personal data and account security events.

    A TimescaleDB hypertable on ``occurred_at`` (part of the primary key).

    Attributes:
        access_audit_log_id: Auto-increasing ID (BIGINT), with ``occurred_at``
            the primary key.
        occurred_at: When the access happened; hypertable time column.
        user_id: Who accessed the data, or whose account the event is about;
            ``None`` only for a failed login with an unknown phone number.
        organization_id: Organization whose data was accessed.
        action: What happened (``AccessAuditAction``).
        resource_type: Kind of data accessed; ``USER_ACCOUNT`` for account events.
        resource_id: ID of the record accessed, as text.
        details: Action-specific facts as JSON.
        ip_address: IP address the request came from.
        user_agent: Device and app as reported by the client, copied as text.
    """

    __tablename__ = "access_audit_logs"

    access_audit_log_id: Mapped[int] = mapped_column(
        BigInteger(), primary_key=True, autoincrement=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, default=utc_now
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB(), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET(), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OrganizationSettingModel(Base):
    """Settings an organization chooses for itself: one row, one column per setting.

    Created with default values together with the organization. Change
    history is on (``organization_setting_history``).

    Attributes:
        organization_id: Primary key and foreign key to the organization (1:1).
        telemetry_interval_seconds: How often T-Boxes send telemetry (TX-09).
        driving_session_auto_end_minutes: A driving session ends on its own
            after this long without movement (DR-07).
        created_at: Creation time.
        updated_at: When a setting was last changed.
    """

    __tablename__ = "organization_settings"

    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    telemetry_interval_seconds: Mapped[int] = mapped_column(
        Integer(), default=10, server_default=text("10"), nullable=False
    )
    driving_session_auto_end_minutes: Mapped[int] = mapped_column(
        Integer(), default=120, server_default=text("120"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
