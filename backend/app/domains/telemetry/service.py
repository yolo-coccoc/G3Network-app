"""Business service for the telemetry domain.

Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-A2 (Tiered
battery alerts)

The current MVP flow processes each message individually to reduce latency
and isolate transactions. The batch functions are kept as-is in the module
because a later phase may need to optimize throughput with batch lookup and
bulk insert. F-A2's threshold detection only runs in the per-message flow
(``process_message``) - ``process_batch``/``batch_worker.py`` are dormant and
not part of the current process lifecycle.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.service as charging_stations_service
import app.domains.notifications.service as notifications_service
import app.domains.telematics.service as telematics_service
import app.domains.telemetry.repository as telemetry_repository
from app.domains.notifications.types import NotificationType
from app.domains.telemetry.exceptions import TelemetryNotFoundError
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.schemas import (
    TelemetryEnvelope,
    VehicleTelemetryLatestResponse,
)
from app.domains.telemetry.types import (
    BATTERY_ALERT_THRESHOLDS,
    BatteryAlertLevel,
)
from app.domains.vehicles import service as vehicle_service
from app.libs.common.geo import location_to_coordinates

logger = logging.getLogger(__name__)


def to_vehicle_telemetry_latest_response(
    telemetry: VehicleTelemetryModel,
) -> VehicleTelemetryLatestResponse:
    """Build the latest-telemetry response from the ORM model.

    Pure mapping only, no I/O. Built explicitly (rather than
    ``VehicleTelemetryLatestResponse.model_validate(telemetry,
    from_attributes=True)``) because the ORM model stores GPS as a single
    ``location`` geography point while the response still exposes plain
    ``latitude``/``longitude`` fields - the two no longer line up 1:1 by
    attribute name.

    Args:
        telemetry: Telemetry ORM object queried by the repository.

    Returns:
        Response schema with latitude/longitude decoded from ``location``.
    """
    latitude, longitude = location_to_coordinates(telemetry.location)
    # location_to_coordinates()'s return type is generic (Optional, since
    # charging_stations.location can be null) - vehicle_telemetry.location
    # is NOT NULL, so this pair is never actually missing; the assertion
    # documents that invariant for both mypy and a future reader.
    assert (
        latitude is not None and longitude is not None
    ), "vehicle_telemetry.location is NOT NULL"
    return VehicleTelemetryLatestResponse(
        vehicle_id=telemetry.vehicle_id,
        telematic_serial=telemetry.telematic_serial,
        recorded_at=telemetry.recorded_at,
        latitude=latitude,
        longitude=longitude,
        speed=telemetry.speed,
        heading=telemetry.heading,
        soc=telemetry.soc,
        battery_voltage=telemetry.battery_voltage,
        battery_current=telemetry.battery_current,
        battery_temperature=telemetry.battery_temperature,
        motor_temperature=telemetry.motor_temperature,
        odometer=telemetry.odometer,
        signal_strength=telemetry.signal_strength,
        error_codes=telemetry.error_codes,
        schema_version=telemetry.schema_version,
    )


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
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db,
        vehicle_id,
    )
    if vehicle_reference is None:
        raise TelemetryNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    telemetry = await telemetry_repository.get_latest_vehicle_telemetry(db, vehicle_id)
    if telemetry is None:
        raise TelemetryNotFoundError(
            f"No telemetry found for vehicle with id '{vehicle_id}'"
        )

    return to_vehicle_telemetry_latest_response(telemetry)


def detect_battery_alert_level(
    previous_soc: float | None, current_soc: float
) -> BatteryAlertLevel | None:
    """Detect whether SOC just crossed a tiered alert threshold (F-A2).

    Pure function, no I/O. A crossing is ``previous_soc > threshold >=
    current_soc`` - strict on the previous side, inclusive on the current
    side. The asymmetry is load-bearing: it makes a reading of exactly the
    threshold alert once and only once. If both sides were inclusive, a
    vehicle resting at exactly the threshold would alert on every message it
    sends while parked there.

    Args:
        previous_soc: The vehicle's previous SOC reading (%), or ``None`` if
            this is the first telemetry ever recorded for the vehicle (in
            which case no alert is ever raised, regardless of how low
            ``current_soc`` is).
        current_soc: The current message's SOC reading (%).

    Returns:
        The most severe level crossed by this single reading (a gap that
        skips multiple thresholds, e.g. 35% to 8%, still raises exactly one
        alert), or ``None`` if no threshold was crossed downward.
    """
    if previous_soc is None:
        return None
    crossed_level: BatteryAlertLevel | None = None
    for level, threshold in BATTERY_ALERT_THRESHOLDS.items():
        if previous_soc > threshold.threshold_percent >= current_soc:
            # Iterates least to most severe; keep the last match so a
            # multi-threshold drop resolves to the most severe one crossed.
            crossed_level = level
    return crossed_level


async def _raise_battery_alert(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    alert_level: BatteryAlertLevel,
    current_soc: float,
    latitude: float,
    longitude: float,
) -> None:
    """Resolve the nearest operational station and raise a battery alert.

    Args:
        db: Session whose transaction is owned by the worker.
        vehicle_id: Vehicle the alert is about.
        alert_level: Level returned by ``detect_battery_alert_level``.
        current_soc: SOC (%) that triggered the alert.
        latitude: Vehicle's GPS latitude at the triggering message.
        longitude: Vehicle's GPS longitude at the triggering message.

    Side Effects:
        Writes one notification row into the session; does not commit. The
        nearest-station lookup is a snapshot taken now, from the vehicle's
        GPS at this exact message - it is not recomputed later, so it
        describes where the vehicle was when it crossed the threshold, not
        where it currently is.
    """
    threshold = BATTERY_ALERT_THRESHOLDS[alert_level]
    nearest_station = await charging_stations_service.find_nearest_operational_station(
        db, latitude=latitude, longitude=longitude
    )
    payload: dict[str, object] = {
        "threshold_percent": threshold.threshold_percent,
        "soc": current_soc,
        "station_id": (
            str(nearest_station.station_id) if nearest_station is not None else None
        ),
        "station_name": (
            nearest_station.display_name if nearest_station is not None else None
        ),
        "distance_km": (
            nearest_station.distance_km if nearest_station is not None else None
        ),
    }
    await notifications_service.create_notification(
        db,
        notification_type=NotificationType.BATTERY_ALERT,
        severity=threshold.severity,
        vehicle_id=vehicle_id,
        title=f"Battery at {current_soc:.0f}% ({alert_level.value.title()})",
        body=(
            f"Vehicle battery dropped to {current_soc:.1f}%, crossing the "
            f"{threshold.threshold_percent:.0f}% threshold."
        ),
        payload=payload,
    )
    logger.info(
        "battery alert raised",
        extra={
            "vehicle_id": str(vehicle_id),
            "alert_level": alert_level.value,
            "soc": current_soc,
        },
    )


class BatchResult(TypedDict):
    """Counters returned after processing a telemetry batch."""

    processed: int
    skipped: int
    errors: int


class MessageResult(TypedDict):
    """Counters returned after processing a telemetry message."""

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
    - After a successful insert, F-A2 battery-threshold detection runs
      against the vehicle's previous SOC reading; a crossing raises exactly
      one notification (see ``detect_battery_alert_level``). This never
      affects ``processed``/``skipped``/``errors`` - it is reported via a
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
        May write one row into the session and emit a structured log. The
        function does not commit or roll back.
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
    # the actual previous reading, and the crossing could never be detected.
    previous_telemetry = await telemetry_repository.get_latest_vehicle_telemetry(
        db, vehicle_id
    )
    previous_soc = previous_telemetry.soc if previous_telemetry is not None else None

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

    alert_level = detect_battery_alert_level(previous_soc, message.battery.soc)
    if alert_level is not None:
        await _raise_battery_alert(
            db,
            vehicle_id=vehicle_id,
            alert_level=alert_level,
            current_soc=message.battery.soc,
            latitude=message.location.latitude,
            longitude=message.location.longitude,
        )

    return {"processed": processed_count, "skipped": 0, "errors": 0}


async def process_batch(
    db: AsyncSession, messages: Sequence[TelemetryEnvelope]
) -> BatchResult:
    """
    Process a batch of telemetry messages.

    Business logic:
    1. Get the list of unique telematic_serial values from the batch
    2. Look up telematic_id and vehicle_id from the repository (batch query)
    3. For each message:
       - If telematic_serial does not exist: log warning, skip
       - If vehicle_id is None: log warning, skip
       - Convert to a DB dict
    4. Bulk insert into the database

    Args:
        db: AsyncSession used to operate on the database
        messages: List of TelemetryMessage to process

    Returns:
        Dict with keys:
        - processed: number of messages processed successfully
        - skipped: number of messages skipped (telematic not found, vehicle
          not yet assigned)
        - errors: number of messages that had errors

    Note:
        - Batch lookup: 1 query for the whole batch, not one query per message
        - No cache used in the MVP
        - If a DB error occurs: raise so the batch worker can handle it
    """
    if not messages:
        return {"processed": 0, "skipped": 0, "errors": 0}

    logger.info("process_batch started", extra={"batch_size": len(messages)})

    # Step 1: Get the list of unique telematic_serial values from the batch
    unique_serials = list(
        set(envelope.message.telematic_serial for envelope in messages)
    )

    # Step 2: Batch lookup telematic mappings (1 query for the whole batch)
    # The mapping has been normalized into a DTO to avoid unclear tuple unpacking.
    telematic_mappings = await telematics_service.resolve_mappings_by_serial(
        db, unique_serials
    )

    # Step 3: Process each message
    valid_messages = []
    skipped_count = 0
    error_count = 0

    # Timestamp when the backend received the message (same timestamp for the whole batch)
    received_at = datetime.now(timezone.utc)

    for envelope in messages:
        message = envelope.message
        serial = message.telematic_serial

        # Check whether telematic_serial exists in the mapping
        if serial not in telematic_mappings:
            logger.warning(
                "telematic_serial not found, skipping message",
                extra={
                    "telematic_serial": serial,
                    "message_uuid": str(message.message_uuid),
                },
            )
            skipped_count += 1
            continue

        mapping = telematic_mappings[serial]
        telematic_id = mapping.telematic_id
        vehicle_id = mapping.vehicle_id

        # Check whether vehicle_id has been assigned
        # (telematics.service.resolve_mappings_by_serial already filters
        # vehicle_id IS NOT NULL, but check again to be sure)
        if vehicle_id is None:
            logger.warning(
                "vehicle_id is None for telematic, skipping message",
                extra={
                    "telematic_serial": serial,
                    "telematic_id": str(telematic_id),
                    "message_uuid": str(message.message_uuid),
                },
            )
            skipped_count += 1
            continue

        # Convert message to DB dict
        try:
            telemetry_values = message.to_vehicle_telemetry_values(
                telematic_id,
                vehicle_id,
                received_at,
                envelope.raw_payload,
            )
            valid_messages.append(telemetry_values)

        except (TypeError, ValueError) as error:
            logger.exception(
                "failed to convert message to DB dict",
                extra={
                    "telematic_serial": serial,
                    "message_uuid": str(message.message_uuid),
                    "error": str(error),
                },
            )
            error_count += 1
            continue

    # Step 4: Bulk insert into the database
    processed_count = 0
    if valid_messages:
        processed_count = await telemetry_repository.bulk_insert_telemetry(
            db, valid_messages
        )
        logger.info(
            "bulk_insert_telemetry completed",
            extra={
                "rows_inserted": processed_count,
                "rows_requested": len(valid_messages),
            },
        )

    # Log summary
    logger.info(
        "process_batch completed",
        extra={
            "processed": processed_count,
            "skipped": skipped_count,
            "total": len(messages),
        },
    )

    return {
        "processed": processed_count,
        "skipped": skipped_count,
        "errors": error_count,
    }
