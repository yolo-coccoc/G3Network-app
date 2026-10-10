"""Smoke tests for the identity services: rules checked without a database."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.account_service as account_service
import app.domains.identity.audit_service as audit_service
import app.domains.identity.member_service as member_service
import app.domains.identity.organization_service as organization_service
import app.domains.identity.repository as identity_repository
from app.api.main import domain_error_handler
from app.domains.identity.dependencies import require_roles
from app.domains.identity.exceptions import (
    AccessDeniedError,
    AuditLogInvalidError,
    InvalidCredentialsError,
    LoginLockedError,
    OneTimeCodePurposeError,
    OrganizationInvalidError,
    RoleNotAllowedError,
    UserConflictError,
)
from app.domains.identity.models import (
    OrganizationModel,
    UserCredentialModel,
    UserModel,
    UserStateModel,
)
from app.domains.identity.schemas import (
    LoginRequest,
    OneTimeCodeSendRequest,
    OrganizationCreateRequest,
    OrganizationStatusRequest,
)
from app.domains.identity.security import hash_password
from app.domains.identity.types import (
    AccessAuditAction,
    OneTimeCodePurpose,
    OrganizationLegalForm,
    OrganizationStatus,
    Principal,
    SessionPlatform,
    UserRole,
    UserStatus,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.errors import (
    LockedError,
    PermissionDeniedError,
    TooManyRequestsError,
    UnauthenticatedError,
)
from tests.builders import build_organization_record


class FlushSession:
    """Placeholder session whose flush does nothing, for services that flush."""

    async def flush(self) -> None:
        """Pretend to write pending changes."""


def _flush_session() -> AsyncSession:
    """Return a placeholder session typed as an `AsyncSession`."""
    return FlushSession()  # type: ignore[return-value]


def _principal(
    *, is_internal: bool, roles: set[UserRole], organization_id: UUID | None = None
) -> Principal:
    """Build a principal for the permission tests."""
    return Principal(
        user_id=uuid4(),
        membership_id=uuid4(),
        organization_id=organization_id or uuid4(),
        session_id=uuid4(),
        roles=frozenset(roles),
        is_internal=is_internal,
    )


def _user_record(phone_number: str = "+84901234567") -> UserModel:
    """Build an active user."""
    return UserModel(
        user_id=uuid4(),
        phone_number=phone_number,
        full_name="Test User",
        status=UserStatus.ACTIVE.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


@pytest.fixture
def audit_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the account events a service writes instead of inserting them."""
    events: list[dict[str, Any]] = []

    async def record_account_event(_db: AsyncSession, **values: Any) -> None:
        events.append(values)

    monkeypatch.setattr(audit_service, "record_account_event", record_account_event)
    return events


def _install_login_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_record: UserModel | None,
    state_record: UserStateModel,
    password: str = "right password",
) -> None:
    """Fake the repository reads a login performs."""
    credential = UserCredentialModel(
        user_id=user_record.user_id if user_record else uuid4(),
        credential_type="PASSWORD",
        secret_hash=hash_password(password),
    )

    async def find_live_user_by_phone(*_a: Any, **_k: Any) -> UserModel | None:
        return user_record

    async def get_user_state_for_update(*_a: Any, **_k: Any) -> UserStateModel:
        return state_record

    async def find_active_credential(*_a: Any, **_k: Any) -> UserCredentialModel:
        return credential

    monkeypatch.setattr(
        identity_repository, "find_live_user_by_phone", find_live_user_by_phone
    )
    monkeypatch.setattr(
        identity_repository, "get_user_state_for_update", get_user_state_for_update
    )
    monkeypatch.setattr(
        identity_repository, "find_active_credential", find_active_credential
    )


