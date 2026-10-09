"""Shared internal enums of the identity domain.

The identity tables store these as plain ``varchar`` columns (the DBML types
them that way, and a new value is then an edit of the allowed list, not a
PostgreSQL ``ALTER TYPE``); the enums name the allowed values for code. A
column holds the member's value, e.g. ``OrganizationStatus.ACTIVE.value``.
"""

import enum


class OrganizationLegalForm(str, enum.Enum):
    """What kind of legal person an organization is."""

    COMPANY = "COMPANY"
    INDIVIDUAL = "INDIVIDUAL"


class OrganizationStatus(str, enum.Enum):
    """Lifecycle of an organization (a decision, so it has a reason)."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class UserStatus(str, enum.Enum):
    """State of a whole user account, across every organization."""

    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"


class MembershipStatus(str, enum.Enum):
    """Standing of a person in one organization only."""

    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"


class CredentialType(str, enum.Enum):
    """How a user proves who they are (only a password today)."""

    PASSWORD = "PASSWORD"


class SessionPlatform(str, enum.Enum):
    """Where the app of a login session runs; decides the session lifetime."""

    ANDROID = "ANDROID"
    IOS = "IOS"
    WEB = "WEB"


class OneTimeCodePurpose(str, enum.Enum):
    """What an SMS one-time code is for."""

    INVITE = "INVITE"
    SIGN_UP = "SIGN_UP"
    PASSWORD_RESET = "PASSWORD_RESET"
    PHONE_CHANGE = "PHONE_CHANGE"


class LegalDocumentPurpose(str, enum.Enum):
    """Which legal text a document is, and who accepts it."""

    TERMS_OF_SERVICE = "TERMS_OF_SERVICE"
    PRIVACY_POLICY = "PRIVACY_POLICY"
    DATA_PROCESSING_AGREEMENT = "DATA_PROCESSING_AGREEMENT"
    PRIVACY_NOTICE = "PRIVACY_NOTICE"
    LOCATION_TRACKING = "LOCATION_TRACKING"


class UserRole(str, enum.Enum):
    """Job titles: bundles of feature permissions, the same for every organization."""

    HEAD_ADMIN = "HEAD_ADMIN"
    CO_ADMIN = "CO_ADMIN"
    ORG_ADMIN = "ORG_ADMIN"
    SALES = "SALES"
    ACCOUNTANT = "ACCOUNTANT"
    CUSTOMER_CARE = "CUSTOMER_CARE"
    OPERATIONS = "OPERATIONS"
    MAINTENANCE = "MAINTENANCE"
    WARRANTY = "WARRANTY"
    FLEET_MANAGER = "FLEET_MANAGER"
    DISPATCHER = "DISPATCHER"
    DRIVER = "DRIVER"
    TECHNICIAN = "TECHNICIAN"


class AccessAuditAction(str, enum.Enum):
    """What an access audit entry records (data actions and account events)."""

    VIEW = "VIEW"
    EXPORT = "EXPORT"
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGIN_LOCKED = "LOGIN_LOCKED"
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    PHONE_CHANGED = "PHONE_CHANGED"
