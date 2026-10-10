"""Smoke tests for authentication, role gates and the data scope (WP2b).

Covers the rules every domain applies since the routers were wired to
`get_current_principal`: no token means 401, a role outside the endpoint's
feature list means 403, and a record of another organization is "not found"
(404) unless the caller is internal staff (ACC-15, DM-24).
"""

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_session_repository
import app.domains.charging_sessions.service as charging_session_service
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.service as charging_stations_service
import app.domains.drivers.repository as driver_repository
import app.domains.drivers.router as driver_router
import app.domains.drivers.service as driver_service
import app.domains.fleet.repository as fleet_repository
import app.domains.fleet.router as fleet_router
import app.domains.fleet.service as fleet_service
import app.domains.notifications.repository as notification_repository
import app.domains.notifications.service as notification_service
import app.domains.support.repository as support_repository
import app.domains.support.service as support_service
import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.router as telematics_router
import app.domains.telematics.service as telematics_service
import app.domains.telemetry.router as telemetry_router
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.router as vehicle_router
from app.api.main import app
from app.domains.charging_sessions.exceptions import ChargingSessionNotFoundError
from app.domains.charging_stations.exceptions import (
    ChargingLocationNotFoundError,
    ChargingStationNotFoundError,
)
from app.domains.drivers.exceptions import DriverNotFoundError
from app.domains.fleet.exceptions import FleetNotFoundError
from app.domains.identity.dependencies import get_session_identity
from app.domains.identity.exceptions import AccessDeniedError, SessionInvalidError
from app.domains.identity.types import (
    FEATURE_ROLES,
    Principal,
    UserRole,
    roles_for,
)
from app.domains.notifications.exceptions import NotificationNotFoundError
from app.domains.support.exceptions import SupportCaseNotFoundError
from app.domains.telematics.exceptions import TelematicNotFoundError
from app.domains.telemetry.exceptions import TelemetryNotFoundError
from app.libs.common.errors import PermissionDeniedError, UnauthenticatedError
from tests.builders import (
    build_charging_location_record,
    fake_db_session,
)
from tests.principals import (
    OTHER_ORGANIZATION_ID,
    build_internal_principal,
    build_principal,
)

# Operations that must stay open: health, the sign-in family and the public
# legal texts. Everything else needs a bearer token.
PUBLIC_OPERATIONS = {
    ("GET", "/health"),
    ("POST", "/api/v1/auth/otp/send"),
    ("POST", "/api/v1/auth/sign-up"),
    ("POST", "/api/v1/auth/invitations/accept"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/password/reset"),
    ("GET", "/api/v1/legal-documents/current"),
    ("GET", "/api/v1/legal-documents/{legal_document_id}"),
}


def test_every_endpoint_but_the_public_ones_declares_bearer_security() -> None:
    """No router endpoint was left open by the wiring (ACC-14)."""
    open_operations = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method, operation in operations.items()
        if not operation.get("security")
    }

    assert open_operations == PUBLIC_OPERATIONS


@pytest.mark.asyncio
async def test_a_request_without_a_token_is_unauthenticated() -> None:
    """The authentication dependency answers 401 (UnauthenticatedError) first."""
    with pytest.raises(SessionInvalidError) as raised:
        await get_session_identity(None, fake_db_session())

    assert isinstance(raised.value, UnauthenticatedError)


def _roles_without(*roles: UserRole) -> frozenset[UserRole]:
    """Return every role except the given ones."""
    return frozenset(UserRole) - frozenset(roles)


