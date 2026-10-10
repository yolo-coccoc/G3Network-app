"""Review smoke tests for telemetry ingestion robustness and data reach (RV-OP1..RV-OP10).

Each `xfail(strict=True)` test asserts the correct behaviour that a reviewed
defect breaks today; once the defect is fixed the test passes, strict mode
reports it as a failure and the marker must be removed. The other tests are
guards for behaviour that already holds and must keep holding.

Nothing here touches a database or the MQTT broker: the consumer is fed
stand-in messages and the service's collaborators are monkeypatched.
"""

import asyncio
import json
import math
from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from aiomqtt import Message
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_public_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.router as telemetry_router
import app.domains.telemetry.service as telemetry_service
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import UserRole
from app.domains.telematics.types import TelematicVehicleMapping
from app.domains.telemetry.ingestion.mqtt_consumer import MQTTConsumer
from app.domains.telemetry.models import TelemetryModel
from app.domains.telemetry.schemas import TelemetryEnvelope, TelemetryMessage
from app.libs.common.clock import utc_now
from tests.builders import build_telemetry_envelope, fake_db_session
from tests.principals import build_principal

SERIAL = "TBOX-REVIEW-001"
TOPIC = f"g3network/telematics/{SERIAL}/telemetry"
INT32_MAX = 2_147_483_647
INT64_MAX = 9_223_372_036_854_775_807


def _payload(**overrides: object) -> dict[str, object]:
    """Build a valid telemetry payload, with top-level keys replaced.

    Args:
        **overrides: Top-level keys to replace or add.

    Returns:
        A JSON-ready dict the consumer accepts when no override breaks it.
    """
    payload: dict[str, object] = {
        "message_uuid": str(uuid4()),
        "telematic_serial": SERIAL,
        "recorded_at": "2026-07-25T10:30:00Z",
        "location": {"latitude": 21.0285, "longitude": 105.8542},
        "battery": {"soc": 78.5},
    }
    payload.update(overrides)
    return payload


def _encode(payload: dict[str, object]) -> bytes:
    """Encode a payload as the device would put it on the wire.

    ``json.dumps`` writes ``NaN``/``Infinity`` tokens and ``\\u0000``
    escapes, which ``json.loads`` in the consumer reads back.

    Args:
        payload: The payload.

    Returns:
        UTF-8 JSON bytes.
    """
    return json.dumps(payload).encode("utf-8")


def _message(payload: bytes, topic: str = TOPIC) -> Message:
    """Build a stand-in for an aiomqtt message.

    Args:
        payload: The raw bytes the broker delivered.
        topic: The topic it arrived on.

    Returns:
        An object exposing the `payload` and `topic` attributes the consumer reads.
    """
    return cast(Message, SimpleNamespace(payload=payload, topic=topic))


def _xfail(finding: str, reason: str) -> pytest.MarkDecorator:
    """Build the strict xfail marker of one reviewed defect.

    Args:
        finding: Review ID, e.g. ``RV-OP1``.
        reason: One line naming the defect.

    Returns:
        The marker.
    """
    return pytest.mark.xfail(strict=True, reason=f"REVIEW {finding}: {reason}")


def _assert_storable(envelope: TelemetryEnvelope) -> None:
    """Assert PostgreSQL would accept the row built from a queued envelope.

    Checks the JSONB ``raw_payload`` (no ``NaN``/``Infinity`` token, no NUL
    escape), every float column (finite), the integer columns (in range of
    their column type) and the error codes (no NUL).

    Args:
        envelope: An envelope the consumer put on the queue.
    """
    serialized = json.dumps(envelope.raw_payload, allow_nan=False)
    assert "\\u0000" not in serialized
    values = envelope.message.to_vehicle_telemetry_values(
        uuid4(), uuid4(), uuid4(), utc_now(), envelope.raw_payload
    )
    for column, value in values.items():
        if isinstance(value, float):
            assert math.isfinite(value), column
    cycle_count = values["cycle_count"]
    assert cycle_count is None or abs(cast(int, cycle_count)) <= INT32_MAX
    signal_dbm = values["signal_dbm"]
    assert signal_dbm is None or abs(cast(int, signal_dbm)) <= INT64_MAX
    for code in envelope.message.errors or []:
        assert "\x00" not in code


