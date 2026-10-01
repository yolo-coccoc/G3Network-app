"""Public business service for the telemetry domain.

Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-A2 (Tiered
battery alerts), F-A3 (Battery health (SOH) & cycle tracking), F-A4
(Anomaly detection), F-A5 (Location, trip history & geofencing - the
time-range history query only; geofencing itself is deferred, see
docs/01-requirements/future.md), F-A6 (Operating performance report,
computed from SOC drops in telemetry - charging_sessions carries no
vehicle linkage), F-C6 (Per-customer energy usage, computed from SOC
rises in the same telemetry history; "customer" is a vehicle in this MVP)

This is the only telemetry module other domains may import (the
``telematics`` device-health monitor calls ``resolve_last_telemetry_at``).
It orchestrates I/O and delegates the pure work to internal modules:

- ``time_windows`` validates the query windows;
- ``mappers`` builds the HTTP responses from ORM rows;
- ``reports`` computes the F-A6/F-C6 reports from a folded window;
- ``alerting`` (backed by the pure ``detection``) raises the F-A2/F-A3/F-A4
  notifications during ingestion.

Cross-domain edges owned by this module: ``vehicles`` (existence, battery
capacity, F-F2 activation) and ``telematics`` (serial -> vehicle mapping).

Ingestion processes each message individually (``process_message``), for
low latency and per-message transaction isolation. A batched path (batch
lookup + bulk insert) is deferred until a benchmark needs it - see
``docs/01-requirements/future.md`` item 25.
"""

import logging
from datetime import datetime, timezone
from typing import TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_service
import app.domains.telemetry.alerting as telemetry_alerting
import app.domains.telemetry.mappers as telemetry_mappers
import app.domains.telemetry.reports as telemetry_reports
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.time_windows as telemetry_time_windows
import app.domains.vehicles.service as vehicle_service
from app.domains.telemetry.exceptions import TelemetryNotFoundError
from app.domains.telemetry.schemas import (
    TelemetryEnvelope,
    VehicleEnergyUsageResponse,
    VehicleOperatingReportResponse,
    VehicleTelemetryHistoryResponse,
    VehicleTelemetryLatestResponse,
)
from app.domains.vehicles.types import VehicleReference
from app.libs.common.config import settings

logger = logging.getLogger(__name__)


async def _get_vehicle_reference(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleReference:
    """Resolve a vehicle that must exist for a telemetry read to make sense.

    Args:
        db: Database session owned by the HTTP boundary.
        vehicle_id: Internal ID of the vehicle to resolve.

    Returns:
        The vehicle's cross-domain reference (id, VIN, battery capacity).

    Raises:
        TelemetryNotFoundError: If the vehicle does not exist or was
            soft-deleted.
    """
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db, vehicle_id
    )
    if vehicle_reference is None:
        raise TelemetryNotFoundError(f"Vehicle with id '{vehicle_id}' not found")
    return vehicle_reference


