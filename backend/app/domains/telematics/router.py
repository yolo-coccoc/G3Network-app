"""FastAPI router for Telematic device CRUD, the health dashboard and the F-J2 config pushes.

Handlers only translate HTTP to service calls. Domain exceptions are not
caught here: `app/api/main.py` maps each shared error base once
(`TelematicNotFoundError`, `TelematicVehicleNotFoundError` and the fleet
push's `FleetNotFoundError` -> 404,
`TelematicConflictError` and
`TelematicNotConfigurableError` -> 409, `TelematicCommandPublishError` ->
502) with the same `{"detail": message}` body for every router.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_service
from app.domains.identity.dependencies import require_roles
from app.domains.identity.types import Principal, roles_for
from app.domains.telematics.schemas import (
    TelematicConfigPushRequest,
    TelematicConfigResponse,
    TelematicCreateRequest,
    TelematicFleetConfigPushResponse,
    TelematicHealthListResponse,
    TelematicHealthResponse,
    TelematicHealthSummaryResponse,
    TelematicListResponse,
    TelematicResponse,
    TelematicStatusReportListResponse,
    TelematicUpdateRequest,
)
from app.domains.telematics.types import TelematicHealthState, TelematicStatus
from app.libs.common.config import settings
from app.libs.common.reason import Reason
from app.libs.db.session import get_db

router = APIRouter(tags=["telematics"])

# Who may call what (features.yaml `users`, via `roles_for`): DEV-01 device
# registry, DEV-02 device-to-vehicle assignment, DEV-04 health dashboard,
# DEV-07 remote configuration.
DEVICE_READERS = require_roles(*roles_for("DEV-01", "DEV-02", "DEV-04"))
DEVICE_WRITERS = require_roles(*roles_for("DEV-01", "DEV-02"))
DEVICE_CONFIGURERS = require_roles(*roles_for("DEV-07"))
DEVICE_HEALTH_READERS = require_roles(*roles_for("DEV-04"))


@router.post("/", response_model=TelematicResponse, status_code=status.HTTP_201_CREATED)
async def create_telematic_endpoint(
    telematic_create_request: TelematicCreateRequest,
    principal: Principal = Depends(DEVICE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicResponse:
    """Create a Telematic device, optionally assigned to a vehicle by VIN.

    Args:
        telematic_create_request: Request data for creating the device.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created device.

    Raises:
        TelematicVehicleNotFoundError: ``vehicle_vin`` matches no live
            vehicle (404).
        TelematicConflictError: The serial already exists or the vehicle is
            already assigned to another live device (409).
    """
    return await telematics_service.create_telematic(
        db_session,
        telematic_create_request,
        principal=principal,
    )


@router.get("/", response_model=TelematicListResponse)
async def list_telematics_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE, ge=1, le=settings.API_MAX_PAGE_SIZE
    ),
    status_filter: TelematicStatus | None = Query(None, alias="status"),
    principal: Principal = Depends(DEVICE_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicListResponse:
    """List devices that have not been soft-deleted.

    Args:
        page: Page number, starting from 1.
        page_size: Number of records per page.
        status_filter: Status filter (query parameter ``status``), if any.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of devices.
    """
    return await telematics_service.list_telematics(
        db_session,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        principal=principal,
    )


@router.get("/health/summary", response_model=TelematicHealthSummaryResponse)
async def get_device_health_summary_endpoint(
    fleet_id: UUID | None = Query(None, description="Only this fleet's trucks"),
    organization_id: UUID | None = Query(
        None, description="Only this organization's devices (internal staff)"
    ),
    principal: Principal = Depends(DEVICE_HEALTH_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicHealthSummaryResponse:
    """Count devices per health state and give the healthy share (DEV-04).

    Args:
        fleet_id: Only the devices on this fleet's trucks, if given.
        organization_id: Only devices of this organization, if given.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The count per state and the share of healthy devices.

    Raises:
        FleetNotFoundError: The fleet does not exist or is out of reach (404).
    """
    return await telematics_service.get_device_health_summary(
        db_session,
        principal=principal,
        fleet_id=fleet_id,
        organization_id=organization_id,
    )


@router.get("/health/devices", response_model=TelematicHealthListResponse)
async def list_device_health_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE, ge=1, le=settings.API_MAX_PAGE_SIZE
    ),
    fleet_id: UUID | None = Query(None, description="Only this fleet's trucks"),
    organization_id: UUID | None = Query(
        None, description="Only this organization's devices (internal staff)"
    ),
    health_state: TelematicHealthState | None = Query(None),
    principal: Principal = Depends(DEVICE_HEALTH_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicHealthListResponse:
    """List devices with their health state and newest status report (DEV-04).

    Args:
        page: Page number, starting from 1.
        page_size: Number of records per page.
        fleet_id: Only the devices on this fleet's trucks, if given.
        organization_id: Only devices of this organization, if given.
        health_state: Only devices in this state, if given.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of device health entries.

    Raises:
        FleetNotFoundError: The fleet does not exist or is out of reach (404).
    """
    return await telematics_service.list_device_health(
        db_session,
        principal=principal,
        page=page,
        page_size=page_size,
        fleet_id=fleet_id,
        organization_id=organization_id,
        health_state=health_state,
    )


@router.get("/{telematic_id}/health", response_model=TelematicHealthResponse)
async def get_telematic_health_endpoint(
    telematic_id: UUID,
    principal: Principal = Depends(DEVICE_HEALTH_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicHealthResponse:
    """Get the health of one device: state, silence and its newest status report.

    Args:
        telematic_id: Internal ID of the device.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The device's health.

    Raises:
        TelematicNotFoundError: The device does not exist or is soft-deleted
            (404).
    """
    return await telematics_service.get_telematic_health(
        db_session, telematic_id, principal=principal
    )


@router.get(
    "/{telematic_id}/status-reports", response_model=TelematicStatusReportListResponse
)
async def list_telematic_status_reports_endpoint(
    telematic_id: UUID,
    since: datetime | None = Query(
        None, description="Only reports produced at or after this time"
    ),
    limit: int = Query(100, ge=1, le=1000),
    principal: Principal = Depends(DEVICE_HEALTH_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicStatusReportListResponse:
    """List a device's status reports, newest first (signal, power, SIM trend).

    Args:
        telematic_id: Internal ID of the device.
        since: Only reports produced at or after this time, if given.
        limit: Maximum number of reports.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The reports, newest first.

    Raises:
        TelematicNotFoundError: The device does not exist or is soft-deleted
            (404).
    """
    return await telematics_service.list_telematic_status_reports(
        db_session, telematic_id, principal=principal, since=since, limit=limit
    )


@router.get("/{telematic_id}", response_model=TelematicResponse)
async def get_telematic_endpoint(
    telematic_id: UUID,
    principal: Principal = Depends(DEVICE_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicResponse:
    """Get the details of a device.

    Args:
        telematic_id: Internal ID of the device.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Device details.

    Raises:
        TelematicNotFoundError: The device does not exist or is
            soft-deleted (404).
    """
    return await telematics_service.get_telematic(
        db_session, telematic_id, principal=principal
    )


@router.patch("/{telematic_id}", response_model=TelematicResponse)
async def update_telematic_endpoint(
    telematic_id: UUID,
    telematic_update_request: TelematicUpdateRequest,
    principal: Principal = Depends(DEVICE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicResponse:
    """Partially update a device.

    Args:
        telematic_id: Internal ID of the device.
        telematic_update_request: Request data for updating the device.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated device.

    Raises:
        TelematicNotFoundError: The device does not exist or is
            soft-deleted (404).
        TelematicVehicleNotFoundError: ``vehicle_vin`` matches no live
            vehicle (404).
        TelematicConflictError: The new serial already exists or the new
            vehicle is already assigned to another live device (409).
    """
    return await telematics_service.update_telematic(
        db_session,
        telematic_id,
        telematic_update_request,
        principal=principal,
    )


@router.delete("/{telematic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_telematic_endpoint(
    telematic_id: UUID,
    reason: Reason | None = Query(None, description="Why the device is removed"),
    principal: Principal = Depends(DEVICE_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    """Soft-delete a device.

    Args:
        telematic_id: Internal ID of the device.
        reason: Why the device is removed (kept as the status reason).
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Raises:
        TelematicNotFoundError: The device does not exist or is already
            soft-deleted (404).
    """
    await telematics_service.soft_delete_telematic(
        db_session, telematic_id, principal=principal, reason=reason
    )


@router.post(
    "/fleets/{fleet_id}/config", response_model=TelematicFleetConfigPushResponse
)
async def push_fleet_config_endpoint(
    fleet_id: UUID,
    telematic_config_push_request: TelematicConfigPushRequest,
    principal: Principal = Depends(DEVICE_CONFIGURERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicFleetConfigPushResponse:
    """Push a telemetry publish-interval config to every device of a fleet (F-J2).

    Answers 200 even when some vehicles were skipped or failed: the
    partial outcome is in the per-vehicle results (planner D9).

    Args:
        fleet_id: Internal ID of the fleet.
        telematic_config_push_request: Desired telemetry publish interval.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The count per outcome and one result per active fleet member.

    Raises:
        FleetNotFoundError: The fleet does not exist or is soft-deleted
            (404).
    """
    return await telematics_service.push_fleet_config(
        db_session,
        fleet_id,
        telematic_config_push_request,
        principal=principal,
    )


@router.post("/{telematic_id}/config", response_model=TelematicConfigResponse)
async def push_telematic_config_endpoint(
    telematic_id: UUID,
    telematic_config_push_request: TelematicConfigPushRequest,
    principal: Principal = Depends(DEVICE_CONFIGURERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TelematicConfigResponse:
    """Push a telemetry publish-interval config to a device over MQTT (F-J2).

    Args:
        telematic_id: Internal ID of the device.
        telematic_config_push_request: Desired telemetry publish interval.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The pushed configuration and the command topic it was published to.

    Raises:
        TelematicNotFoundError: The device does not exist or is
            soft-deleted (404).
        TelematicNotConfigurableError: The device is not mounted on a
            vehicle or is ``INACTIVE`` (409).
        TelematicCommandPublishError: The MQTT publish failed (502).
    """
    return await telematics_service.push_telematic_config(
        db_session,
        telematic_id,
        telematic_config_push_request,
        principal=principal,
    )