@pytest.mark.parametrize(
    ("gate", "allowed_role", "refused_role"),
    [
        (vehicle_router.VEHICLE_WRITERS, UserRole.FLEET_MANAGER, UserRole.DRIVER),
        (vehicle_router.VEHICLE_MODEL_WRITERS, UserRole.OPERATIONS, UserRole.SALES),
        (telematics_router.DEVICE_WRITERS, UserRole.OPERATIONS, UserRole.DRIVER),
        (
            telemetry_router.LOCATION_HISTORY_READERS,
            UserRole.DISPATCHER,
            UserRole.DRIVER,
        ),
        (fleet_router.GEOFENCE_WRITERS, UserRole.DISPATCHER, UserRole.DRIVER),
        (driver_router.DRIVER_PROFILE_WRITERS, UserRole.FLEET_MANAGER, UserRole.DRIVER),
        (driver_router.DRIVING_SESSION_USERS, UserRole.DRIVER, UserRole.ACCOUNTANT),
    ],
)
@pytest.mark.asyncio
async def test_role_gate_admits_the_feature_roles_and_refuses_the_others(
    gate: Callable[..., Awaitable[Principal]],
    allowed_role: UserRole,
    refused_role: UserRole,
) -> None:
    """Each endpoint family follows the `users` list of its features."""
    internal = gate is vehicle_router.VEHICLE_MODEL_WRITERS
    allowed = build_principal(roles=frozenset({allowed_role}), is_internal=internal)
    refused = build_principal(roles=frozenset({refused_role}), is_internal=internal)

    assert await gate(principal=allowed) is allowed
    with pytest.raises(AccessDeniedError) as raised:
        await gate(principal=refused)
    assert isinstance(raised.value, PermissionDeniedError)


@pytest.mark.asyncio
async def test_a_customer_cannot_use_an_internal_only_gate() -> None:
    """The truck model catalog is changed by our staff only (VEH-03)."""
    customer_operations = build_principal(
        roles=frozenset({UserRole.OPERATIONS}), is_internal=False
    )

    with pytest.raises(AccessDeniedError):
        await vehicle_router.VEHICLE_MODEL_WRITERS(principal=customer_operations)


def test_administrators_and_the_organization_admin_pass_every_feature_gate() -> None:
    """HEAD_ADMIN, CO_ADMIN and ORG_ADMIN are added to every feature list."""
    for feature_code in FEATURE_ROLES:
        roles = roles_for(feature_code)
        assert {UserRole.HEAD_ADMIN, UserRole.CO_ADMIN, UserRole.ORG_ADMIN} <= roles


def test_an_unknown_feature_code_fails_at_import_time() -> None:
    """A typo in a router's feature code cannot silently open an endpoint."""
    with pytest.raises(KeyError):
        roles_for("XXX-99")


def test_the_data_scope_is_the_organization_for_customers_and_none_for_staff() -> None:
    """`data_scope` is what services pass to repositories (None = all)."""
    customer = build_principal()

    assert customer.data_scope == customer.organization_id
    assert build_internal_principal().data_scope is None


# --- 404 for another organization's record, per domain ------------------------


@pytest.mark.asyncio
async def test_telematic_of_another_organization_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device lookup carries the caller's organization (customer) or none."""
    scopes: list[UUID | None] = []

    async def get_by_id(
        db: AsyncSession, telematic_id: UUID, *, organization_id: UUID | None = None
    ) -> None:
        scopes.append(organization_id)

    monkeypatch.setattr(telematics_repository, "get_by_id", get_by_id)
    customer = build_principal()

    with pytest.raises(TelematicNotFoundError):
        await telematics_service.get_telematic(
            fake_db_session(), uuid4(), principal=customer
        )
    with pytest.raises(TelematicNotFoundError):
        await telematics_service.get_telematic(
            fake_db_session(), uuid4(), principal=build_internal_principal()
        )

    assert scopes == [customer.organization_id, None]


@pytest.mark.asyncio
async def test_fleet_of_another_organization_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fleet lookup carries the caller's organization (customer) or none."""
    scopes: list[UUID | None] = []

    async def get_by_id(
        db: AsyncSession, fleet_id: UUID, *, organization_id: UUID | None = None
    ) -> None:
        scopes.append(organization_id)

    monkeypatch.setattr(fleet_repository, "get_by_id", get_by_id)
    customer = build_principal()

    with pytest.raises(FleetNotFoundError):
        await fleet_service.get_fleet(fake_db_session(), uuid4(), principal=customer)

    assert scopes == [customer.organization_id]


@pytest.mark.asyncio
async def test_driver_of_another_organization_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver profile is read through its membership's organization."""
    scopes: list[UUID | None] = []

    async def get_by_id(
        db: AsyncSession, driver_id: UUID, *, organization_id: UUID | None = None
    ) -> None:
        scopes.append(organization_id)

    monkeypatch.setattr(driver_repository, "get_by_id", get_by_id)
    customer = build_principal()

    with pytest.raises(DriverNotFoundError):
        await driver_service.get_driver(fake_db_session(), uuid4(), principal=customer)

    assert scopes == [customer.organization_id]


