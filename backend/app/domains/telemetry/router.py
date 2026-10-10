"""HTTP router for reading vehicle telemetry data.

This module only turns requests into service calls; it contains no
database queries or business logic. Domain exceptions are not caught here:
``app/api/main.py`` maps ``TelemetryNotFoundError`` (and the fleet
domain's ``FleetNotFoundError``, raised through the fleet-wide views) to
404 and ``TelemetryInvalidRangeError`` to 400 through their shared bases.

The fleet-wide views live here, under ``/fleets/{fleet_id}/...``, rather
than in the fleet domain, so the only edge is ``telemetry -> fleet``
(planner D7 of ``backend-happy-path-completion.md``).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.service as identity_service
import app.domains.telemetry.service as telemetry_service
from app.domains.identity.dependencies import (
    get_client_context,
    require_roles,
)
from app.domains.identity.types import (
    AccessAuditAction,
    ClientContext,
    Principal,
    roles_for,
)
from app.domains.telemetry.schemas import (
    FleetOperatingReportResponse,
    FleetVehicleLiveStatusListResponse,
    VehicleActivationListResponse,
    VehicleActivationResponse,
    VehicleBatteryHealthResponse,
    VehicleEnergyUsageResponse,
    VehicleOperatingReportResponse,
    VehicleTelemetryHistoryResponse,
    VehicleTelemetryLatestResponse,
)
from app.domains.telemetry.types import (
    ReportFormat,
    ReportGranularity,
    VehicleActivationStatus,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["telemetry"])

# Who may call what (features.yaml `users`, via `roles_for`): MON-02 live
# status (a DRIVER reads only the truck they are checked in to, enforced in the
# service), MON-10 location history, MON-07 battery health, MON-14 reports,
# FLT-04/FLT-06 fleet views, VEH-05 vehicle activation (computed here because
# `vehicles` may not call `telematics` or `telemetry`).
LIVE_STATUS_READERS = require_roles(*roles_for("MON-02", "DEV-04"))
LOCATION_HISTORY_READERS = require_roles(*roles_for("MON-10"))
BATTERY_HEALTH_READERS = require_roles(*roles_for("MON-07", "MON-08"))
REPORT_READERS = require_roles(*roles_for("MON-14"))
ACTIVATION_READERS = require_roles(*roles_for("VEH-05"))
FLEET_LIVE_READERS = require_roles(*roles_for("FLT-04"))
FLEET_REPORT_READERS = require_roles(*roles_for("FLT-06", "MON-14"))


@router.get(
    "/vehicles/activation",
    response_model=VehicleActivationListResponse,
    summary="List trucks with their computed activation and the success rate",
)
async def list_vehicle_activations_endpoint(
    activation_status: VehicleActivationStatus | None = Query(
        None, description="Only trucks in this status (AWAITING_DATA = still waiting)"
    ),
    organization_id: UUID | None = Query(
        None, description="Only this organization's trucks (internal staff)"
    ),
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    principal: Principal = Depends(ACTIVATION_READERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleActivationListResponse:
    """Return the activation summary and a page of trucks (VEH-05).

    Args:
        activation_status: Only trucks in this status on the page, if given.
        organization_id: Only trucks of this organization, if given.
        page: Page number.
        page_size: Number of records per page.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The counts and the success rate over the scope, and the page.
    """
    return await telemetry_service.list_vehicle_activations(
        db,
        principal=principal,
        organization_id=organization_id,
        activation_status=activation_status,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/vehicles/{vehicle_id}/activation",
    response_model=VehicleActivationResponse,
    summary="Get the computed activation of a truck",
)
async def get_vehicle_activation_endpoint(
    vehicle_id: UUID,
    principal: Principal = Depends(ACTIVATION_READERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleActivationResponse:
    """Return a truck's activation: device mounted now and data received (VEH-05).

    Args:
        vehicle_id: Internal ID of the vehicle.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The truck's activation.

    Raises:
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist, was
            soft-deleted or is out of the caller's reach.
    """
    return await telemetry_service.get_vehicle_activation_response(
        db, vehicle_id, principal=principal
    )


@router.get(
    "/vehicles/{vehicle_id}/latest",
    response_model=VehicleTelemetryLatestResponse,
    summary="Get the latest telemetry for a vehicle",
)
async def get_latest_vehicle_telemetry_endpoint(
    vehicle_id: UUID,
    client_context: ClientContext = Depends(get_client_context),
    principal: Principal = Depends(LIVE_STATUS_READERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleTelemetryLatestResponse:
    """Return the latest telemetry record for a vehicle.

    Args:
        vehicle_id: Internal ID of the vehicle.
        client_context: IP address and user agent, for the audit row.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The latest telemetry record.

    Raises:
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist, was
            soft-deleted, is out of the caller's reach, or has no telemetry
            yet.
        AccessDeniedError: HTTP 403 - a driver who is not checked in to this
            truck.
    """
    latest_response = await telemetry_service.get_latest_vehicle_telemetry_response(
        db, vehicle_id, principal=principal
    )
    # The position of a truck is personal data of its driver (ACC-18).
    await identity_service.record_data_access(
        db,
        principal=principal,
        action=AccessAuditAction.VIEW,
        resource_type="VEHICLE_LOCATION",
        resource_id=str(vehicle_id),
        client_context=client_context,
    )
    return latest_response


@router.get(
    "/vehicles/{vehicle_id}/history",
    response_model=VehicleTelemetryHistoryResponse,
    summary="Get telemetry history for a vehicle within a time range",
)
async def get_vehicle_telemetry_history_endpoint(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    limit: int = Query(
        settings.TELEMETRY_HISTORY_DEFAULT_LIMIT,
        ge=1,
        le=settings.TELEMETRY_HISTORY_MAX_LIMIT,
    ),
    client_context: ClientContext = Depends(get_client_context),
    principal: Principal = Depends(LOCATION_HISTORY_READERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleTelemetryHistoryResponse:
    """Return a vehicle's telemetry history within a bounded time range (F-A5).

    Scoped as a time-range location/telemetry history query for trip
    replay, not segmented trips - this backend has no trip concept yet (see
    ``docs/decisions/deferred.md``). The caller narrows the time window
    if a range holds more points than ``limit``.

    Args:
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        limit: Maximum number of points to return.
        client_context: IP address and user agent, for the audit row.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        Telemetry points ordered chronologically (oldest first).

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist or
            was soft-deleted.
    """
    history_response = await telemetry_service.get_vehicle_telemetry_history_response(
        db,
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        principal=principal,
    )
    # A location trail is personal data of the driver (ACC-18, ID-41).
    await identity_service.record_data_access(
        db,
        principal=principal,
        action=AccessAuditAction.VIEW,
        resource_type="VEHICLE_LOCATION_HISTORY",
        resource_id=str(vehicle_id),
        client_context=client_context,
        details={
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        },
    )
    return history_response


@router.get(
    "/vehicles/{vehicle_id}/operating-report",
    response_model=VehicleOperatingReportResponse,
    summary="Get a vehicle's operating performance over a time window",
    responses={200: {"content": {"text/csv": {}}}},
)
async def get_vehicle_operating_report_endpoint(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    granularity: ReportGranularity | None = None,
    report_format: ReportFormat = Query(ReportFormat.JSON, alias="format"),
    principal: Principal = Depends(REPORT_READERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleOperatingReportResponse | Response:
    """Return distance, energy consumed, and cost for a vehicle over a window (F-A6).

    Energy is inferred from SOC drops in the vehicle's own telemetry, not
    from charging-session records. See
    ``VehicleOperatingReportResponse`` for the accuracy limits.

    Args:
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        granularity: Optional ``day``/``week``/``month`` breakdown in
            ``settings.APP_REPORT_TIMEZONE``; omitted keeps the single
            whole-window aggregate.
        report_format: ``json`` (default) or ``csv`` (query parameter
            ``format``) - CSV has one row per period, or one row for the
            whole window.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The operating report over the normalized time window, as JSON or
        as a ``text/csv`` attachment.

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist or
            was soft-deleted.
    """
    operating_report = await telemetry_service.get_vehicle_operating_report(
        db,
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        granularity=granularity,
        principal=principal,
    )
    if report_format is ReportFormat.CSV:
        return Response(
            content=telemetry_service.serialize_vehicle_operating_report_csv(
                operating_report
            ),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="operating-report-{vehicle_id}.csv"'
                )
            },
        )
    return operating_report


@router.get(
    "/vehicles/{vehicle_id}/battery-health",
    response_model=VehicleBatteryHealthResponse,
    summary="Get a vehicle's daily battery-health (SOH) trend",
)
async def get_vehicle_battery_health_endpoint(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    principal: Principal = Depends(BATTERY_HEALTH_READERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleBatteryHealthResponse:
    """Return one SOH/cycle-count point per day over a window (F-A3).

    Days are calendar days of ``settings.APP_REPORT_TIMEZONE``; days
    without an SOH or cycle-count reading are omitted.

    Args:
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The daily battery-health trend over the normalized window.

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist or
            was soft-deleted.
    """
    return await telemetry_service.get_vehicle_battery_health_response(
        db,
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        principal=principal,
    )


@router.get(
    "/vehicles/{vehicle_id}/energy-usage",
    response_model=VehicleEnergyUsageResponse,
    summary="Get a vehicle's (customer's) charged energy over a time window",
)
async def get_vehicle_energy_usage_endpoint(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    principal: Principal = Depends(REPORT_READERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleEnergyUsageResponse:
    """Return energy charged into a vehicle's pack over a window (F-C6).

    "Customer" is a vehicle in this MVP. Energy is inferred from SOC
    rises in the vehicle's own telemetry, not from a station meter - see
    ``VehicleEnergyUsageResponse`` for why this cannot satisfy NF-10's
    reconciliation requirement.

    Args:
        vehicle_id: Internal ID of the vehicle (customer).
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The energy-usage report over the normalized time window.

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: HTTP 404 - the vehicle does not exist or
            was soft-deleted.
    """
    return await telemetry_service.get_vehicle_energy_usage_report(
        db,
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        principal=principal,
    )


@router.get(
    "/fleets/{fleet_id}/vehicles/latest",
    response_model=FleetVehicleLiveStatusListResponse,
    summary="Get the newest position and online flag of every vehicle in a fleet",
)
async def list_fleet_vehicle_live_statuses_endpoint(
    fleet_id: UUID,
    page: int = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number"),
    page_size: int = Query(
        settings.API_DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.API_MAX_PAGE_SIZE,
        description="Number of records per page",
    ),
    principal: Principal = Depends(FLEET_LIVE_READERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> FleetVehicleLiveStatusListResponse:
    """Return a fleet's current member vehicles with their live status (F-E1).

    Args:
        fleet_id: Internal ID of the fleet.
        page: Page number.
        page_size: Number of records per page.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        One page of member vehicles (oldest member first) with their VIN,
        plate, status, newest position and online flag.

    Raises:
        FleetNotFoundError: HTTP 404 - the fleet does not exist or was
            soft-deleted.
    """
    return await telemetry_service.list_fleet_vehicle_live_statuses(
        db, fleet_id, page=page, page_size=page_size, principal=principal
    )


@router.get(
    "/fleets/{fleet_id}/operating-report",
    response_model=FleetOperatingReportResponse,
    summary="Get a fleet's operating performance over a time window",
    responses={200: {"content": {"text/csv": {}}}},
)
async def get_fleet_operating_report_endpoint(
    fleet_id: UUID,
    start_time: datetime,
    end_time: datetime,
    report_format: ReportFormat = Query(ReportFormat.JSON, alias="format"),
    principal: Principal = Depends(FLEET_REPORT_READERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> FleetOperatingReportResponse | Response:
    """Return per-vehicle and total distance, energy and cost of a fleet (F-A6).

    Covers the fleet's current member vehicles. Totals are sums; fleet
    rates are recomputed from the sums, never averaged. See
    ``FleetOperatingReportResponse`` for the accuracy limits.

    Args:
        fleet_id: Internal ID of the fleet.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        report_format: ``json`` (default) or ``csv`` (query parameter
            ``format``) - CSV has one row per vehicle plus a final
            ``TOTAL`` row.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The fleet operating report, as JSON or as a ``text/csv``
        attachment.

    Raises:
        TelemetryInvalidRangeError: HTTP 400 - a bound has no timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS``.
        FleetNotFoundError: HTTP 404 - the fleet does not exist or was
            soft-deleted.
    """
    fleet_report = await telemetry_service.get_fleet_operating_report(
        db, fleet_id, start_time=start_time, end_time=end_time, principal=principal
    )
    if report_format is ReportFormat.CSV:
        return Response(
            content=telemetry_service.serialize_fleet_operating_report_csv(
                fleet_report
            ),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="fleet-operating-report-{fleet_id}.csv"'
                )
            },
        )
    return fleet_report
