"""Data access repository for the telemetry domain.

Feature code: F-A1 (Real-time vehicle telemetry ingestion)

This module contains both the singular operations used by the current MVP
flow and the batch operations kept for reuse when real throughput needs
optimization. The repository does not own the transaction: the entry
boundary passes in the session and decides whether to commit or roll back.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.types import VehicleTelemetryWindowSummary

logger = logging.getLogger(__name__)


async def insert_telemetry(
    db: AsyncSession,
    message: dict[str, object],
) -> int:
    """Insert a single telemetry record using SQLAlchemy Core.

    Args:
        db: Database session owned by the entry boundary.
        message: Data dict already converted by the service to match the
            database model.

    Returns:
        Number of rows the database reports as inserted.

    Side Effects:
        Writes one row into the current session. The function does not
        commit or roll back.
    """
    result = cast(
        CursorResult[Any],
        await db.execute(insert(VehicleTelemetryModel).values(message)),
    )
    logger.debug(
        "insert_telemetry",
        extra={"rows_inserted": result.rowcount},
    )
    return result.rowcount


async def get_latest_vehicle_telemetry(
    db: AsyncSession, vehicle_id: UUID
) -> VehicleTelemetryModel | None:
    """Get the latest telemetry record for a vehicle.

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The record with the largest `recorded_at`, or None if there is no
        data yet.
    """
    result = await db.execute(
        select(VehicleTelemetryModel)
        .where(VehicleTelemetryModel.vehicle_id == vehicle_id)
        .order_by(VehicleTelemetryModel.recorded_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_vehicle_telemetry_history(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    limit: int,
) -> list[VehicleTelemetryModel]:
    """Get telemetry records for a vehicle within a time range (F-A5).

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound, already validated and normalized
            to UTC by the service.
        end_time: Inclusive upper bound, already validated and normalized to
            UTC by the service.
        limit: Maximum number of records to return, already clamped by the
            service.

    Returns:
        Records ordered by ``recorded_at`` ascending (chronological, for
        trip replay), oldest first, capped at ``limit``.

    Side Effects:
        Reuses the existing ``ix_vehicle_telemetry_vehicle_time`` index
        (btree on ``vehicle_id, recorded_at DESC``) - PostgreSQL can scan it
        backwards for this ascending range scan, so no new index is needed.
    """
    result = await db.execute(
        select(VehicleTelemetryModel)
        .where(
            VehicleTelemetryModel.vehicle_id == vehicle_id,
            VehicleTelemetryModel.recorded_at >= start_time,
            VehicleTelemetryModel.recorded_at <= end_time,
        )
        .order_by(VehicleTelemetryModel.recorded_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_vehicle_window_summary(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> VehicleTelemetryWindowSummary:
    """Fold one vehicle's telemetry deltas over a time window (F-A6/F-C6).

    A single SQL pass computes ``lag(soc)``/``lag(odometer)`` over
    ``(PARTITION BY vehicle_id ORDER BY recorded_at)`` within the
    time-bounded window (an inner query), then sums the clamped
    positive deltas in an outer aggregate - a window function cannot be
    nested inside an aggregate in the same ``SELECT``. Reuses
    ``ix_vehicle_telemetry_vehicle_time`` for both the filter and the
    window's sort order; no new index is needed. Deliberately not built
    on ``get_vehicle_telemetry_history`` - that function's hard ``limit``
    would silently truncate a month of frequent telemetry.

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle to fold.
        start_time: Inclusive lower bound, already validated and
            normalized to UTC by the service.
        end_time: Inclusive upper bound, already validated and normalized
            to UTC by the service.

    Returns:
        The folded summary. An empty window (no rows) yields zeroed sums
        and ``None`` timestamps, never ``NULL`` sums - see the outer
        ``coalesce`` below.

    Side Effects:
        Read-only. Single SQL round trip; no I/O beyond the query.
    """
    # lag() must be computed in an inner SELECT: SQL forbids a window
    # function inside an aggregate in the same select list.
    # partition_by is redundant while the WHERE pins one vehicle, but it
    # keeps the fold correct by construction if the filter is ever
    # widened, and costs nothing - PostgreSQL proves vehicle_id constant
    # and reuses ix_vehicle_telemetry_vehicle_time's ordering instead of
    # sorting.
    previous_soc = func.lag(VehicleTelemetryModel.soc).over(
        partition_by=VehicleTelemetryModel.vehicle_id,
        order_by=VehicleTelemetryModel.recorded_at.asc(),
    )
    previous_odometer = func.lag(VehicleTelemetryModel.odometer).over(
        partition_by=VehicleTelemetryModel.vehicle_id,
        order_by=VehicleTelemetryModel.recorded_at.asc(),
    )

    deltas = (
        select(
            VehicleTelemetryModel.recorded_at.label("recorded_at"),
            VehicleTelemetryModel.odometer.label("odometer"),
            (previous_soc - VehicleTelemetryModel.soc).label("soc_drop"),
            (VehicleTelemetryModel.soc - previous_soc).label("soc_rise"),
            (VehicleTelemetryModel.odometer - previous_odometer).label(
                "odometer_delta"
            ),
        )
        .where(
            VehicleTelemetryModel.vehicle_id == vehicle_id,
            VehicleTelemetryModel.recorded_at >= start_time,
            VehicleTelemetryModel.recorded_at <= end_time,
        )
        .subquery()
    )

    # coalesce(delta, 0.0) before greatest(): the window's first row has
    # no predecessor (lag() is NULL), and an odometer the device didn't
    # report is NULL too. PostgreSQL's GREATEST happens to skip NULL
    # arguments, but relying on that non-standard behaviour would make
    # the clamp unreadable - be explicit instead.
    # greatest(..., 0.0) drops negative deltas: a drop is not a rise, a
    # rise is not a drop, and an odometer that moved backwards (device
    # reset) contributes nothing rather than a negative distance.
    # The outer coalesce covers the empty window, where sum() is NULL.
    clamped_drop = func.greatest(func.coalesce(deltas.c.soc_drop, 0.0), 0.0)
    clamped_rise = func.greatest(func.coalesce(deltas.c.soc_rise, 0.0), 0.0)
    clamped_distance = func.greatest(func.coalesce(deltas.c.odometer_delta, 0.0), 0.0)

    result = await db.execute(
        select(
            func.coalesce(func.sum(clamped_drop), 0.0),
            func.coalesce(func.sum(clamped_rise), 0.0),
            func.coalesce(func.sum(clamped_distance), 0.0),
            func.count(),
            func.count(deltas.c.odometer),
            func.min(deltas.c.recorded_at),
            func.max(deltas.c.recorded_at),
        ).select_from(deltas)
    )
    (
        soc_discharge_percent,
        soc_charge_percent,
        distance_km,
        sample_count,
        odometer_sample_count,
        first_recorded_at,
        last_recorded_at,
    ) = result.one()
    return VehicleTelemetryWindowSummary(
        soc_discharge_percent=float(soc_discharge_percent),
        soc_charge_percent=float(soc_charge_percent),
        distance_km=float(distance_km),
        sample_count=int(sample_count),
        odometer_sample_count=int(odometer_sample_count),
        first_recorded_at=first_recorded_at,
        last_recorded_at=last_recorded_at,
    )


async def bulk_insert_telemetry(
    db: AsyncSession,
    messages: Sequence[dict[str, object]],
) -> int:
    """
    Bulk insert telemetry data into the database.

    Uses SQLAlchemy Core insert (not ORM add_all) to optimize performance.

    Args:
        db: AsyncSession used to operate on the database
        messages: List of dicts, each dict is 1 row of data
                  (output from TelemetryMessage.to_vehicle_telemetry_values())

    Returns:
        Number of rows inserted

    Note:
        - Does not use ORM add_all because it is slow for large batches
        - Uses Core insert with values() to take advantage of PostgreSQL's
          bulk insert
        - The entry boundary owns the transaction and commit/rollback
    """
    if not messages:
        return 0

    # Build insert statement
    stmt = insert(VehicleTelemetryModel).values(messages)

    # Execute
    result = cast(CursorResult[Any], await db.execute(stmt))

    logger.debug(
        "bulk_insert_telemetry",
        extra={
            "rows_requested": len(messages),
            "rows_inserted": result.rowcount,
        },
    )

    return result.rowcount
