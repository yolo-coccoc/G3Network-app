"""Business service for the telemetry domain.

Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-A2 (Tiered
battery alerts), F-A4 (Anomaly detection)

The current MVP flow processes each message individually to reduce latency
and isolate transactions. The batch functions are kept as-is in the module
because a later phase may need to optimize throughput with batch lookup and
bulk insert. F-A2's threshold detection and F-A4's anomaly detection only run
in the per-message flow (``process_message``) - ``process_batch``/
``batch_worker.py`` are dormant and not part of the current process
lifecycle.
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
    TelemetryMessage,
    VehicleTelemetryLatestResponse,
)
from app.domains.telemetry.types import (
    BATTERY_ALERT_THRESHOLDS,
    HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS,
    VEHICLE_ANOMALY_SEVERITIES,
    VOLTAGE_DROP_THRESHOLD_VOLTS,
    BatteryAlertLevel,
    VehicleAnomaly,
    VehicleAnomalyType,
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


def detect_high_battery_temperature(
    previous_celsius: float | None, current_celsius: float | None
) -> VehicleAnomaly | None:
    """Detect a battery temperature entering the high-temperature anomaly range (F-A4).

    Pure function, no I/O. This is a level condition, not a one-time
    crossing like F-A2's SOC thresholds - a vehicle resting above the
    threshold would alert on every message if this fired on "at or above the
    threshold" alone. It only fires on *entry*: unlike
    ``detect_battery_alert_level``, a missing previous reading is treated as
    "below threshold" rather than suppressing the alert - a fire-safety
    anomaly must not be silently skipped just because it is the vehicle's
    first message. It stays silent while the reading remains above the
    threshold, and re-arms once the reading recovers below it.

    Args:
        previous_celsius: The vehicle's previous battery temperature
            reading, or ``None`` if this is the first reading or the device
            didn't report it.
        current_celsius: The current message's battery temperature reading,
            or ``None`` if the device didn't report it (in which case
            nothing can be detected).

    Returns:
        A ``HIGH_BATTERY_TEMPERATURE`` anomaly on entry into the high range,
        or ``None``.
    """
    if current_celsius is None:
        return None
    was_below = (
        previous_celsius is None
        or previous_celsius < HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS
    )
    if not (
        was_below and current_celsius >= HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS
    ):
        return None
    return VehicleAnomaly(
        anomaly_type=VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE,
        severity=VEHICLE_ANOMALY_SEVERITIES[
            VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE
        ],
        evidence={
            "threshold_celsius": HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS,
            "observed_celsius": current_celsius,
        },
    )


def detect_sudden_voltage_drop(
    previous_volts: float | None, current_volts: float | None
) -> VehicleAnomaly | None:
    """Detect a sudden absolute drop in battery voltage between readings (F-A4).

    Pure function, no I/O. Undefined without both readings, unlike the
    high-temperature detector - a drop is a comparison between two points,
    so a missing previous reading (first message, or device didn't report
    voltage) means nothing can be said, and this stays silent rather than
    guessing a baseline.

    Args:
        previous_volts: The vehicle's previous battery voltage reading, or
            ``None``.
        current_volts: The current message's battery voltage reading, or
            ``None``.

    Returns:
        A ``SUDDEN_VOLTAGE_DROP`` anomaly if the drop meets or exceeds
        ``VOLTAGE_DROP_THRESHOLD_VOLTS``, or ``None``.
    """
    if previous_volts is None or current_volts is None:
        return None
    drop_volts = previous_volts - current_volts
    if drop_volts < VOLTAGE_DROP_THRESHOLD_VOLTS:
        return None
    return VehicleAnomaly(
        anomaly_type=VehicleAnomalyType.SUDDEN_VOLTAGE_DROP,
        severity=VEHICLE_ANOMALY_SEVERITIES[VehicleAnomalyType.SUDDEN_VOLTAGE_DROP],
        evidence={
            "threshold_volts": VOLTAGE_DROP_THRESHOLD_VOLTS,
            "previous_volts": previous_volts,
            "current_volts": current_volts,
            "drop_volts": drop_volts,
        },
    )


def detect_new_error_codes(
    previous_codes: Sequence[str] | None, current_codes: Sequence[str] | None
) -> VehicleAnomaly | None:
    """Detect a device error code that wasn't present in the previous reading (F-A4).

    Pure function, no I/O. F-A4 names "cell/module fault" and "motor fault"
    as separate triggers, but the MQTT contract only carries opaque error
    code strings with no vendor catalog to map a code to one or the other -
    see ``VehicleAnomalyType.DEVICE_FAULT``'s docstring and
    ``docs/01-requirements/future.md``. Only *newly appearing* codes fire an
    anomaly; a code that was already active on the previous reading (still
    faulted, not a new fault) or one that cleared does not.

    Args:
        previous_codes: Error codes active on the vehicle's previous
            reading, or ``None`` if there were none.
        current_codes: Error codes active on the current reading, or
            ``None`` if there are none.

    Returns:
        A ``DEVICE_FAULT`` anomaly listing the newly appeared codes, or
        ``None`` if there are none.
    """
    if not current_codes:
        return None
    new_codes = sorted(set(current_codes) - set(previous_codes or []))
    if not new_codes:
        return None
    return VehicleAnomaly(
        anomaly_type=VehicleAnomalyType.DEVICE_FAULT,
        severity=VEHICLE_ANOMALY_SEVERITIES[VehicleAnomalyType.DEVICE_FAULT],
        evidence={
            "new_codes": new_codes,
            "active_codes": sorted(current_codes),
        },
    )


def detect_vehicle_anomalies(
    previous_telemetry: VehicleTelemetryModel | None,
    message: TelemetryMessage,
) -> list[VehicleAnomaly]:
    """Run every F-A4 detector against one telemetry reading.

    Pure function, no I/O. A single message may legitimately trip more than
    one detector (e.g. a battery fire event could show both high temperature
    and a new fault code), so every detector runs independently and all
    results are returned.

    Args:
        previous_telemetry: The vehicle's previous telemetry row, already
            queried by the caller, or ``None`` for the vehicle's first
            reading.
        message: The current message already validated by Pydantic.

    Returns:
        Every anomaly detected in this reading, in detector-declaration
        order (temperature, voltage, then fault codes); empty if none.
    """
    previous_temperature = (
        previous_telemetry.battery_temperature if previous_telemetry else None
    )
    previous_voltage = (
        previous_telemetry.battery_voltage if previous_telemetry else None
    )
    # error_codes is stored as {"codes": [...]} JSONB, or None.
    previous_error_codes = (
        previous_telemetry.error_codes.get("codes")
        if previous_telemetry and previous_telemetry.error_codes
        else None
    )

    anomalies: list[VehicleAnomaly] = []
    temperature_anomaly = detect_high_battery_temperature(
        previous_temperature,
        message.battery.temperature,
    )
    if temperature_anomaly is not None:
        anomalies.append(temperature_anomaly)
    voltage_anomaly = detect_sudden_voltage_drop(
        previous_voltage,
        message.battery.voltage,
    )
    if voltage_anomaly is not None:
        anomalies.append(voltage_anomaly)
    fault_anomaly = detect_new_error_codes(previous_error_codes, message.errors)
    if fault_anomaly is not None:
        anomalies.append(fault_anomaly)
    return anomalies


def to_telemetry_snapshot(message: TelemetryMessage) -> dict[str, object]:
    """Build a JSONB-safe data snapshot of a telemetry message (F-A4).

    Pure mapping, no I/O. This is the "event log with a data snapshot" F-A4
    asks for - stored inside the anomaly notification's ``payload`` rather
    than a separate table (see ``docs/02-planners/backend-anomaly-detection.md``).
    Only JSON-serializable values are included (``UUID``/``datetime`` are
    converted to strings) since ``payload`` is a JSONB column.

    Args:
        message: The telemetry message the anomaly was detected in.

    Returns:
        A flat dict of the message's fields relevant to investigating an
        anomaly.
    """
    return {
        "message_uuid": str(message.message_uuid),
        "recorded_at": message.recorded_at.isoformat(),
        "latitude": message.location.latitude,
        "longitude": message.location.longitude,
        "speed": message.vehicle_state.speed if message.vehicle_state else None,
        "odometer": message.vehicle_state.odometer if message.vehicle_state else None,
        "soc": message.battery.soc,
        "battery_voltage": message.battery.voltage,
        "battery_current": message.battery.current,
        "battery_temperature": message.battery.temperature,
        "motor_temperature": message.motor.temperature if message.motor else None,
        "error_codes": message.errors,
        "schema_version": message.schema_version,
    }


_ANOMALY_TITLES: dict[VehicleAnomalyType, str] = {
    VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE: "High battery temperature detected",
    VehicleAnomalyType.SUDDEN_VOLTAGE_DROP: "Sudden battery voltage drop detected",
    VehicleAnomalyType.DEVICE_FAULT: "Device fault code reported",
}


async def _raise_vehicle_anomaly_alert(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    anomaly: VehicleAnomaly,
    message: TelemetryMessage,
) -> None:
    """Raise an F-A4 anomaly notification carrying evidence and a data snapshot.

    Args:
        db: Session whose transaction is owned by the worker.
        vehicle_id: Vehicle the anomaly was detected on.
        anomaly: Anomaly already detected by ``detect_vehicle_anomalies``.
        message: The telemetry message the anomaly was detected in, used to
            build the stored snapshot.

    Side Effects:
        Writes one notification row into the session; does not commit.
    """
    payload: dict[str, object] = {
        "anomaly_type": anomaly.anomaly_type.value,
        "evidence": anomaly.evidence,
        "snapshot": to_telemetry_snapshot(message),
    }
    await notifications_service.create_notification(
        db,
        notification_type=NotificationType.ANOMALY_ALERT,
        severity=anomaly.severity,
        vehicle_id=vehicle_id,
        title=_ANOMALY_TITLES[anomaly.anomaly_type],
        body=(
            f"Vehicle anomaly '{anomaly.anomaly_type.value}' detected with "
            f"evidence {anomaly.evidence}."
        ),
        payload=payload,
    )
    logger.info(
        "vehicle anomaly alert raised",
        extra={
            "vehicle_id": str(vehicle_id),
            "anomaly_type": anomaly.anomaly_type.value,
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
      one notification (see ``detect_battery_alert_level``). F-A4 anomaly
      detection also runs against the same previous reading; a message may
      trip zero, one, or more anomaly detectors, each raising its own
      notification (see ``detect_vehicle_anomalies``). Neither ever affects
      ``processed``/``skipped``/``errors`` - each is reported via a separate
      structured log line.

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
    # Kept as the full ORM row (not just .soc) since F-A4's detectors also
    # need the previous temperature/voltage/error codes.
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

    for anomaly in detect_vehicle_anomalies(previous_telemetry, message):
        await _raise_vehicle_anomaly_alert(
            db,
            vehicle_id=vehicle_id,
            anomaly=anomaly,
            message=message,
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