@pytest.mark.asyncio
async def test_a_driver_only_caller_cannot_name_another_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only managers check another driver in (DR-07); a driver acts for themselves."""

    async def no_own_profile(db: AsyncSession, membership_id: UUID) -> None:
        return None

    monkeypatch.setattr(driver_repository, "find_by_membership_id", no_own_profile)
    driver_only = build_principal(roles=frozenset({UserRole.DRIVER}))

    from app.domains.drivers.schemas import DrivingSessionCheckOutRequest

    with pytest.raises(AccessDeniedError):
        await driver_service.check_out_driver(
            fake_db_session(),
            DrivingSessionCheckOutRequest(driver_id=uuid4()),
            principal=driver_only,
        )


@pytest.mark.asyncio
async def test_support_case_of_another_organization_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A case lookup carries the caller's organization; staff need no driver."""
    seen: list[tuple[UUID | None, UUID | None]] = []

    async def get_by_id(
        db: AsyncSession,
        case_id: UUID,
        *,
        organization_id: UUID | None = None,
        driver_id: UUID | None = None,
    ) -> None:
        seen.append((organization_id, driver_id))

    monkeypatch.setattr(support_repository, "get_by_id", get_by_id)
    customer = build_principal(roles=frozenset({UserRole.FLEET_MANAGER}))

    with pytest.raises(SupportCaseNotFoundError):
        await support_service.get_support_case(
            fake_db_session(), uuid4(), principal=customer
        )

    assert seen == [(customer.organization_id, None)]


@pytest.mark.asyncio
async def test_charging_session_of_another_organization_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session is scoped by its payer; a driver also by the scanning user."""
    seen: list[tuple[UUID | None, UUID | None]] = []

    async def get_session_by_id(
        db: AsyncSession,
        session_id: UUID,
        *,
        organization_id: UUID | None = None,
        started_by: UUID | None = None,
    ) -> None:
        seen.append((organization_id, started_by))

    monkeypatch.setattr(
        charging_session_repository, "get_session_by_id", get_session_by_id
    )
    driver = build_principal(roles=frozenset({UserRole.DRIVER}))
    manager = build_principal(roles=frozenset({UserRole.FLEET_MANAGER}))

    for principal in (driver, manager):
        with pytest.raises(ChargingSessionNotFoundError):
            await charging_session_service.get_charging_session(
                fake_db_session(), uuid4(), principal=principal
            )

    assert seen == [
        (driver.organization_id, driver.user_id),
        (manager.organization_id, None),
    ]


@pytest.mark.asyncio
async def test_a_notification_outside_the_inbox_and_the_organization_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver reads only their own inbox; staff also their organization's."""
    record = type("NotificationStub", (), {"organization_id": OTHER_ORGANIZATION_ID})()

    async def get_by_id(db: AsyncSession, notification_id: int) -> object:
        return record

    async def find_recipient(*_args: object) -> None:
        return None

    monkeypatch.setattr(notification_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(notification_repository, "find_recipient", find_recipient)

    for principal in (
        build_principal(roles=frozenset({UserRole.DRIVER})),
        build_principal(roles=frozenset({UserRole.FLEET_MANAGER})),
    ):
        with pytest.raises(NotificationNotFoundError):
            await notification_service.get_notification(
                fake_db_session(), 1, principal=principal
            )


@pytest.mark.asyncio
async def test_telemetry_of_another_organizations_vehicle_is_not_found(
    monkeypatch: pytest.MonkeyPatch, no_installed_battery: None
) -> None:
    """A vehicle reference of another organization hides the vehicle's data."""
    import app.domains.vehicles.service as vehicle_service
    from app.domains.vehicles.types import VehicleReference

    async def resolve(db: AsyncSession, vehicle_id: UUID) -> VehicleReference:
        return VehicleReference(
            vehicle_id=vehicle_id,
            vin="1HGBH41JXMN109186",
            organization_id=OTHER_ORGANIZATION_ID,
            battery_capacity_kwh=None,
        )

    monkeypatch.setattr(vehicle_service, "resolve_vehicle_reference_by_id", resolve)

    with pytest.raises(TelemetryNotFoundError):
        await telemetry_service.get_latest_vehicle_telemetry_response(
            fake_db_session(),
            uuid4(),
            principal=build_principal(roles=frozenset({UserRole.FLEET_MANAGER})),
        )


@pytest.mark.asyncio
async def test_a_driver_reads_live_data_only_of_the_truck_they_are_checked_in_to(
    monkeypatch: pytest.MonkeyPatch, no_installed_battery: None
) -> None:
    """After check-out the driver gets 403, whichever organization owns the truck."""
    import app.domains.vehicles.service as vehicle_service
    from app.domains.vehicles.types import VehicleReference

    async def resolve(db: AsyncSession, vehicle_id: UUID) -> VehicleReference:
        return VehicleReference(
            vehicle_id=vehicle_id,
            vin="1HGBH41JXMN109186",
            organization_id=OTHER_ORGANIZATION_ID,
            battery_capacity_kwh=None,
        )

    async def not_checked_in(*_args: object) -> bool:
        return False

    monkeypatch.setattr(vehicle_service, "resolve_vehicle_reference_by_id", resolve)
    monkeypatch.setattr(
        driver_service, "is_membership_checked_in_to_vehicle", not_checked_in
    )

    with pytest.raises(AccessDeniedError):
        await telemetry_service.get_latest_vehicle_telemetry_response(
            fake_db_session(),
            uuid4(),
            principal=build_principal(roles=frozenset({UserRole.DRIVER})),
        )


# --- charging network visibility (CS-10, CS-13) --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_public", "has_grant", "view", "expected"),
    [
        (True, False, True, True),
        (True, False, False, False),
        (False, False, True, False),
        (False, True, True, True),
        (False, True, False, False),
    ],
)
async def test_a_foreign_location_is_visible_when_public_or_granted_but_never_managed(
    monkeypatch: pytest.MonkeyPatch,
    is_public: bool,
    has_grant: bool,
    view: bool,
    expected: bool,
) -> None:
    """Public or granted locations may be seen by others; only the owner manages."""

    async def get_live_location_access(
        db: AsyncSession, location_id: UUID, organization_id: UUID
    ) -> object | None:
        return object() if has_grant else None

    monkeypatch.setattr(
        charging_stations_repository,
        "get_live_location_access",
        get_live_location_access,
    )
    location = build_charging_location_record(organization_id=OTHER_ORGANIZATION_ID)
    location.is_public = is_public

    reachable = await charging_stations_service._can_reach_location(
        fake_db_session(), location, build_principal(), view=view
    )

    assert reachable is expected