@pytest.mark.asyncio
async def test_login_with_unknown_phone_is_a_plain_401_and_audited(
    monkeypatch: pytest.MonkeyPatch, audit_events: list[dict[str, Any]]
) -> None:
    """An unknown number gives the same error as a wrong password (ACC-04)."""
    _install_login_fakes(
        monkeypatch,
        user_record=None,
        state_record=UserStateModel(user_id=uuid4(), failed_login_count=0),
    )

    with pytest.raises(InvalidCredentialsError):
        await account_service.login(
            _flush_session(),
            LoginRequest(
                phone_number="0901234567",
                password="x",
                platform=SessionPlatform.ANDROID,
            ),
            None,
        )

    assert audit_events[0]["action"] is AccessAuditAction.LOGIN_FAILED
    assert audit_events[0]["details"] == {"failure": "UNKNOWN_PHONE"}


@pytest.mark.asyncio
async def test_repeated_wrong_passwords_lock_logins_for_a_while(
    monkeypatch: pytest.MonkeyPatch,
    audit_events: list[dict[str, Any]],
) -> None:
    """After the configured failures the account answers 423 (ID-23)."""
    user_record = _user_record()
    state_record = UserStateModel(user_id=user_record.user_id, failed_login_count=0)
    _install_login_fakes(
        monkeypatch, user_record=user_record, state_record=state_record
    )
    monkeypatch.setattr(settings, "IDENTITY_LOGIN_MAX_FAILED_ATTEMPTS", 2)
    login_request = LoginRequest(
        phone_number="0901234567", password="wrong", platform=SessionPlatform.ANDROID
    )

    with pytest.raises(InvalidCredentialsError):
        await account_service.login(_flush_session(), login_request, None)
    with pytest.raises(InvalidCredentialsError):
        await account_service.login(_flush_session(), login_request, None)
    assert state_record.login_locked_until is not None
    assert state_record.login_locked_until > utc_now() + timedelta(minutes=1)
    with pytest.raises(LoginLockedError):
        await account_service.login(_flush_session(), login_request, None)

    actions = [event["action"] for event in audit_events]
    assert AccessAuditAction.LOGIN_LOCKED in actions
    assert actions.count(AccessAuditAction.LOGIN_FAILED) == 3


@pytest.mark.asyncio
async def test_phone_change_code_cannot_be_requested_without_a_login() -> None:
    """The public code endpoint refuses the PHONE_CHANGE purpose."""
    with pytest.raises(OneTimeCodePurposeError):
        await account_service.send_one_time_code(
            _flush_session(),
            OneTimeCodeSendRequest(
                phone_number="0901234567", purpose=OneTimeCodePurpose.PHONE_CHANGE
            ),
        )


@pytest.mark.asyncio
async def test_password_reset_code_for_an_unknown_phone_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reset request cannot reveal whether a number has an account."""

    async def no_user(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(identity_repository, "find_live_user_by_phone", no_user)

    response = await account_service.send_one_time_code(
        _flush_session(),
        OneTimeCodeSendRequest(
            phone_number="0901234567", purpose=OneTimeCodePurpose.PASSWORD_RESET
        ),
    )

    assert response.expires_at > utc_now()


@pytest.mark.asyncio
async def test_sign_up_code_for_a_registered_phone_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered number is told to log in instead (409)."""

    async def existing_user(*_a: Any, **_k: Any) -> UserModel:
        return _user_record()

    monkeypatch.setattr(identity_repository, "find_live_user_by_phone", existing_user)

    with pytest.raises(UserConflictError):
        await account_service.send_one_time_code(
            _flush_session(),
            OneTimeCodeSendRequest(
                phone_number="0901234567", purpose=OneTimeCodePurpose.SIGN_UP
            ),
        )


def test_role_grant_rules_follow_the_decision_log() -> None:
    """ORG_ADMIN is handed over; admin roles are internal-only and HEAD-granted."""
    internal_organization = build_organization_record()
    internal_organization.is_internal = True
    customer_organization = build_organization_record()
    head_admin = _principal(is_internal=True, roles={UserRole.HEAD_ADMIN})
    co_admin = _principal(is_internal=True, roles={UserRole.CO_ADMIN})

    with pytest.raises(RoleNotAllowedError):
        member_service._check_role_grantable(
            head_admin, customer_organization, UserRole.ORG_ADMIN
        )
    with pytest.raises(RoleNotAllowedError):
        member_service._check_role_grantable(
            head_admin, customer_organization, UserRole.CO_ADMIN
        )
    with pytest.raises(AccessDeniedError):
        member_service._check_role_grantable(
            co_admin, internal_organization, UserRole.CO_ADMIN
        )
    member_service._check_role_grantable(
        head_admin, internal_organization, UserRole.CO_ADMIN
    )
    member_service._check_role_grantable(
        co_admin, customer_organization, UserRole.FLEET_MANAGER
    )


