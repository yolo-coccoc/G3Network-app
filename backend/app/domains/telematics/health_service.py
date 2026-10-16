"""Device health and status reports (DEV-03, DEV-04, DEV-05).

Stores the status a T-Box reports about itself (``record_status_report``,
called by the status ingestion) and serves the health of one device, the
device-health list and its summary: the silence flag from the newest
telemetry and the newest status report, judged by ``health_rule.py``.
Internal module: the domain's router and the status ingestion call it. Runs
in the caller's transaction and never commits.
"""

import logging
from datetime import datetime
from typing import TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.service as fleet_service
import app.domains.telematics.access as telematic_access
import app.domains.telematics.device_view as telematic_device_view
import app.domains.telematics.health_rule as health_rule
import app.domains.telematics.repository as telematics_repository
from app.domains.identity.types import Principal
from app.domains.telematics.models import TelematicModel
from app.domains.telematics.schemas import (
    TelematicHealthListResponse,
    TelematicHealthResponse,
    TelematicHealthSummaryResponse,
    TelematicStatusEnvelope,
    TelematicStatusReportListResponse,
    TelematicStatusReportResponse,
)
from app.domains.telematics.types import (
    TelematicHealthState,
)
from app.libs.common.clock import utc_now
from app.libs.common.pagination import normalize_page_window

logger = logging.getLogger(__name__)


class StatusReportResult(TypedDict):
    """Counters returned after processing one status message.

    Attributes:
        processed: Reports stored (0 or 1).
        skipped: 1 when nothing was stored: unknown or deleted device, or a
            message with no health field (a plain online / offline notice).
        errors: Always 0 here; a malformed message never reaches the service
            (the consumer drops it) and a database error is raised.
    """

    processed: int
    skipped: int
    errors: int


async def record_status_report(
    db_session: AsyncSession, envelope: TelematicStatusEnvelope
) -> StatusReportResult:
    """Store one T-Box status report (DEV-03, TX-10).

    Rules:
        The device is found by the serial of the topic; a serial that is
        unknown or belongs to a deleted device is skipped (the report would
        have no owner). A device that is not mounted still reports (a unit
        in stock being provisioned), so only the device must exist. A message
        with none of the health fields is skipped, so a Last Will
        ``{"status": "offline"}`` never becomes an empty "newest report". The
        row's ``reported_at`` is the device's timestamp or the receive time.

    Args:
        db_session: Session whose transaction is owned by the worker.
        envelope: Message already validated by the consumer, with the serial.

    Returns:
        The counters of this one message.

    Raises:
        Exception: Database errors propagate so the worker rolls back.

    Side Effects:
        May insert and flush one ``telematic_status_reports`` row; does not
        commit.
    """
    telematic_record = await telematics_repository.find_by_serial(
        db_session, envelope.telematic_serial
    )
    if telematic_record is None:
        logger.warning(
            "device not found, status message skipped",
            extra={"telematic_serial": envelope.telematic_serial},
        )
        return {"processed": 0, "skipped": 1, "errors": 0}
    if not envelope.message.has_health_fields():
        return {"processed": 0, "skipped": 1, "errors": 0}
    await telematics_repository.insert_status_report(
        db_session,
        envelope.message.to_status_report_values(
            telematic_record.telematic_id, utc_now()
        ),
    )
    return {"processed": 1, "skipped": 0, "errors": 0}


