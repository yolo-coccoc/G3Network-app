"""Shared internal enums of the identity domain.

The identity tables store these as plain ``varchar`` columns (the DBML types
them that way, and a new value is then an edit of the allowed list, not a
PostgreSQL ``ALTER TYPE``); the enums name the allowed values for code. A
column holds the member's value, e.g. ``OrganizationStatus.ACTIVE.value``.
"""

import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


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


class MembershipEndKind(str, enum.Enum):
    """How a person's standing in an organization stopped (DR-10).

    Attributes:
        ENDED: The membership ended (removed, or the person left): its job
            profiles leave the system.
        LOCKED: The membership was locked: reversible, so a profile stays but
            the person must not keep working on a truck.
    """

    ENDED = "ENDED"
    LOCKED = "LOCKED"


# A callback another part of the application registers to react, inside the
# same transaction, when a membership ends or is locked. Called as
# ``hook(db_session, membership_id=..., kind=..., acting_user_id=..., reason=...)``.
# identity depends on no domain, so the drivers domain (DR-10) is wired to it
# in ``app/api/membership_end_hooks.py`` instead of being imported here.
MembershipEndHook = Callable[..., Awaitable[None]]


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


@dataclass(frozen=True)
class MembershipPersonReference:
    """A membership joined with the person behind it, for other domains.

    Returned by `resolve_membership_person_reference`; the drivers domain uses
    it to check that a profile's membership exists and to show the person's
    name and phone number, which live on the user (DR-09).

    Attributes:
        membership_id: Internal ID of the membership.
        organization_id: The organization the person belongs to.
        user_id: The person.
        full_name: The person's full name.
        phone_number: The person's phone number (the login ID).
        membership_status: Value of `MembershipStatus`.
        user_status: Value of `UserStatus`.
        left_at: When the person left the organization, `None` while a member.
    """

    membership_id: UUID
    organization_id: UUID
    user_id: UUID
    full_name: str
    phone_number: str
    membership_status: str
    user_status: str
    left_at: datetime | None


# ---------------------------------------------------------------------------
# Role groups and the role -> feature table (ACC-14, BL-16)
# ---------------------------------------------------------------------------

# Roles that exist only in an internal organization (ID-12, ID-40).
INTERNAL_ONLY_ROLES = frozenset({UserRole.HEAD_ADMIN, UserRole.CO_ADMIN})

# Our two administrator roles; most internal staff actions need one of them.
INTERNAL_ADMIN_ROLES = frozenset({UserRole.HEAD_ADMIN, UserRole.CO_ADMIN})

# Roles that may create and onboard a customer organization (ACC-01, ACC-08).
ORGANIZATION_CREATOR_ROLES = frozenset(
    {UserRole.HEAD_ADMIN, UserRole.CO_ADMIN, UserRole.SALES}
)

# Keys are the feature codes of `docs/product/features/features.yaml` that the
# identity domain serves; a role holds a feature when it is in the `users`
# list of that feature. The per-role list of every other domain's features is
# decided in the permission-granting step (ID-44), so only these exist today.
# Until subscription plans exist (BL-16) a customer organization gets every
# feature of its roles, and an internal user the whole role: features = role.
ROLE_FEATURES: dict[UserRole, frozenset[str]] = {
    UserRole.HEAD_ADMIN: frozenset(
        {"ACC-01", "ACC-03", "ACC-13", "ACC-18", "ACC-20", "ACC-22"}
    ),
    UserRole.CO_ADMIN: frozenset(
        {
            "ACC-01",
            "ACC-02",
            "ACC-03",
            "ACC-09",
            "ACC-12",
            "ACC-13",
            "ACC-17",
            "ACC-18",
            "ACC-22",
        }
    ),
    UserRole.ORG_ADMIN: frozenset(
        {"ACC-04", "ACC-05", "ACC-06", "ACC-09", "ACC-12", "ACC-13", "ACC-17"}
    ),
    UserRole.SALES: frozenset({"ACC-01", "ACC-02", "ACC-08"}),
    UserRole.ACCOUNTANT: frozenset(),
    UserRole.CUSTOMER_CARE: frozenset({"ACC-04", "ACC-22"}),
    UserRole.OPERATIONS: frozenset({"ACC-04"}),
    UserRole.MAINTENANCE: frozenset(),
    UserRole.WARRANTY: frozenset(),
    UserRole.FLEET_MANAGER: frozenset({"ACC-04", "ACC-06", "ACC-10", "ACC-16"}),
    UserRole.DISPATCHER: frozenset({"ACC-16"}),
    UserRole.DRIVER: frozenset(
        {"ACC-04", "ACC-05", "ACC-06", "ACC-07", "ACC-10", "ACC-16", "ACC-17"}
    ),
    UserRole.TECHNICIAN: frozenset(),
}