def test_member_management_is_for_org_admin_of_that_organization_or_our_admins() -> (
    None
):
    """An ORG_ADMIN of another organization may not manage members."""
    organization_id = uuid4()
    own_admin = _principal(
        is_internal=False, roles={UserRole.ORG_ADMIN}, organization_id=organization_id
    )
    other_admin = _principal(is_internal=False, roles={UserRole.ORG_ADMIN})
    dispatcher = _principal(
        is_internal=False, roles={UserRole.DISPATCHER}, organization_id=organization_id
    )
    co_admin = _principal(is_internal=True, roles={UserRole.CO_ADMIN})

    member_service._require_member_manager(own_admin, organization_id)
    member_service._require_member_manager(co_admin, organization_id)
    with pytest.raises(AccessDeniedError):
        member_service._require_member_manager(other_admin, organization_id)
    with pytest.raises(AccessDeniedError):
        member_service._require_member_manager(dispatcher, organization_id)


@pytest.mark.parametrize(
    ("legal_form", "tax_code", "expected"),
    [
        (OrganizationLegalForm.COMPANY, "0312345678", "0312345678"),
        (OrganizationLegalForm.COMPANY, "0312345678-001", "0312345678001"),
        (OrganizationLegalForm.INDIVIDUAL, "012345678901", "012345678901"),
    ],
)
def test_tax_code_is_checked_against_the_legal_form(
    legal_form: OrganizationLegalForm, tax_code: str, expected: str
) -> None:
    """Company 10/13 digits, individual 12 digits (ID-05)."""
    assert organization_service.normalize_tax_code(legal_form, tax_code) == expected


def test_tax_code_of_the_wrong_length_is_refused() -> None:
    """A 12-digit code is not a company tax code, a 10-digit one not a CCCD."""
    with pytest.raises(OrganizationInvalidError):
        organization_service.normalize_tax_code(
            OrganizationLegalForm.COMPANY, "012345678901"
        )
    with pytest.raises(OrganizationInvalidError):
        organization_service.normalize_tax_code(
            OrganizationLegalForm.INDIVIDUAL, "0312345678"
        )
    with pytest.raises(OrganizationInvalidError):
        organization_service.normalize_tax_code(
            OrganizationLegalForm.COMPANY, "03123abc78"
        )


@pytest.mark.asyncio
async def test_only_internal_staff_create_organizations() -> None:
    """A customer ORG_ADMIN cannot create an organization (403)."""
    create_request = OrganizationCreateRequest(
        legal_form=OrganizationLegalForm.COMPANY,
        display_name="A",
        legal_name="A Co",
        first_admin={"phone_number": "0901234567", "full_name": "Admin"},  # type: ignore[arg-type]
    )

    with pytest.raises(AccessDeniedError):
        await organization_service.create_organization(
            _flush_session(),
            _principal(is_internal=False, roles={UserRole.ORG_ADMIN}),
            create_request,
        )
    with pytest.raises(AccessDeniedError):
        await organization_service.create_organization(
            _flush_session(),
            _principal(is_internal=True, roles={UserRole.OPERATIONS}),
            create_request,
        )


@pytest.mark.asyncio
async def test_status_change_needs_an_internal_administrator() -> None:
    """Sales may create customers but not suspend or close them."""
    with pytest.raises(AccessDeniedError):
        await organization_service.change_organization_status(
            _flush_session(),
            _principal(is_internal=True, roles={UserRole.SALES}),
            uuid4(),
            OrganizationStatusRequest(
                status=OrganizationStatus.SUSPENDED, reason="debt"
            ),
        )


