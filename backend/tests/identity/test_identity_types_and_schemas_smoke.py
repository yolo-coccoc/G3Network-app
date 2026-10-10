"""Smoke tests for the identity DTOs, role table and request schemas."""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domains.identity.schemas import (
    LoginRequest,
    OrganizationSettingsUpdateRequest,
    OrganizationUpdateRequest,
    SignUpRequest,
)
from app.domains.identity.types import (
    INTERNAL_ONLY_ROLES,
    ROLE_FEATURES,
    Principal,
    SessionPlatform,
    UserRole,
    features_of_roles,
)


def _principal(*, is_internal: bool, roles: frozenset[UserRole]) -> Principal:
    """Build a principal for the access-rule tests."""
    return Principal(
        user_id=uuid4(),
        membership_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        roles=roles,
        is_internal=is_internal,
    )


def test_internal_principal_reaches_every_organization_others_only_their_own() -> None:
    """Data reach follows the organization, not the role (ID-44, ACC-15)."""
    internal = _principal(is_internal=True, roles=frozenset({UserRole.CO_ADMIN}))
    customer = _principal(is_internal=False, roles=frozenset({UserRole.ORG_ADMIN}))

    assert internal.can_access_organization(uuid4())
    assert customer.can_access_organization(customer.organization_id)
    assert not customer.can_access_organization(uuid4())


def test_principal_roles_and_features() -> None:
    """has_any_role and the role-only feature table agree."""
    principal = _principal(
        is_internal=False, roles=frozenset({UserRole.ORG_ADMIN, UserRole.DRIVER})
    )

    assert principal.has_any_role(UserRole.DRIVER)
    assert not principal.has_any_role(UserRole.SALES)
    assert "ACC-09" in principal.features
    assert principal.features == features_of_roles(principal.roles)


def test_every_role_has_a_feature_entry_and_admin_roles_are_internal_only() -> None:
    """The role table covers the whole enum; HEAD/CO admin are internal only."""
    assert set(ROLE_FEATURES) == set(UserRole)
    assert INTERNAL_ONLY_ROLES == {UserRole.HEAD_ADMIN, UserRole.CO_ADMIN}


def test_phone_number_is_normalized_by_the_request_schema() -> None:
    """A national number in a body is stored in E.164 form."""
    login_request = LoginRequest(
        phone_number="0901234567", password="x", platform=SessionPlatform.ANDROID
    )

    assert login_request.phone_number == "+84901234567"


def test_sign_up_rejects_a_short_password_and_a_bad_code() -> None:
    """Schema validation gives 422 for a weak password or a malformed code."""
    common: dict[str, Any] = {
        "phone_number": "0901234567",
        "full_name": "A",
        "platform": SessionPlatform.ANDROID,
    }
    with pytest.raises(ValidationError):
        SignUpRequest(code="123456", password="short", **common)
    with pytest.raises(ValidationError):
        SignUpRequest(code="12345x", password="long enough", **common)


def test_tracked_updates_require_a_reason() -> None:
    """A change to a tracked row carries a reason (DM-21): 422 without it."""
    with pytest.raises(ValidationError):
        OrganizationUpdateRequest(display_name="New")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        OrganizationSettingsUpdateRequest(telemetry_interval_seconds=10)  # type: ignore[call-arg]


def test_settings_update_enforces_value_ranges() -> None:
    """Telemetry interval and auto-end minutes stay inside sane bounds."""
    with pytest.raises(ValidationError):
        OrganizationSettingsUpdateRequest(telemetry_interval_seconds=1, reason="x")
    with pytest.raises(ValidationError):
        OrganizationSettingsUpdateRequest(
            driving_session_auto_end_minutes=0, reason="x"
        )