DEEP_NESTING = b"[" * 100_000 + b"]" * 100_000
HUGE_INTEGER = b'{"a": ' + b"1" * 5_000 + b"}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            DEEP_NESTING,
            id="deeply-nested-json",
        ),
        pytest.param(
            HUGE_INTEGER,
            id="integer-over-4300-digits",
        ),
        pytest.param(
            _encode(_payload(recorded_at="0001-01-01T00:00:00+05:00")),
            id="recorded-at-year-1-with-offset",
        ),
        pytest.param(
            _encode(_payload(recorded_at="9999-12-31T23:00:00-05:00")),
            id="recorded-at-year-9999-with-offset",
        ),
    ],
)
async def test_consumer_drops_hostile_payload_without_stopping_ingestion(
    payload: bytes,
) -> None:
    """A hostile payload is dropped; nothing escapes to end the consume loop.

    An exception leaving `_handle_message` ends `start_consuming` and, with
    it, the whole ingestion process, for every vehicle.
    """
    queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue()
    consumer = MQTTConsumer(queue=queue)

    await consumer._handle_message(_message(payload))

    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\xfe\x00 not utf-8",
        b"{not json",
        b"[1, 2, 3]",
        b"null",
        _encode({"a": 1}),
        _encode(_payload(location={"latitude": float("nan"), "longitude": 105.0})),
        _encode(_payload(battery={"soc": 150})),
        _encode(_payload(recorded_at="2026-07-25T10:30:00")),
    ],
    ids=[
        "invalid-utf8",
        "invalid-json",
        "json-array",
        "json-null",
        "schema-mismatch",
        "nan-latitude",
        "soc-out-of-range",
        "naive-recorded-at",
    ],
)
async def test_consumer_drops_malformed_payload_without_raising_guard(
    payload: bytes,
) -> None:
    """Guard: malformed, non-UTF-8 and schema-invalid payloads are dropped quietly."""
    queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue()
    consumer = MQTTConsumer(queue=queue)

    await consumer._handle_message(_message(payload))

    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_payload(), id="clean-payload"),
        pytest.param(
            _payload(battery={"soc": 50.0, "temperature": float("nan")}),
            id="nan-battery-temperature",
        ),
        pytest.param(
            _payload(battery={"soc": 50.0, "current": float("nan")}),
            id="nan-battery-current",
        ),
        pytest.param(
            _payload(motor={"temperature": float("nan")}),
            id="nan-motor-temperature",
        ),
        pytest.param(
            _payload(battery={"soc": 50.0, "voltage": float("inf")}),
            id="infinite-voltage",
        ),
        pytest.param(
            _payload(vehicle_state={"odometer": float("inf")}),
            id="infinite-odometer",
        ),
        pytest.param(
            _payload(errors=["E\x00"]),
            id="nul-in-error-code",
        ),
        pytest.param(
            _payload(vendor_extra="a\x00"),
            id="nul-in-unknown-key",
        ),
        pytest.param(
            _payload(battery={"soc": 50.0, "cycle_count": 99_999_999_999}),
            id="cycle-count-over-int32",
        ),
        pytest.param(
            _payload(signal={"strength": 10**20}),
            id="signal-over-int64",
        ),
    ],
)
async def test_consumer_never_queues_a_payload_postgresql_would_reject(
    payload: dict[str, object],
) -> None:
    """Every envelope that reaches the worker can be inserted.

    A value PostgreSQL refuses makes the insert raise, the worker re-raises
    (MVP policy) and the ingestion process exits: dropping or cleaning such a
    payload at the consumer is the correct behaviour.
    """
    queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue()
    consumer = MQTTConsumer(queue=queue)

    await consumer._handle_message(_message(_encode(payload)))

    while not queue.empty():
        _assert_storable(queue.get_nowait())