# Roles per feature of the other domains, copied from the `users` lists of
# `docs/product/features/features.yaml` (SYSTEM and EXTERNAL dropped: they are
# no logged-in caller). Routers never write role names inline; they ask
# `roles_for(<feature codes>)` so the catalog stays the one source (WP2b).
FEATURE_ROLES: dict[str, frozenset[UserRole]] = {
    "VEH-01": frozenset(
        {UserRole.OPERATIONS, UserRole.FLEET_MANAGER, UserRole.ORG_ADMIN}
    ),
    "VEH-02": frozenset({UserRole.OPERATIONS, UserRole.SALES}),
    "VEH-03": frozenset({UserRole.OPERATIONS}),
    "VEH-05": frozenset({UserRole.OPERATIONS}),
    "VEH-06": frozenset(
        {UserRole.OPERATIONS, UserRole.CUSTOMER_CARE, UserRole.WARRANTY}
    ),
    "BAT-01": frozenset({UserRole.OPERATIONS, UserRole.MAINTENANCE, UserRole.WARRANTY}),
    "WAR-01": frozenset(
        {UserRole.WARRANTY, UserRole.OPERATIONS, UserRole.FLEET_MANAGER}
    ),
    "DEV-01": frozenset({UserRole.OPERATIONS}),
    "DEV-02": frozenset({UserRole.OPERATIONS}),
    "DEV-03": frozenset(),
    "DEV-04": frozenset({UserRole.OPERATIONS}),
    "DEV-05": frozenset({UserRole.OPERATIONS}),
    "DEV-06": frozenset({UserRole.OPERATIONS}),
    "DEV-07": frozenset({UserRole.OPERATIONS}),
    "DEV-08": frozenset({UserRole.OPERATIONS}),
    "MON-01": frozenset(),
    "MON-02": frozenset(
        {
            UserRole.DRIVER,
            UserRole.FLEET_MANAGER,
            UserRole.DISPATCHER,
            UserRole.CUSTOMER_CARE,
        }
    ),
    "MON-03": frozenset({UserRole.DRIVER}),
    "MON-04": frozenset({UserRole.DRIVER, UserRole.FLEET_MANAGER}),
    "MON-05": frozenset({UserRole.DRIVER, UserRole.FLEET_MANAGER, UserRole.OPERATIONS}),
    "MON-06": frozenset(
        {UserRole.DRIVER, UserRole.FLEET_MANAGER, UserRole.MAINTENANCE}
    ),
    "MON-07": frozenset(
        {UserRole.FLEET_MANAGER, UserRole.WARRANTY, UserRole.MAINTENANCE}
    ),
    "MON-08": frozenset(
        {UserRole.FLEET_MANAGER, UserRole.MAINTENANCE, UserRole.WARRANTY}
    ),
    "MON-09": frozenset({UserRole.FLEET_MANAGER, UserRole.DISPATCHER}),
    "MON-10": frozenset(
        {UserRole.FLEET_MANAGER, UserRole.DISPATCHER, UserRole.CUSTOMER_CARE}
    ),
    "MON-11": frozenset({UserRole.DRIVER, UserRole.FLEET_MANAGER}),
    "MON-12": frozenset({UserRole.FLEET_MANAGER}),
    "MON-13": frozenset({UserRole.DRIVER, UserRole.FLEET_MANAGER}),
    "MON-14": frozenset({UserRole.FLEET_MANAGER, UserRole.DRIVER, UserRole.ACCOUNTANT}),
    "MON-15": frozenset({UserRole.FLEET_MANAGER}),
    "MON-16": frozenset({UserRole.FLEET_MANAGER}),
    "MON-17": frozenset({UserRole.OPERATIONS}),
    "MON-18": frozenset({UserRole.DRIVER}),
    "MON-19": frozenset({UserRole.FLEET_MANAGER}),
    "DRV-01": frozenset({UserRole.FLEET_MANAGER, UserRole.ORG_ADMIN}),
    "DRV-02": frozenset({UserRole.DRIVER, UserRole.FLEET_MANAGER, UserRole.DISPATCHER}),
    "DRV-03": frozenset({UserRole.DISPATCHER, UserRole.FLEET_MANAGER, UserRole.DRIVER}),
    "DRV-04": frozenset({UserRole.DRIVER}),
    "DRV-05": frozenset({UserRole.DRIVER}),
    "DRV-06": frozenset({UserRole.FLEET_MANAGER}),
    "DRV-07": frozenset({UserRole.FLEET_MANAGER, UserRole.DRIVER}),
    "FLT-01": frozenset({UserRole.FLEET_MANAGER, UserRole.ORG_ADMIN}),
    "FLT-02": frozenset({UserRole.FLEET_MANAGER}),
    "FLT-03": frozenset({UserRole.ORG_ADMIN}),
    "FLT-04": frozenset({UserRole.FLEET_MANAGER, UserRole.DISPATCHER}),
    "FLT-05": frozenset({UserRole.FLEET_MANAGER, UserRole.DISPATCHER}),
    "FLT-06": frozenset({UserRole.FLEET_MANAGER, UserRole.ORG_ADMIN}),
    "FLT-07": frozenset({UserRole.FLEET_MANAGER, UserRole.ACCOUNTANT}),
    "FLT-08": frozenset({UserRole.FLEET_MANAGER, UserRole.WARRANTY}),
    "STN-01": frozenset({UserRole.OPERATIONS}),
    "STN-02": frozenset({UserRole.OPERATIONS}),
    "STN-03": frozenset(),
    "STN-04": frozenset({UserRole.OPERATIONS, UserRole.DRIVER}),
    "STN-05": frozenset({UserRole.OPERATIONS}),
    "STN-06": frozenset({UserRole.DRIVER, UserRole.DISPATCHER}),
    "STN-07": frozenset({UserRole.DRIVER}),
    "STN-08": frozenset({UserRole.DRIVER, UserRole.OPERATIONS}),
    "STN-09": frozenset({UserRole.DRIVER, UserRole.DISPATCHER}),
    "STN-10": frozenset({UserRole.OPERATIONS}),
    "STN-11": frozenset({UserRole.OPERATIONS}),
    "STN-12": frozenset({UserRole.OPERATIONS, UserRole.SALES}),
    "STN-13": frozenset({UserRole.OPERATIONS}),
    "STN-14": frozenset({UserRole.OPERATIONS, UserRole.HEAD_ADMIN}),
    "STN-15": frozenset({UserRole.OPERATIONS}),
    "CHG-01": frozenset({UserRole.DRIVER}),
    "CHG-02": frozenset({UserRole.OPERATIONS, UserRole.CUSTOMER_CARE}),
    "CHG-03": frozenset({UserRole.DRIVER, UserRole.ACCOUNTANT}),
    "CHG-04": frozenset({UserRole.DRIVER, UserRole.FLEET_MANAGER, UserRole.ACCOUNTANT}),
    "CHG-05": frozenset({UserRole.ACCOUNTANT, UserRole.OPERATIONS}),
    "CHG-06": frozenset({UserRole.ACCOUNTANT, UserRole.OPERATIONS}),
    "CHG-07": frozenset(),
    "CHG-08": frozenset({UserRole.DRIVER, UserRole.FLEET_MANAGER}),
    "PAY-01": frozenset({UserRole.SALES, UserRole.HEAD_ADMIN}),
    "PAY-02": frozenset({UserRole.SALES, UserRole.HEAD_ADMIN}),
    "PAY-03": frozenset({UserRole.SALES, UserRole.ACCOUNTANT, UserRole.ORG_ADMIN}),
    "PAY-04": frozenset({UserRole.SALES, UserRole.ORG_ADMIN}),
    "PAY-05": frozenset({UserRole.DRIVER, UserRole.ORG_ADMIN}),
    "PAY-06": frozenset({UserRole.DRIVER}),
    "PAY-07": frozenset({UserRole.DRIVER, UserRole.FLEET_MANAGER, UserRole.ACCOUNTANT}),
    "PAY-08": frozenset({UserRole.ACCOUNTANT, UserRole.FLEET_MANAGER}),
    "PAY-09": frozenset({UserRole.OPERATIONS, UserRole.DRIVER}),
    "PAY-10": frozenset({UserRole.ACCOUNTANT}),
    "PAY-11": frozenset({UserRole.ACCOUNTANT, UserRole.DRIVER}),
    "PAY-12": frozenset({UserRole.SALES, UserRole.DRIVER}),
    "PAY-13": frozenset({UserRole.HEAD_ADMIN, UserRole.ACCOUNTANT}),
    "NTF-01": frozenset(
        {
            UserRole.DRIVER,
            UserRole.FLEET_MANAGER,
            UserRole.OPERATIONS,
            UserRole.CUSTOMER_CARE,
        }
    ),
    "NTF-02": frozenset({UserRole.DRIVER, UserRole.FLEET_MANAGER}),
    "NTF-03": frozenset({UserRole.DRIVER}),
    "NTF-04": frozenset({UserRole.FLEET_MANAGER, UserRole.ACCOUNTANT}),
    "NTF-05": frozenset({UserRole.ORG_ADMIN}),
    "NTF-06": frozenset(),
    "NTF-07": frozenset({UserRole.CUSTOMER_CARE}),
    "NTF-08": frozenset({UserRole.FLEET_MANAGER, UserRole.OPERATIONS}),
    "SUP-01": frozenset(
        {UserRole.DRIVER, UserRole.FLEET_MANAGER, UserRole.CUSTOMER_CARE}
    ),
    "SUP-02": frozenset({UserRole.DRIVER, UserRole.CUSTOMER_CARE}),
    "SUP-03": frozenset({UserRole.CUSTOMER_CARE}),
    "SUP-04": frozenset({UserRole.CUSTOMER_CARE, UserRole.OPERATIONS}),
    "SUP-05": frozenset({UserRole.CUSTOMER_CARE, UserRole.TECHNICIAN}),
    "SUP-06": frozenset({UserRole.TECHNICIAN}),
    "SUP-07": frozenset({UserRole.DRIVER}),
}

