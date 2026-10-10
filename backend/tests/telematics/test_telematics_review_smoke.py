"""Review smoke tests for the T-Box status consumer and device serials (RV-OP1, RV-OP3, RV-OP11).

Each `xfail(strict=True)` test asserts the correct behaviour that a reviewed
defect breaks today; once the defect is fixed the test passes, strict mode
reports it as a failure and the marker must be removed. The other tests are
guards for behaviour that already holds.

Nothing here touches a database or the MQTT broker.
"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from aiomqtt import Message
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.repository as telematics_repository
import app.domains.telematics.service as telematics_service
from app.domains.telematics.ingestion.mqtt_consumer import StatusReportConsumer
from app.domains.telematics.models import TelematicModel, TelematicStatusReportModel
from app.domains.telematics.schemas import (
    TelematicCreateRequest,
    TelematicStatusEnvelope,
    TelematicStatusMessage,
    TelematicUpdateRequest,
)
from tests.builders import fake_db_session

STATUS_TOPIC = "g3network/telematics/TBOX-REVIEW-001/status"


def _message(payload: bytes, topic: str = STATUS_TOPIC) -> Message:
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            b"[" * 100_000 + b"]" * 100_000,
            id="deeply-nested-json",
        ),
        pytest.param(
            b'{"signal_dbm": ' + b"1" * 5_000 + b"}",
            id="integer-over-4300-digits",
        ),
    ],
)
async def test_status_consumer_drops_hostile_payload_without_stopping_ingestion(
    payload: bytes,
) -> None:
    """A hostile status payload is dropped; nothing escapes to end the consume loop."""
    queue: asyncio.Queue[TelematicStatusEnvelope] = asyncio.Queue()
    consumer = StatusReportConsumer(queue=queue)

    await consumer._handle_message(_message(payload))

    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "topic"),
    [
        (b"\xff\xfe\x00 not utf-8", STATUS_TOPIC),
        (b"{not json", STATUS_TOPIC),
        (b"[1, 2, 3]", STATUS_TOPIC),
        (b'"online"', STATUS_TOPIC),
        (b'{"firmware_version": "1.0"}', "g3network/telematics//status"),
        (b'{"firmware_version": "1.0"}', "g3network/telematics/A/B/status"),
    ],
    ids=[
        "invalid-utf8",
        "invalid-json",
        "json-array",
        "json-string",
        "topic-without-serial",
        "topic-with-extra-level",
    ],
)
async def test_status_consumer_drops_malformed_message_without_raising_guard(
    payload: bytes, topic: str
) -> None:
    """Guard: malformed payloads and topics without a serial are dropped quietly."""
    queue: asyncio.Queue[TelematicStatusEnvelope] = asyncio.Queue()
    consumer = StatusReportConsumer(queue=queue)

    await consumer._handle_message(_message(payload, topic))

    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"firmware_version": "1.2.3", "gnss_status": "FIX"}, id="clean"),
        pytest.param(
            {"firmware_version": "1.2\x003"},
            id="nul-in-firmware-version",
        ),
        pytest.param(
            {"gnss_status": "FI\x00X"},
            id="nul-in-gnss-status",
        ),
        pytest.param(
            {"sim": {"iccid": "8984\x00049"}},
            id="nul-in-sim-iccid",
        ),
        pytest.param(
            {"firmware_version": "1.0", "timestamp": "0001-01-01T00:00:00+05:00"},
            id="timestamp-year-1-with-offset",
        ),
    ],
)
async def test_status_consumer_never_queues_a_report_postgresql_would_reject(
    payload: dict[str, object],
) -> None:
    """Every status report the consumer queues can be inserted.

    A NUL byte in text or a time that cannot be converted to UTC makes the
    insert raise and the status worker stop the process; such a value must
    be dropped or cleaned before the queue.
    """
    queue: asyncio.Queue[TelematicStatusEnvelope] = asyncio.Queue()
    consumer = StatusReportConsumer(queue=queue)

    await consumer._handle_message(_message(json.dumps(payload).encode("utf-8")))

    while not queue.empty():
        envelope = queue.get_nowait()
        values = envelope.message.to_status_report_values(
            uuid4(), datetime.now(timezone.utc)
        )
        for column, value in values.items():
            if isinstance(value, str):
                assert "\x00" not in value, column
        reported_at = cast(datetime, values["reported_at"])
        reported_at.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_status_report_of_unknown_serial_is_skipped_without_error_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: a report whose topic serial matches no device is skipped, not an error."""
    inserted: list[dict[str, object]] = []

    async def no_device(db_session: AsyncSession, serial: str) -> TelematicModel | None:
        return None

    async def insert_report(
        db_session: AsyncSession, values: dict[str, object]
    ) -> TelematicStatusReportModel:
        inserted.append(values)
        return TelematicStatusReportModel(**values)

    monkeypatch.setattr(telematics_repository, "find_by_serial", no_device)
    monkeypatch.setattr(telematics_repository, "insert_status_report", insert_report)

    result = await telematics_service.record_status_report(
        fake_db_session(),
        TelematicStatusEnvelope(
            "TBOX-UNKNOWN",
            TelematicStatusMessage.model_validate({"firmware_version": "1.0"}),
        ),
    )

    assert result == {"processed": 0, "skipped": 1, "errors": 0}
    assert inserted == []


@pytest.mark.parametrize(
    "serial",
    [
        pytest.param(
            "TBOX/../001",
            id="slash",
        ),
        pytest.param(
            "TBOX+001",
            id="single-level-wildcard",
        ),
        pytest.param(
            "TBOX#001",
            id="multi-level-wildcard",
        ),
    ],
)
def test_device_serial_rejects_mqtt_topic_characters(serial: str) -> None:
    """A serial becomes one MQTT topic level, so '/', '+' and '#' are refused.

    Checked on both create and update; paho refuses to publish to a topic
    with a wildcard, and a '/' puts the device on a topic its ACL never
    matches.
    """
    with pytest.raises(ValidationError):
        TelematicCreateRequest(telematic_serial=serial)
    with pytest.raises(ValidationError):
        TelematicUpdateRequest(telematic_serial=serial)


def test_device_serial_with_surrounding_spaces_is_stripped_or_refused() -> None:
    """A serial is stored exactly as telemetry will look it up (stripped).

    The telemetry message strips its serial; a device created as " TBOX "
    would never match its own messages.
    """
    try:
        create_request = TelematicCreateRequest(telematic_serial=" TBOX-001 ")
    except ValidationError:
        return
    assert create_request.telematic_serial == "TBOX-001"


def test_device_serial_with_plain_characters_is_accepted_guard() -> None:
    """Guard: an ordinary serial of letters, digits and dashes is accepted."""
    create_request = TelematicCreateRequest(telematic_serial="TBOX-VN-000123")

    assert create_request.telematic_serial == "TBOX-VN-000123"