async def get_latest_vehicle_telemetry_response(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleTelemetryLatestResponse:
    """Get the latest telemetry after confirming the vehicle is still active.

    Args:
        db: Database session owned by the HTTP boundary.
        vehicle_id: Internal ID of the vehicle to query.

    Returns:
        Response schema containing the latest telemetry record.

    Raises:
        TelemetryNotFoundError: When the vehicle does not exist or has no
            telemetry yet.
    """
    await _get_vehicle_reference(db, vehicle_id)

    telemetry = await telemetry_repository.get_latest_vehicle_telemetry(db, vehicle_id)
    if telemetry is None:
        raise TelemetryNotFoundError(
            f"No telemetry found for vehicle with id '{vehicle_id}'"
        )

    return telemetry_mappers.to_vehicle_telemetry_latest_response(telemetry)


async def resolve_last_telemetry_at(
    db: AsyncSession, vehicle_id: UUID
) -> datetime | None:
    """Get a vehicle's last telemetry receive time. Public entry point for F-J1/F-J3.

    Returns a primitive rather than the ORM row or an HTTP schema, as a
    cross-domain call must.

    Args:
        db: Async session owned by the caller's entry boundary (the
            telematics device-health monitor's transaction).
        vehicle_id: Internal ID of the vehicle to check.

    Returns:
        The newest `received_at` (backend receive clock) among the vehicle's
        telemetry rows, or `None` if the vehicle has never reported. Both
        the value and the ordering use `received_at`, so a device whose
        clock runs ahead can neither dodge the silence check nor, via one
        future-dated `recorded_at`, make newer arrivals invisible to it.

    Side Effects:
        Read-only query; does not commit or roll back.
    """
    return await telemetry_repository.find_latest_received_at(db, vehicle_id)


async def get_vehicle_telemetry_history_response(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    limit: int | None = None,
) -> VehicleTelemetryHistoryResponse:
    """Get a vehicle's telemetry history within a bounded time range (F-A5).

    Scoped as a time-range location/telemetry history query, not segmented
    trips - this backend has no trip concept (see
    ``docs/01-requirements/future.md``). The caller narrows the time window
    if a range holds more points than ``limit``; this function does not
    paginate server-side.

    Args:
        db: Database session owned by the HTTP boundary.
        vehicle_id: Internal ID of the vehicle to query.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        limit: Maximum number of points to return, or ``None`` to use
            ``settings.TELEMETRY_HISTORY_DEFAULT_LIMIT``. Clamped to
            ``[1, settings.TELEMETRY_HISTORY_MAX_LIMIT]``.

    Returns:
        Response with points ordered chronologically (oldest first).

    Raises:
        TelemetryInvalidRangeError: If either bound is missing a timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: If the vehicle does not exist or was
            soft-deleted.
    """
    normalized_start, normalized_end = telemetry_time_windows.validate_time_window(
        start_time, end_time, settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS
    )

    resolved_limit = (
        limit if limit is not None else settings.TELEMETRY_HISTORY_DEFAULT_LIMIT
    )
    resolved_limit = min(max(resolved_limit, 1), settings.TELEMETRY_HISTORY_MAX_LIMIT)

    await _get_vehicle_reference(db, vehicle_id)

    records = await telemetry_repository.get_vehicle_telemetry_history(
        db,
        vehicle_id=vehicle_id,
        start_time=normalized_start,
        end_time=normalized_end,
        limit=resolved_limit,
    )
    points = [
        telemetry_mappers.to_vehicle_telemetry_history_point(record)
        for record in records
    ]
    return VehicleTelemetryHistoryResponse(
        vehicle_id=vehicle_id, points=points, count=len(points)
    )


async def _resolve_report_context(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> telemetry_reports.VehicleReportContext:
    """Validate a report window, resolve the vehicle, and fold its telemetry.

    Shared by F-A6 and F-C6 - both read the same window, the same vehicle
    record (for its battery capacity), and the same single aggregate;
    only the projection into a response schema differs.

    Args:
        db: Database session owned by the HTTP boundary.
        vehicle_id: Internal ID of the vehicle to report on.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.

    Returns:
        The validated window, the vehicle reference and the folded summary.

    Raises:
        TelemetryInvalidRangeError: Either bound is missing a timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: The vehicle does not exist or was
            soft-deleted. Unlike F-C5's station-energy summary (which
            doesn't own station existence and returns a zero result for
            an unknown station), this function 404s - `telemetry` already
            depends on `vehicles` and already 404s on its other two
            endpoints, and the vehicle lookup here is a required input
            for battery capacity anyway, not an avoidable extra query.
    """
    normalized_start, normalized_end = telemetry_time_windows.validate_time_window(
        start_time, end_time, settings.TELEMETRY_REPORT_MAX_RANGE_DAYS
    )
    vehicle_reference = await _get_vehicle_reference(db, vehicle_id)
    window_summary = await telemetry_repository.get_vehicle_window_summary(
        db,
        vehicle_id=vehicle_id,
        start_time=normalized_start,
        end_time=normalized_end,
    )
    return telemetry_reports.VehicleReportContext(
        vehicle_reference=vehicle_reference,
        start_time=normalized_start,
        end_time=normalized_end,
        window_summary=window_summary,
    )


async def get_vehicle_operating_report(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> VehicleOperatingReportResponse:
    """Get a vehicle's operating performance over a time window (F-A6).

    Energy consumed is inferred from summed SOC drops in the vehicle's
    own telemetry (not from `charging_sessions`, which carries no vehicle
    linkage), converted to kWh via the vehicle's recorded battery
    capacity or a documented engineering default. This measures gross
    discharge - SOC rises (regen, any charging inside the window) are not
    netted out. Assumes telemetry is reported frequently; sparse
    telemetry silently under-counts (an entire discharge-recharge cycle
    inside a reporting gap is invisible to this method). See
    ``VehicleOperatingReportResponse`` for the full limitations and
    ``reports.build_operating_report`` for when a rate is ``None``.

    Args:
        db: Database session owned by the HTTP boundary.
        vehicle_id: Internal ID of the vehicle to report on.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.

    Returns:
        The operating report over the normalized window.

    Raises:
        TelemetryInvalidRangeError: See ``_resolve_report_context``.
        TelemetryNotFoundError: See ``_resolve_report_context``.
    """
    report_context = await _resolve_report_context(
        db, vehicle_id=vehicle_id, start_time=start_time, end_time=end_time
    )
    return telemetry_reports.build_operating_report(report_context)


async def get_vehicle_energy_usage_report(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> VehicleEnergyUsageResponse:
    """Get the energy that entered one vehicle's pack over a time window (F-C6).

    "Customer" is a vehicle in this MVP (one vehicle per customer,
    per the user's simplification); there is no customer entity in this
    backend. Energy is inferred from summed SOC rises in the vehicle's
    own telemetry - the mirror image of F-A6's SOC-drop sum, sharing the
    same single-scan aggregate. See ``VehicleEnergyUsageResponse`` for why
    this cannot satisfy NF-10's 3-way reconciliation.

    Args:
        db: Database session owned by the HTTP boundary.
        vehicle_id: Internal ID of the vehicle to report on.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.

    Returns:
        The energy-usage report over the normalized window.

    Raises:
        TelemetryInvalidRangeError: See ``_resolve_report_context``.
        TelemetryNotFoundError: See ``_resolve_report_context``.
    """
    report_context = await _resolve_report_context(
        db, vehicle_id=vehicle_id, start_time=start_time, end_time=end_time
    )
    return telemetry_reports.build_energy_usage_report(report_context)


class MessageResult(TypedDict):
    """Counters returned after processing a telemetry message.

    Attributes:
        processed: Rows inserted for the message (0 or 1).
        skipped: 1 if the message was skipped because its serial has no
            vehicle mapping, else 0.
        errors: 1 if the message could not be converted into a row, else 0.
    """

    processed: int
    skipped: int
    errors: int


async def process_message(
    db: AsyncSession,
    envelope: TelemetryEnvelope,
) -> MessageResult:
    """Process one telemetry message within the current session's scope.

    Business rules:
    - Look up exactly one mapping by ``telematic_serial``.
    - A serial that doesn't exist or a device not yet assigned to a vehicle
      is safely skipped.
    - A valid message is enriched and then exactly one row is inserted.
    - A message conversion error only increments ``errors`` for the current
      message; a DB error is raised so the transaction boundary rolls back
      and the worker stops per MVP policy.
    - After a successful insert, if this is the vehicle's first-ever
      telemetry message (``previous_telemetry is None``), F-F2's activation
      state machine advances to ``ACTIVATED`` (see
      ``vehicle_service.mark_vehicle_activated``) - a best-effort side
      channel that never affects this function's own return value.
    - After a successful insert, the F-A2 battery-threshold, F-A3 SOH and
      F-A4 anomaly detectors run against the vehicle's previous reading
      and each alert raises its own notification (see
      ``alerting.raise_alerts_for_reading``). None of these ever affect
      ``processed``/``skipped``/``errors`` - each is reported via a
      separate structured log line.

    Args:
        db: AsyncSession whose transaction is owned by the worker.
        envelope: Message already validated by the MQTT consumer, along with
            the original raw payload.

    Returns:
        Dict with ``processed``, ``skipped`` and ``errors`` for that single
        message.

    Raises:
        Exception: Propagates database errors or unexpected errors so the
            worker can roll back.

    Side Effects:
        May write one telemetry row and alert notifications into the
        session and emit structured logs. The function does not commit or
        roll back.
    """
    message = envelope.message
    mapping = await telematics_service.resolve_mapping_by_serial(
        db, message.telematic_serial
    )
    if mapping is None:
        logger.warning(
            "telematic mapping not found, skipping message",
            extra={
                "telematic_serial": message.telematic_serial,
                "message_uuid": str(message.message_uuid),
            },
        )
        return {"processed": 0, "skipped": 1, "errors": 0}

    telematic_id = mapping.telematic_id
    vehicle_id = mapping.vehicle_id

    # Read the previous reading before inserting this one - once the new row
    # is inserted, get_latest_vehicle_telemetry would return it instead of
    # the actual previous reading, and no crossing could ever be detected.
    # Kept as the full ORM row since the F-A2/F-A3/F-A4 detectors compare
    # SOC, SOH, temperature, voltage and error codes.
    previous_telemetry = await telemetry_repository.get_latest_vehicle_telemetry(
        db, vehicle_id
    )

    try:
        telemetry_values = message.to_vehicle_telemetry_values(
            telematic_id,
            vehicle_id,
            datetime.now(timezone.utc),
            envelope.raw_payload,
        )
    except (TypeError, ValueError) as error:
        logger.exception(
            "failed to convert message to DB dict",
            extra={
                "telematic_serial": message.telematic_serial,
                "message_uuid": str(message.message_uuid),
                "error": str(error),
            },
        )
        return {"processed": 0, "skipped": 0, "errors": 1}

    processed_count = await telemetry_repository.insert_telemetry(
        db,
        telemetry_values,
    )
    logger.info(
        "telemetry message persisted",
        extra={
            "message_uuid": str(message.message_uuid),
            "processed": processed_count,
        },
    )

    if previous_telemetry is None:
        # F-F2: this vehicle's first-ever telemetry message confirms
        # end-to-end data flow. Best-effort side channel - never raises.
        await vehicle_service.mark_vehicle_activated(db, vehicle_id)

    await telemetry_alerting.raise_alerts_for_reading(
        db,
        vehicle_id=vehicle_id,
        previous_telemetry=previous_telemetry,
        message=message,
    )

    return {"processed": processed_count, "skipped": 0, "errors": 0}
