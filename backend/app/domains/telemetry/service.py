"""Public business service for the telemetry domain.

Feature code: F-A1 (Real-time vehicle telemetry ingestion, latest reading
and online status), F-A2 (Tiered battery alerts), F-A3 (Battery health
(SOH) & cycle tracking, incl. the daily trend), F-A4 (Anomaly detection),
F-A5 (Location, trip history & geofencing - the time-range history query
only; geofencing itself is deferred, see docs/01-requirements/future.md),
F-A6 (Operating performance report, computed from SOC drops in telemetry -
charging_sessions carries no vehicle linkage - with an optional
day/week/month breakdown), F-C6 (Per-customer energy usage, computed from
SOC rises in the same telemetry history; "customer" is a vehicle in this
MVP)

This is the only telemetry module other domains may import. Public
cross-domain functions (primitives or frozen DTOs from ``types.py`` only):

- ``resolve_last_telemetry_at`` - the ``telematics`` device-health monitor;
- ``resolve_vehicle_live_status`` - newest position and online flag;
- ``resolve_vehicle_operating_summary`` - additive F-A6 figures for a
  fleet rollup.

It orchestrates I/O and delegates the pure work to internal modules:

- ``time_windows`` validates the query windows and cuts report periods;
- ``mappers`` builds the HTTP responses and DTOs from ORM rows;
- ``reports`` computes the F-A6/F-C6 reports and the F-A3 trend;
- ``alerting`` (backed by the pure ``detection``) raises the F-A2/F-A3/F-A4
  notifications during ingestion.

Cross-domain edges owned by this module: ``vehicles`` (existence, battery
capacity, F-F2 activation) and ``telematics`` (serial -> vehicle mapping).

"Online" (planner D2) is derived at read time from the newest
``received_at`` and ``settings.TELEMETRY_ONLINE_THRESHOLD_SECONDS``; it is
never stored. Report calendars (planner D4) use
``settings.APP_REPORT_TIMEZONE``; every returned timestamp stays UTC.

Ingestion processes each message individually (``process_message``), for
low latency and per-message transaction isolation. A batched path (batch
lookup + bulk insert) is deferred until a benchmark needs it - see
``docs/01-requirements/future.md`` item 25.
"""

import logging
from datetime import datetime, timedelta
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
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.schemas import (
    TelemetryEnvelope,
    VehicleBatteryHealthResponse,
    VehicleEnergyUsageResponse,
    VehicleOperatingReportResponse,
    VehicleTelemetryHistoryResponse,
    VehicleTelemetryLatestResponse,
)
from app.domains.telemetry.types import (
    ReportGranularity,
    VehicleLiveStatusReference,
    VehicleOperatingSummary,
    VehicleTelemetryWindowSummary,
)
from app.domains.vehicles.types import VehicleReference
from app.libs.common.clock import utc_now
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

# Fold of a period without any reading: the repository omits such periods,
# and the report still lists them with zero sums.
_EMPTY_WINDOW_SUMMARY = VehicleTelemetryWindowSummary(
    soc_discharge_percent=0.0,
    soc_charge_percent=0.0,
    distance_km=0.0,
    sample_count=0,
    odometer_sample_count=0,
    first_recorded_at=None,
    last_recorded_at=None,
)


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


def _calculate_is_online(last_received_at: datetime | None) -> bool:
    """Tell whether a vehicle counts as online right now (planner D2).

    Args:
        last_received_at: Newest backend receive time among the vehicle's
            telemetry rows, or ``None`` if it never reported.

    Returns:
        ``True`` if ``last_received_at`` lies within
        ``settings.TELEMETRY_ONLINE_THRESHOLD_SECONDS`` of ``utc_now()``.
    """
    if last_received_at is None:
        return False
    online_threshold = timedelta(seconds=settings.TELEMETRY_ONLINE_THRESHOLD_SECONDS)
    return utc_now() - last_received_at <= online_threshold


async def _find_latest_telemetry_with_online_flag(
    db: AsyncSession, vehicle_id: UUID
) -> tuple[VehicleTelemetryModel, bool] | None:
    """Get a vehicle's newest reading and whether the vehicle is online.

    Two simple queries: the newest row by device ``recorded_at`` (what
    "latest reading" has always meant here) and the newest backend
    ``received_at``. The online flag uses the latter, for the reason given
    in ``resolve_last_telemetry_at``: one future-dated ``recorded_at``
    must not make a reporting vehicle look offline.

    Args:
        db: Database session owned by the caller's entry boundary.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        ``(latest_row, is_online)``, or ``None`` if the vehicle has no
        telemetry.

    Side Effects:
        Read-only.
    """
    telemetry = await telemetry_repository.get_latest_vehicle_telemetry(db, vehicle_id)
    if telemetry is None:
        return None
    last_received_at = await telemetry_repository.find_latest_received_at(
        db, vehicle_id
    )
    return telemetry, _calculate_is_online(last_received_at)


