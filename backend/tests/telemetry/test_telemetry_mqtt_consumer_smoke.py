"""Smoke tests for the MQTT consumer's handling of malformed payloads (F-A1)."""

import asyncio
import json
from types import SimpleNamespace
from typing import cast

import pytest
from aiomqtt import Message

from app.domains.telemetry.ingestion.mqtt_consumer import MQTTConsumer
from app.domains.telemetry.schemas import TelemetryEnvelope


def _message(payload: bytes) -> Message:
    """Build a stand-in for an aiomqtt message with the given raw payload.

    Args:
        payload: The raw bytes the broker delivered.

    Returns:
        An object exposing the `payload` and `topic` attributes the consumer reads.
    """
    return cast(
        Message,
        SimpleNamespace(payload=payload, topic="g3network/telematics/X/telemetry"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [b"\xff\xfe\x00 not utf-8", b"{not json", json.dumps({"a": 1}).encode()],
    ids=["invalid-utf8", "invalid-json", "schema-mismatch"],
)
async def test_consumer_drops_malformed_payload_without_raising(
    payload: bytes,
) -> None:
    """A malformed payload is logged and dropped; it never escapes the handler.

    Regression: a non-UTF-8 payload raised UnicodeDecodeError out of
    `_handle_message`, ending the consumer loop and stopping all ingestion.
    """
    queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue()
    consumer = MQTTConsumer(queue=queue)

    await consumer._handle_message(_message(payload))

    assert queue.empty()