async def list_telematic_status_reports(
    db_session: AsyncSession,
    telematic_id: UUID,
    *,
    principal: Principal,
    since: datetime | None,
    limit: int,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicStatusReportListResponse:
    """List a device's status reports, newest first, to see trends (DEV-04).

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.
        principal: The caller; a device of another organization is not found
            unless the caller is internal.
        since: Only reports produced at or after this time, if given.
        limit: Maximum number of reports.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a device not mounted on one of them is not found. `None` means
            no limit.

    Returns:
        The reports, newest first.

    Raises:
        TelematicDateInvalidError: ``since`` has no time zone.
        TelematicNotFoundError: The device does not exist, is soft-deleted or
            is out of the caller's reach.
    """
    if since is not None:
        since = telematic_access.normalize_utc(since, "since")
    await telematic_access.get_telematic_record(
        db_session, telematic_id, principal, visible_vehicle_ids=visible_vehicle_ids
    )
    status_report_records = await telematics_repository.list_status_reports(
        db_session, telematic_id, since=since, limit=limit
    )
    return TelematicStatusReportListResponse(
        items=[
            TelematicStatusReportResponse.model_validate(status_report_record)
            for status_report_record in status_report_records
        ],
        count=len(status_report_records),
    )


async def build_telematic_health_response(
    db_session: AsyncSession, telematic_record: TelematicModel
) -> TelematicHealthResponse:
    """Build the dashboard entry of one device (DEV-04).

    Args:
        db_session: Current database session.
        telematic_record: Device record loaded in this session.

    Returns:
        The device's health: state, silence and online flags, and its newest
        status report.

    Side Effects:
        Read-only, see ``_load_device_view``.
    """
    device_view = await telematic_device_view.load_device_view(
        db_session, telematic_record
    )
    device_health = device_view.device_health
    latest_status_report = device_view.latest_status_report
    return TelematicHealthResponse(
        telematic_id=telematic_record.telematic_id,
        telematic_serial=telematic_record.telematic_serial,
        organization_id=telematic_record.organization_id,
        status=telematic_record.status,
        vehicle_id=telematic_record.vehicle_id if device_view.is_mounted else None,
        vehicle_vin=device_view.vehicle_vin,
        health_state=health_rule.classify_device_health(
            status=telematic_record.status,
            is_mounted=device_view.is_mounted,
            last_seen_at=device_health.last_seen_at,
            is_silent=device_health.is_silent,
            sim_data_status=(
                latest_status_report.sim_data_status if latest_status_report else None
            ),
            gnss_status=(
                latest_status_report.gnss_status if latest_status_report else None
            ),
        ),
        last_seen_at=device_health.last_seen_at,
        is_online=device_health.is_online,
        is_silent=device_health.is_silent,
        latest_report=(
            TelematicStatusReportResponse.model_validate(latest_status_report)
            if latest_status_report
            else None
        ),
    )


async def get_telematic_health(
    db_session: AsyncSession,
    telematic_id: UUID,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicHealthResponse:
    """Get the dashboard entry of one device inside the caller's reach.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.
        principal: The caller.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a device not mounted on one of them is not found. `None` means
            no limit.

    Returns:
        The device's health.

    Raises:
        TelematicNotFoundError: The device does not exist, is soft-deleted or
            is out of the caller's reach.
    """
    telematic_record = await telematic_access.get_telematic_record(
        db_session, telematic_id, principal, visible_vehicle_ids=visible_vehicle_ids
    )
    return await build_telematic_health_response(db_session, telematic_record)


async def _collect_device_health(
    db_session: AsyncSession,
    principal: Principal,
    *,
    fleet_id: UUID | None,
    organization_id: UUID | None,
    visible_vehicle_ids: frozenset[UUID] | None,
) -> list[TelematicHealthResponse]:
    """Compute the health of every device in the caller's scope (DEV-04).

    Args:
        db_session: Current database session.
        principal: The caller; a restricted caller sees only their own
            organization's devices.
        fleet_id: Only the devices mounted on this fleet's current trucks, if
            given.
        organization_id: Only devices of this organization (internal staff;
            for a restricted caller any other organization gives nothing).
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10):
            without ``fleet_id`` only devices mounted on them are read, so an
            unmounted device is hidden (D5). With ``fleet_id`` the fleet
            service already applies the same limit to the fleet's trucks.
            `None` means no limit.

    Returns:
        One entry per device, newest device first (fleet: membership order).

    Raises:
        FleetNotFoundError: The fleet does not exist or is out of reach.

    Side Effects:
        Read-only. One group of lookups per device, per the
        no-preemptive-batching rule; the whole scope is read because the
        summary and the state filter need every device.
    """
    scope = principal.data_scope
    if scope is not None and organization_id not in (None, scope):
        return []
    scope = scope if scope is not None else organization_id
    if fleet_id is None:
        telematic_records = await telematics_repository.list_in_scope(
            db_session, organization_id=scope, vehicle_ids=visible_vehicle_ids
        )
    else:
        vehicle_ids = await fleet_service.list_active_member_vehicle_ids(
            db_session, fleet_id, principal=principal
        )
        telematic_records = []
        for vehicle_id in vehicle_ids:
            telematic_record = await telematics_repository.find_by_vehicle_id(
                db_session, vehicle_id
            )
            if telematic_record is not None and (
                scope is None or telematic_record.organization_id == scope
            ):
                telematic_records.append(telematic_record)
    return [
        await build_telematic_health_response(db_session, telematic_record)
        for telematic_record in telematic_records
    ]


async def list_device_health(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int,
    page_size: int,
    fleet_id: UUID | None = None,
    organization_id: UUID | None = None,
    health_state: TelematicHealthState | None = None,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicHealthListResponse:
    """List the health of devices as a dashboard page (DEV-04).

    Args:
        db_session: Current database session.
        principal: The caller.
        page: Page number, starting from 1; clamped by ``normalize_page_window``.
        page_size: Devices per page; clamped to ``1..API_MAX_PAGE_SIZE``.
        fleet_id: Only the devices on this fleet's trucks, if given.
        organization_id: Only devices of this organization, if given.
        health_state: Only devices in this state, if given.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            `None` means no limit.

    Returns:
        One page of entries and the total matching the filters.

    Raises:
        FleetNotFoundError: The fleet does not exist or is out of reach.
    """
    page_window = normalize_page_window(page, page_size)
    health_responses = await _collect_device_health(
        db_session,
        principal,
        fleet_id=fleet_id,
        organization_id=organization_id,
        visible_vehicle_ids=visible_vehicle_ids,
    )
    if health_state is not None:
        health_responses = [
            health_response
            for health_response in health_responses
            if health_response.health_state is health_state
        ]
    return TelematicHealthListResponse(
        items=health_responses[
            page_window.offset : page_window.offset + page_window.page_size
        ],
        total=len(health_responses),
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_device_health_summary(
    db_session: AsyncSession,
    *,
    principal: Principal,
    fleet_id: UUID | None = None,
    organization_id: UUID | None = None,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicHealthSummaryResponse:
    """Count devices per health state and give the healthy share (DEV-04).

    Args:
        db_session: Current database session.
        principal: The caller.
        fleet_id: Only the devices on this fleet's trucks, if given.
        organization_id: Only devices of this organization, if given.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            `None` means no limit.

    Returns:
        The count per state, the online count and the healthy share of the
        devices that should be reporting.

    Raises:
        FleetNotFoundError: The fleet does not exist or is out of reach.
    """
    health_responses = await _collect_device_health(
        db_session,
        principal,
        fleet_id=fleet_id,
        organization_id=organization_id,
        visible_vehicle_ids=visible_vehicle_ids,
    )
    counts = {
        state: sum(
            1
            for health_response in health_responses
            if health_response.health_state is state
        )
        for state in TelematicHealthState
    }
    expected_count = (
        counts[TelematicHealthState.HEALTHY]
        + counts[TelematicHealthState.ATTENTION]
        + counts[TelematicHealthState.SILENT]
        + counts[TelematicHealthState.NO_DATA]
    )
    return TelematicHealthSummaryResponse(
        total_count=len(health_responses),
        healthy_count=counts[TelematicHealthState.HEALTHY],
        attention_count=counts[TelematicHealthState.ATTENTION],
        silent_count=counts[TelematicHealthState.SILENT],
        no_data_count=counts[TelematicHealthState.NO_DATA],
        not_mounted_count=counts[TelematicHealthState.NOT_MOUNTED],
        inactive_count=counts[TelematicHealthState.INACTIVE],
        online_count=sum(
            1 for health_response in health_responses if health_response.is_online
        ),
        healthy_percent=(
            round(100 * counts[TelematicHealthState.HEALTHY] / expected_count, 1)
            if expected_count
            else None
        ),
    )