async def get_latest_vehicle_telemetry_response(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleTelemetryLatestResponse:
    """Get the latest telemetry after confirming the vehicle is still active.

    Args:
        db: Database session owned by the HTTP boundary.
        vehicle_id: Internal ID of the vehicle to query.

    Returns:
        Response schema containing the latest telemetry record, its
        ``received_at`` and the read-time ``is_online`` flag.

    Raises:
        TelemetryNotFoundError: When the vehicle does not exist or has no
            telemetry yet.
    """
    await _get_vehicle_reference(db, vehicle_id)

    latest = await _find_latest_telemetry_with_online_flag(db, vehicle_id)
    if latest is None:
        raise TelemetryNotFoundError(
            f"No telemetry found for vehicle with id '{vehicle_id}'"
        )
    telemetry, is_online = latest
    return telemetry_mappers.to_vehicle_telemetry_latest_response(
        telemetry, is_online=is_online
    )


async def resolve_vehicle_live_status(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleLiveStatusReference | None:
    """Get a vehicle's newest position and online flag. Public entry point (F-A1).

    For other domains and telemetry's own fleet views (fleet map, F-E1).
    Does not check that the vehicle exists - a vehicle without telemetry,
    known or not, simply has no live status; the caller already owns the
    vehicle list it asks about.

    Args:
        db: Async session owned by the caller's entry boundary.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The vehicle's newest position, ``recorded_at``/``received_at`` of
        that reading, ``is_online`` (newest receive time within
        ``settings.TELEMETRY_ONLINE_THRESHOLD_SECONDS``) and signal
        strength, or ``None`` if the vehicle has no telemetry.

    Side Effects:
        Read-only queries; does not commit or roll back.
    """
    latest = await _find_latest_telemetry_with_online_flag(db, vehicle_id)
    if latest is None:
        return None
    telemetry, is_online = latest
    return telemetry_mappers.to_vehicle_live_status_reference(
        telemetry, is_online=is_online
    )


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


async def get_vehicle_battery_health_response(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> VehicleBatteryHealthResponse:
    """Get a vehicle's daily battery-health trend over a window (F-A3).

    One point per calendar day of ``settings.APP_REPORT_TIMEZONE`` that
    has an SOH or cycle-count reading: the day's last reported SOH and
    cycle count, plus an estimated usable capacity when the vehicle's
    nominal capacity is recorded. Days without such a reading are
    omitted, never interpolated.

    Args:
        db: Database session owned by the HTTP boundary.
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.

    Returns:
        The daily trend over the normalized window.

    Raises:
        TelemetryInvalidRangeError: If either bound is missing a timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``settings.TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS``.
        TelemetryNotFoundError: If the vehicle does not exist or was
            soft-deleted.
    """
    normalized_start, normalized_end = telemetry_time_windows.validate_time_window(
        start_time, end_time, settings.TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS
    )
    vehicle_reference = await _get_vehicle_reference(db, vehicle_id)
    health_days = await telemetry_repository.list_vehicle_battery_health_days(
        db,
        vehicle_id=vehicle_id,
        start_time=normalized_start,
        end_time=normalized_end,
        time_zone=settings.APP_REPORT_TIMEZONE,
    )
    return telemetry_reports.build_battery_health_response(
        vehicle_reference,
        start_time=normalized_start,
        end_time=normalized_end,
        health_days=health_days,
    )


async def _resolve_report_context(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    granularity: ReportGranularity | None = None,
) -> telemetry_reports.VehicleReportContext:
    """Validate a report window, resolve the vehicle, and fold its telemetry.

    Shared by F-A6 and F-C6 - both read the same window, the same vehicle
    record (for its battery capacity), and the same single aggregate;
    only the projection into a response schema differs. With a
    ``granularity``, a second query folds the window per calendar period
    (``settings.APP_REPORT_TIMEZONE``), and every period of the window is
    listed - a period without readings gets a zero fold.

    Args:
        db: Database session owned by the HTTP boundary.
        vehicle_id: Internal ID of the vehicle to report on.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        granularity: Period breakdown to add, or ``None`` for the
            whole-window aggregate only.

    Returns:
        The validated window, the vehicle reference, the folded summary
        and, with a granularity, the folded periods.

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

    period_summaries: tuple[telemetry_reports.VehicleReportPeriodSummary, ...] = ()
    if granularity is not None:
        report_periods = telemetry_time_windows.build_report_periods(
            normalized_start,
            normalized_end,
            granularity=granularity,
            time_zone=settings.APP_REPORT_TIMEZONE,
        )
        summaries_by_bucket = await telemetry_repository.list_vehicle_period_summaries(
            db,
            vehicle_id=vehicle_id,
            start_time=normalized_start,
            end_time=normalized_end,
            granularity=granularity,
            time_zone=settings.APP_REPORT_TIMEZONE,
        )
        period_summaries = tuple(
            telemetry_reports.VehicleReportPeriodSummary(
                period=report_period,
                window_summary=summaries_by_bucket.get(
                    report_period.bucket_start, _EMPTY_WINDOW_SUMMARY
                ),
            )
            for report_period in report_periods
        )

    return telemetry_reports.VehicleReportContext(
        vehicle_reference=vehicle_reference,
        start_time=normalized_start,
        end_time=normalized_end,
        window_summary=window_summary,
        granularity=granularity,
        period_summaries=period_summaries,
    )


async def get_vehicle_operating_report(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    granularity: ReportGranularity | None = None,
) -> VehicleOperatingReportResponse:
    """Get a vehicle's operating performance over a time window (F-A6).

    Energy consumed is inferred from summed SOC drops in the vehicle's
    own telemetry (not from `charging_sessions`, which carries no vehicle
    linkage), converted to kWh via the vehicle's recorded battery
    capacity or a documented engineering default, and priced at
    ``settings.TELEMETRY_ENERGY_COST_PER_KWH_VND``. This measures gross
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
        granularity: Optional day/week/month breakdown; ``None`` keeps the
            single whole-window aggregate (``periods`` is then ``None``).

    Returns:
        The operating report over the normalized window.

    Raises:
        TelemetryInvalidRangeError: See ``_resolve_report_context``.
        TelemetryNotFoundError: See ``_resolve_report_context``.
    """
    report_context = await _resolve_report_context(
        db,
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        granularity=granularity,
    )
    return telemetry_reports.build_operating_report(report_context)


def serialize_vehicle_operating_report_csv(
    report: VehicleOperatingReportResponse,
) -> str:
    """Serialize an F-A6 operating report as CSV for the export endpoint.

    Args:
        report: Report returned by ``get_vehicle_operating_report``.

    Returns:
        CSV text: a header row, then one row per period (or one row for
        the whole window without a breakdown); UTC ISO 8601 timestamps.
    """
    return telemetry_reports.serialize_operating_report_csv(report)


async def resolve_vehicle_operating_summary(
    db: AsyncSession,
    vehicle_id: UUID,
    *,
    start_time: datetime,
    end_time: datetime,
) -> VehicleOperatingSummary:
    """Get one vehicle's additive operating figures. Public entry point (F-A6/F-E1).

    For a fleet rollup: returns only sums and counts (distance, consumed
    energy, samples) plus the capacity used, so the caller adds several
    vehicles up before deriving any rate - averaging per-vehicle rates
    would weight a parked vehicle like a busy one. Same window rules,
    vehicle lookup and fold as the F-A6 report.

    Args:
        db: Async session owned by the caller's entry boundary.
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.

    Returns:
        The vehicle's operating summary over the normalized window.

    Raises:
        TelemetryInvalidRangeError: See ``_resolve_report_context``.
        TelemetryNotFoundError: The vehicle does not exist or was
            soft-deleted.

    Side Effects:
        Read-only queries; does not commit or roll back.
    """
    report_context = await _resolve_report_context(
        db, vehicle_id=vehicle_id, start_time=start_time, end_time=end_time
    )
    return telemetry_reports.build_operating_summary(report_context)


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
            utc_now(),
            envelope.raw_payload,
        )
    except (TypeError, ValueError):
        logger.exception(
            "failed to convert message to DB dict",
            extra={
                "telematic_serial": message.telematic_serial,
                "message_uuid": str(message.message_uuid),
            },
        )
        return {"processed": 0, "skipped": 0, "errors": 1}

    processed_count = await telemetry_repository.insert_telemetry(
        db,
        telemetry_values,
    )
    # DEBUG, not INFO: the worker already logs one INFO line per message
    # ("Telemetry message processed") with the same message_uuid and the
    # full counters.
    logger.debug(
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
