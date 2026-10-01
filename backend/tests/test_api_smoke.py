"""Smoke test for the FastAPI entry point and its registered routers."""

import json

import pytest

from app.api.main import app, domain_error_handler, health_check
from app.domains.fleet.exceptions import FleetMembershipConflictError
from app.domains.notifications.router import router as notifications_router
from app.domains.telematics.exceptions import TelematicCommandPublishError
from app.domains.telemetry.exceptions import TelemetryInvalidRangeError
from app.domains.vehicles.exceptions import VehicleNotFoundError
from app.libs.common.config import settings
from app.libs.common.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    UpstreamUnavailableError,
)


def test_openapi_registers_current_backend_routes() -> None:
    """OpenAPI must expose the main route groups of the current backend."""
    paths = app.openapi()["paths"]

    assert "/health" in paths
    assert any(path.startswith("/api/v1/vehicles") for path in paths)
    assert any(path.startswith("/api/v1/telematics") for path in paths)
    assert any(path.startswith("/api/v1/telemetry") for path in paths)
    assert any(path.startswith("/api/v1/charging-stations") for path in paths)
    assert any(path.startswith("/api/v1/charging-sessions") for path in paths)
    # F-A5: the bounded time-range telemetry history query.
    assert "/api/v1/telemetry/vehicles/{vehicle_id}/history" in paths
    # F-D1: the nearby-station search, registered before {station_id}.
    assert "/api/v1/charging-stations/nearby" in paths
    # F-C5: the station-level energy aggregation query.
    assert "/api/v1/charging-sessions/stations/{station_id}/energy" in paths
    # F-C5: the per-station energy series and the all-stations totals.
    assert "/api/v1/charging-sessions/stations/{station_id}/energy/series" in paths
    assert "/api/v1/charging-sessions/stations/energy" in paths
    # F-C2: the whole charger's and every gun's status in one read.
    assert "/api/v1/charging-stations/{station_id}/connectors" in paths
    # F-F2: the activation summary, registered before {vehicle_id}.
    assert "/api/v1/vehicles/activation-summary" in paths
    # F-J2: push a telemetry publish-interval config to a device over MQTT.
    assert "/api/v1/telematics/{telematic_id}/config" in paths
    # F-J2 (D9): the fleet-wide config push, one result per member.
    assert "post" in paths["/api/v1/telematics/fleets/{fleet_id}/config"]
    # F-A6/F-C6: per-vehicle SOC-based operating and energy-usage reports.
    assert "/api/v1/telemetry/vehicles/{vehicle_id}/operating-report" in paths
    assert "/api/v1/telemetry/vehicles/{vehicle_id}/energy-usage" in paths
    # F-E1/F-A6: fleet-wide telemetry views, served by telemetry (planner D7).
    assert "/api/v1/telemetry/fleets/{fleet_id}/vehicles/latest" in paths
    assert "/api/v1/telemetry/fleets/{fleet_id}/operating-report" in paths
    # F-E4: the drivers domain and its vehicle-assignment endpoints.
    assert any(path.startswith("/api/v1/drivers") for path in paths)
    assert "/api/v1/drivers/{driver_id}/assignment" in paths
    assert "/api/v1/drivers/{driver_id}/assignments" in paths
    # F-I1/F-I2: the support domain's ticket and SOS intake endpoints.
    assert "/api/v1/support/cases" in paths
    assert "/api/v1/support/sos" in paths
    assert "/api/v1/support/cases/{case_id}" in paths
    # F-E1: the fleet domain and its vehicle-membership endpoints.
    assert any(path.startswith("/api/v1/fleets") for path in paths)
    assert "/api/v1/fleets/{fleet_id}/vehicles" in paths
    assert "/api/v1/fleets/{fleet_id}/memberships" in paths


@pytest.mark.asyncio
async def test_health_endpoint_returns_healthy_status() -> None:
    """The health check returns a healthy status without needing a database."""
    response = await health_check()

    assert response["status"] == "healthy"
    assert "version" in response


def test_notification_poll_limit_is_bounded_like_other_list_endpoints() -> None:
    """The poll `limit` is capped at API_MAX_PAGE_SIZE (F-A2).

    Regression: it was an unbounded int, so `limit=10**9` was accepted.
    """
    parameters = app.openapi()["paths"]["/api/v1/notifications"]["get"]["parameters"]
    limit_schema = next(p for p in parameters if p["name"] == "limit")["schema"]

    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == settings.API_MAX_PAGE_SIZE


def test_fleet_vehicle_removal_and_driver_assignment_contracts() -> None:
    """Fleet removal is by VIN; driver assignment answers 201 like fleet add (F-E1/F-E4)."""
    paths = app.openapi()["paths"]

    assert "delete" in paths["/api/v1/fleets/{fleet_id}/vehicles/{vehicle_vin}"]
    assignment = paths["/api/v1/drivers/{driver_id}/assignment"]["post"]
    assert "201" in assignment["responses"]


def test_people_package_routes_are_registered() -> None:
    """Geofences, membership close-by-ID and the notification reads exist (F-A5,
    #84, F-A2); the fixed notification paths precede ``/{notification_id}``."""
    paths = app.openapi()["paths"]

    assert "delete" in paths["/api/v1/fleets/{fleet_id}/memberships/{membership_id}"]
    assert {"get", "post"} <= set(paths["/api/v1/fleets/{fleet_id}/geofences"])
    assert {"get", "patch", "delete"} <= set(
        paths["/api/v1/fleets/{fleet_id}/geofences/{geofence_id}"]
    )
    assert "post" in paths["/api/v1/notifications/mark-all-read"]
    assert "get" in paths["/api/v1/notifications/{notification_id}"]
    route_paths = [getattr(route, "path", "") for route in notifications_router.routes]
    assert route_paths.index("/unread-count") < route_paths.index("/{notification_id}")
    assert route_paths.index("/mark-all-read") < route_paths.index("/{notification_id}")


def test_telematic_response_exposes_device_health_fields() -> None:
    """Device reads carry the F-J1 health fields; the fleet push reports per vehicle."""
    schemas = app.openapi()["components"]["schemas"]

    telematic_fields = schemas["TelematicResponse"]["properties"]
    assert {
        "last_seen_at",
        "is_online",
        "is_silent",
        "last_signal_strength_dbm",
    } <= set(telematic_fields)
    fleet_push_fields = schemas["TelematicFleetConfigPushResponse"]["properties"]
    assert {
        "fleet_id",
        "published_count",
        "skipped_count",
        "failed_count",
        "results",
    } <= set(fleet_push_fields)


def test_every_shared_error_base_has_a_registered_handler() -> None:
    """main.py maps each domain-exception base once for all routers."""
    for error_base in (
        NotFoundError,
        ConflictError,
        InvalidInputError,
        UpstreamUnavailableError,
    ):
        assert app.exception_handlers[error_base] is domain_error_handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (VehicleNotFoundError("Vehicle with id 'x' not found"), 404),
        (FleetMembershipConflictError("already in another fleet"), 409),
        (TelemetryInvalidRangeError("end_time must be after start_time"), 400),
        (TelematicCommandPublishError("broker unreachable"), 502),
    ],
)
async def test_domain_error_handler_keeps_status_and_detail_shape(
    error: Exception, expected_status: int
) -> None:
    """A domain exception becomes {"detail": message} with its base's status."""
    response = await domain_error_handler(None, error)  # type: ignore[arg-type]

    assert response.status_code == expected_status
    assert json.loads(bytes(response.body)) == {"detail": str(error)}