# Roles added to every feature: our two administrators (the whole role set of
# an internal user, ID-44) and the organization administrator, who manages
# everything of their own organization (data scope still limits the reach).
ALWAYS_ALLOWED_ROLES = frozenset(
    {UserRole.HEAD_ADMIN, UserRole.CO_ADMIN, UserRole.ORG_ADMIN}
)


def roles_for(*feature_codes: str) -> frozenset[UserRole]:
    """Return the roles allowed to call an endpoint serving the given features.

    Args:
        *feature_codes: Feature codes of the catalog, e.g. ``"VEH-01"``.

    Returns:
        The union of the features' roles plus `ALWAYS_ALLOWED_ROLES`.

    Raises:
        KeyError: A code is not in `FEATURE_ROLES` (a typo fails at import).
    """
    roles: set[UserRole] = set(ALWAYS_ALLOWED_ROLES)
    for feature_code in feature_codes:
        roles |= FEATURE_ROLES[feature_code]
    return frozenset(roles)


def features_of_roles(roles: frozenset[UserRole]) -> frozenset[str]:
    """Return the feature codes a set of roles grants (ACC-14).

    Args:
        roles: The roles a member holds.

    Returns:
        The union of the features of every role.
    """
    features: set[str] = set()
    for role in roles:
        features |= ROLE_FEATURES[role]
    return frozenset(features)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of a request: a person acting for one organization.

    Built from the bearer token by the `get_current_principal` dependency;
    the roles are those of the person's active membership in the session's
    organization at the time of the request.

    Attributes:
        user_id: The person.
        membership_id: The person's membership in `organization_id`.
        organization_id: The organization the session acts for.
        session_id: The login session of the request.
        roles: Roles held in that membership.
        is_internal: Whether `organization_id` is one of our own organizations,
            which gives data reach across every organization (ID-44).
    """

    user_id: UUID
    membership_id: UUID
    organization_id: UUID
    session_id: UUID
    roles: frozenset[UserRole]
    is_internal: bool

    def has_any_role(self, *roles: UserRole) -> bool:
        """Tell whether the caller holds at least one of the given roles.

        Args:
            *roles: Roles to look for.

        Returns:
            `True` if any of them is held.
        """
        return any(role in self.roles for role in roles)

    def can_access_organization(self, organization_id: UUID) -> bool:
        """Apply the data-reach rule (ACC-15): internal sees all, others their own.

        Args:
            organization_id: The organization that owns the data.

        Returns:
            `True` if the caller may read data owned by that organization.
        """
        return self.is_internal or organization_id == self.organization_id

    @property
    def data_scope(self) -> UUID | None:
        """Organization filter for queries: `None` for internal staff (all).

        Services pass it as ``organization_id`` to repositories; a record of
        another organization then does not exist for the caller (404).
        """
        return None if self.is_internal else self.organization_id

    @property
    def features(self) -> frozenset[str]:
        """Feature codes granted by the caller's roles (role only, BL-16)."""
        return features_of_roles(self.roles)


