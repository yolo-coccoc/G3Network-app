"""FastAPI router for Telematic device CRUD and the F-J2 config push.

Handlers only translate HTTP to service calls. Domain exceptions are not
caught here: `app/api/main.py` maps each shared error base once
(`TelematicNotFoundError` -> 404, `TelematicConflictError` and
`TelematicNotConfigurableError` -> 409, `TelematicCommandPublishError` ->
502) with the same `{"detail": message}` body for every router.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_service
from app.domains.telematics.schemas import (
    TelematicConfigPushRequest,
    TelematicConfigResponse,
    TelematicCreateRequest,
    TelematicListResponse,
    TelematicResponse,
    TelematicUpdateRequest,
)
from app.domains.telematics.types import TelematicStatus
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["telematics"])


@router.post("/", response_model=TelematicResponse, status_code=status.HTTP_201_CREATED)
async def create_telematic_endpoint(
    telematic_create_request: TelematicCreateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> TelematicResponse:
    """Create a Telematic device, optionally assigned to a vehicle by VIN.

    Args:
        telematic_create_request: Request data for creating the device.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Created device.

    Raises:
        TelematicConflictError: The serial already exists or the vehicle is
            already assigned to another device (409).
    """
    return await telematics_service.create_telematic(
        db_session,
        telematic_create_request,
    )


@router.get("/", response_model=TelematicListResponse)
async def list_telematics_endpoint(
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE, ge=1, le=settings.API_MAX_PAGE_SIZE
    ),
    status_filter: TelematicStatus | None = Query(None, alias="status"),
    db_session: AsyncSession = Depends(get_db),
) -> TelematicListResponse:
    """List devices that have not been soft-deleted.

    Args:
        page: Page number, starting from 1.
        page_size: Number of records per page.
        status_filter: Status filter (query parameter ``status``), if any.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Paginated list of devices.
    """
    return await telematics_service.list_telematics(
        db_session,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
    )


@router.get("/{telematic_id}", response_model=TelematicResponse)
async def get_telematic_endpoint(
    telematic_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> TelematicResponse:
    """Get the details of a device.

    Args:
        telematic_id: Internal ID of the device.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Device details.

    Raises:
        TelematicNotFoundError: The device does not exist or is
            soft-deleted (404).
    """
    return await telematics_service.get_telematic(db_session, telematic_id)


@router.patch("/{telematic_id}", response_model=TelematicResponse)
async def update_telematic_endpoint(
    telematic_id: UUID,
    telematic_update_request: TelematicUpdateRequest,
    db_session: AsyncSession = Depends(get_db),
) -> TelematicResponse:
    """Partially update a device.

    Args:
        telematic_id: Internal ID of the device.
        telematic_update_request: Request data for updating the device.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        Updated device.

    Raises:
        TelematicNotFoundError: The device does not exist or is
            soft-deleted (404).
        TelematicConflictError: The new serial already exists or the new
            vehicle is already assigned to another device (409).
    """
    return await telematics_service.update_telematic(
        db_session,
        telematic_id,
        telematic_update_request,
    )


@router.delete("/{telematic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_telematic_endpoint(
    telematic_id: UUID,
    db_session: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a device.

    Args:
        telematic_id: Internal ID of the device.
        db_session: Database session owned by the HTTP boundary.

    Raises:
        TelematicNotFoundError: The device does not exist or is already
            soft-deleted (404).
    """
    await telematics_service.soft_delete_telematic(db_session, telematic_id)


@router.post("/{telematic_id}/config", response_model=TelematicConfigResponse)
async def push_telematic_config_endpoint(
    telematic_id: UUID,
    telematic_config_push_request: TelematicConfigPushRequest,
    db_session: AsyncSession = Depends(get_db),
) -> TelematicConfigResponse:
    """Push a telemetry publish-interval config to a device over MQTT (F-J2).

    Args:
        telematic_id: Internal ID of the device.
        telematic_config_push_request: Desired telemetry publish interval.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The pushed configuration and the command topic it was published to.

    Raises:
        TelematicNotFoundError: The device does not exist or is
            soft-deleted (404).
        TelematicNotConfigurableError: The device is ``INACTIVE`` (409).
        TelematicCommandPublishError: The MQTT publish failed (502).
    """
    return await telematics_service.push_telematic_config(
        db_session,
        telematic_id,
        telematic_config_push_request,
    )