@pytest.mark.asyncio
async def test_out_of_reach_organization_is_reported_as_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A customer asking for another organization gets 404, not 403."""
    other = build_organization_record()

    async def get_organization(*_a: Any, **_k: Any) -> OrganizationModel:
        return other

    monkeypatch.setattr(identity_repository, "get_organization", get_organization)

    with pytest.raises(Exception) as error_info:
        await organization_service.get_organization(
            _flush_session(),
            _principal(is_internal=False, roles={UserRole.ORG_ADMIN}),
            other.organization_id,
        )
    assert type(error_info.value).__name__ == "OrganizationNotFoundError"


@pytest.mark.asyncio
async def test_audit_log_export_needs_a_reason_and_only_data_actions_are_allowed() -> (
    None
):
    """EXPORT without a reason and a LOGIN action both fail (ID-41)."""
    principal = _principal(is_internal=False, roles={UserRole.ORG_ADMIN})

    with pytest.raises(AuditLogInvalidError):
        await audit_service.record_data_access(
            _flush_session(),
            principal=principal,
            action=AccessAuditAction.EXPORT,
            resource_type="X",
            resource_id=None,
            details=None,
            client_context=None,
        )
    with pytest.raises(AuditLogInvalidError):
        await audit_service.record_data_access(
            _flush_session(),
            principal=principal,
            action=AccessAuditAction.LOGIN_SUCCESS,
            resource_type="X",
            resource_id=None,
            details=None,
            client_context=None,
        )


@pytest.mark.asyncio
async def test_audit_log_search_is_for_administrators_only() -> None:
    """A dispatcher cannot read the audit log; a reversed range is refused."""
    arguments: dict[str, Any] = {
        "user_id": None,
        "organization_id": None,
        "action": None,
        "resource_type": None,
        "occurred_from": None,
        "occurred_to": None,
        "page": 1,
        "page_size": 10,
    }
    with pytest.raises(AccessDeniedError):
        await audit_service.search_audit_logs(
            _flush_session(),
            _principal(is_internal=False, roles={UserRole.DISPATCHER}),
            **arguments,
        )
    now = utc_now()
    with pytest.raises(AuditLogInvalidError):
        await audit_service.search_audit_logs(
            _flush_session(),
            _principal(is_internal=True, roles={UserRole.HEAD_ADMIN}),
            **{
                **arguments,
                "occurred_from": now,
                "occurred_to": now - timedelta(days=1),
            },
        )


@pytest.mark.asyncio
async def test_require_roles_dependency_admits_only_listed_roles() -> None:
    """The dependency returns the principal or raises 403."""
    dependency = require_roles(UserRole.FLEET_MANAGER)
    manager = _principal(is_internal=False, roles={UserRole.FLEET_MANAGER})
    driver = _principal(is_internal=False, roles={UserRole.DRIVER})

    assert await dependency(principal=manager) is manager
    with pytest.raises(AccessDeniedError):
        await dependency(principal=driver)
    with pytest.raises(AccessDeniedError):
        await require_roles(UserRole.FLEET_MANAGER, internal_only=True)(
            principal=manager
        )


@pytest.mark.asyncio
async def test_new_error_bases_map_to_401_403_423_429() -> None:
    """The shared handler knows the authentication-related bases."""
    request: Any = None

    unauthenticated = await domain_error_handler(request, UnauthenticatedError("no"))
    denied = await domain_error_handler(request, PermissionDeniedError("no"))
    locked = await domain_error_handler(request, LockedError("no"))
    limited = await domain_error_handler(request, TooManyRequestsError("no"))

    assert unauthenticated.status_code == status.HTTP_401_UNAUTHORIZED
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
    assert denied.status_code == status.HTTP_403_FORBIDDEN
    assert locked.status_code == status.HTTP_423_LOCKED
    assert limited.status_code == status.HTTP_429_TOO_MANY_REQUESTS
