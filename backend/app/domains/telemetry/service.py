"""Business service for the telemetry domain.

Feature code: F-A1 (Real-time vehicle telemetry ingestion)

The current MVP flow processes each message individually to reduce latency
and isolate transactions. The batch functions are kept as-is in the module
because a later phase may need to optimize throughput with batch lookup and
bulk insert.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_service
import app.domains.telemetry.repository as telemetry_repository
from app.domains.telemetry.exceptions import TelemetryNotFoundError
from app.domains.telemetry.schemas import (
    TelemetryEnvelope,
    VehicleTelemetryLatestResponse,
)
from app.domains.vehicles import service as vehicle_service

logger = logging.getLogger(__name__)


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

    return VehicleTelemetryLatestResponse.model_validate(telemetry)


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