@pytest.mark.asyncio
async def test_consumer_drops_payload_whose_serial_differs_from_its_topic() -> None:
    """A device may only report for itself: the topic serial must match the payload.

    The broker ACL lets each device publish to its own topic only; trusting
    the payload serial lets any device write another truck's telemetry.
    """
    queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue()
    consumer = MQTTConsumer(queue=queue)

    await consumer._handle_message(
        _message(
            _encode(_payload(telematic_serial="TBOX-VICTIM-999")),
            topic="g3network/telematics/TBOX-ATTACKER-001/telemetry",
        )
    )

    assert queue.empty()


@pytest.mark.asyncio
async def test_consumer_queues_payload_whose_serial_matches_its_topic_guard() -> None:
    """Guard: a valid payload on its own device's topic is queued with its serial."""
    queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue()
    consumer = MQTTConsumer(queue=queue)

    await consumer._handle_message(_message(_encode(_payload())))

    assert queue.get_nowait().message.telematic_serial == SERIAL


@pytest.mark.asyncio
async def test_process_message_skips_unknown_serial_without_error_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: a serial with no mapping is skipped, nothing is inserted, no error."""
    inserted: list[dict[str, object]] = []

    async def no_mapping(db: AsyncSession, serial: str) -> None:
        return None

    async def insert_telemetry(db: AsyncSession, values: dict[str, object]) -> int:
        inserted.append(values)
        return 1

    monkeypatch.setattr(
        telematics_public_service, "resolve_mapping_by_serial", no_mapping
    )
    monkeypatch.setattr(telemetry_repository, "insert_telemetry", insert_telemetry)

    result = await telemetry_service.process_message(
        fake_db_session(), build_telemetry_envelope()
    )

    assert result == {"processed": 0, "skipped": 1, "errors": 0}
    assert inserted == []


@pytest.mark.asyncio
async def test_far_future_recorded_at_is_never_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reading dated a day ahead of the backend clock is refused or skipped.

    Stored, it stays the "latest" and "previous" reading for good (both are
    picked by the largest `recorded_at`): the live view freezes, every later
    message alerts against it and the check-in distance check goes blind.
    The test accepts either fix: the schema rejects it, or the service does
    not insert it.
    """
    future = utc_now() + timedelta(days=1)
    try:
        message = TelemetryMessage.model_validate(
            _payload(recorded_at=future.isoformat())
        )
    except ValidationError:
        return
    envelope = TelemetryEnvelope(message=message, raw_payload={"test": True})
    mapping = TelematicVehicleMapping(
        telematic_id=uuid4(), vehicle_id=uuid4(), organization_id=uuid4()
    )
    inserted: list[dict[str, object]] = []

    async def resolve_mapping(db: AsyncSession, serial: str) -> TelematicVehicleMapping:
        return mapping

    async def no_previous(
        db: AsyncSession, vehicle_id: UUID, before: object
    ) -> TelemetryModel | None:
        return None

    async def insert_telemetry(db: AsyncSession, values: dict[str, object]) -> int:
        inserted.append(values)
        return 1

    monkeypatch.setattr(
        telematics_public_service, "resolve_mapping_by_serial", resolve_mapping
    )
    monkeypatch.setattr(
        telemetry_repository, "get_previous_vehicle_telemetry", no_previous
    )
    monkeypatch.setattr(telemetry_repository, "insert_telemetry", insert_telemetry)

    await telemetry_service.process_message(fake_db_session(), envelope)

    assert inserted == []


@pytest.mark.asyncio
@_xfail("RV-OP10", "MON-14 lets a DRIVER-only caller through the fleet report gate")
async def test_driver_only_caller_cannot_open_the_fleet_operating_report() -> None:
    """A DRIVER sees their own truck, never every truck's figures in a fleet."""
    driver = build_principal(roles=frozenset({UserRole.DRIVER}))

    with pytest.raises(AccessDeniedError):
        await telemetry_router.FLEET_REPORT_READERS(principal=driver)


@pytest.mark.asyncio
async def test_fleet_manager_opens_the_fleet_operating_report_guard() -> None:
    """Guard: a fleet manager passes the fleet report gate."""
    manager = build_principal(roles=frozenset({UserRole.FLEET_MANAGER}))

    assert await telemetry_router.FLEET_REPORT_READERS(principal=manager) is manager