@pytest.mark.asyncio
async def test_the_owner_and_internal_staff_manage_a_location() -> None:
    """The owner organization and internal staff reach any of its locations."""
    owner = build_principal(organization_id=OTHER_ORGANIZATION_ID)
    location = build_charging_location_record(organization_id=OTHER_ORGANIZATION_ID)
    location.is_public = False

    assert await charging_stations_service._can_reach_location(
        fake_db_session(), location, owner, view=False
    )
    assert await charging_stations_service._can_reach_location(
        fake_db_session(), location, build_internal_principal(), view=False
    )


@pytest.mark.asyncio
async def test_station_and_location_of_another_organization_are_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private foreign location hides itself and its chargers (404)."""
    location = build_charging_location_record(organization_id=OTHER_ORGANIZATION_ID)
    location.is_public = False

    async def get_location(
        db: AsyncSession, location_id: UUID, **_scope: object
    ) -> object:
        return location

    async def no_grant(*_args: object) -> None:
        return None

    from tests.builders import build_charging_station_record

    station = build_charging_station_record(location_id=location.location_id)

    async def get_station(db: AsyncSession, station_id: UUID) -> object:
        return station

    monkeypatch.setattr(
        charging_stations_repository, "get_location_by_id", get_location
    )
    monkeypatch.setattr(
        charging_stations_repository, "get_live_location_access", no_grant
    )
    monkeypatch.setattr(charging_stations_repository, "get_station_by_id", get_station)
    customer = build_principal()

    with pytest.raises(ChargingLocationNotFoundError):
        await charging_stations_service.get_charging_location(
            fake_db_session(), location.location_id, principal=customer
        )
    with pytest.raises(ChargingStationNotFoundError):
        await charging_stations_service.get_charging_station(
            fake_db_session(), station.station_id, principal=customer
        )