@dataclass(frozen=True)
class SessionIdentity:
    """A valid login session, before an organization is necessarily picked.

    Attributes:
        user_id: The person logged in.
        session_id: The login session.
        organization_id: Organization shown on the device; `None` until a
            person with several organizations picks one.
    """

    user_id: UUID
    session_id: UUID
    organization_id: UUID | None


@dataclass(frozen=True)
class ClientContext:
    """Where a request came from, copied into audit rows and consents.

    Attributes:
        ip_address: Client IP address as text; `None` when unknown.
        user_agent: The client's `User-Agent` header; `None` when absent.
    """

    ip_address: str | None
    user_agent: str | None


@dataclass(frozen=True)
class OrganizationReference:
    """Minimal view of an organization for other domains.

    Attributes:
        organization_id: Internal ID of the organization.
        display_name: Short name shown in the app and portal.
        is_internal: Whether it is one of our own organizations.
        legal_form: Value of `OrganizationLegalForm`.
        status: Value of `OrganizationStatus`.
    """

    organization_id: UUID
    display_name: str
    is_internal: bool
    legal_form: str
    status: str


@dataclass(frozen=True)
class OrganizationSettingsReference:
    """The settings an organization chose for itself (ID-45), for other domains.

    Attributes:
        organization_id: The organization the settings belong to.
        telemetry_interval_seconds: How often its T-Boxes send telemetry.
        driving_session_auto_end_minutes: Idle time after which a driving
            session ends on its own.
    """

    organization_id: UUID
    telemetry_interval_seconds: int
    driving_session_auto_end_minutes: int
