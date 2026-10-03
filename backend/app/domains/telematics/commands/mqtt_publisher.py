"""Publish backend-to-device configuration commands over MQTT (F-J2).

This is this backend's first-ever MQTT *publish* path - every other MQTT
usage in the repo only subscribes (``telemetry/ingestion/mqtt_consumer.py``).
There is no device subscribed to the command topic today and no ack
topic defined in ``docs/design/specifications/mqtt-spec.md`` 2.3, so a successful
publish only means the broker accepted the message (QoS 1 PUBACK), never
that a device received or applied it.
"""

import asyncio
import json
import logging
from datetime import datetime
from uuid import uuid4

from aiomqtt import Client as MQTTClient

from app.libs.common.config import settings

logger = logging.getLogger(__name__)


def build_command_topic(telematic_serial: str) -> str:
    """Build the backend->device command topic for one device.

    Args:
        telematic_serial: Physical serial of the target device.

    Returns:
        The MQTT topic string, per mqtt-spec.md 2.3's
        ``g3network/telematics/{telematic_serial}/command`` template.
    """
    return settings.MQTT_COMMAND_TOPIC_TEMPLATE.format(
        telematic_serial=telematic_serial
    )


def build_set_telemetry_interval_payload(
    telemetry_interval_seconds: int, *, issued_at: datetime
) -> dict[str, object]:
    """Build the JSON payload for a set_telemetry_interval command.

    Args:
        telemetry_interval_seconds: Desired telemetry publish interval,
            in seconds.
        issued_at: Timestamp to stamp the command with. Injected rather
            than read internally so the payload is assertion-testable and
            so the caller can reuse the exact same instant for the DB
            record it writes alongside the publish.

    Returns:
        The command payload, matching mqtt-spec.md 2.3's
        ``{"command", "timestamp"}`` shape plus this command's own field.
    """
    return {
        "command": "set_telemetry_interval",
        "telemetry_interval_seconds": telemetry_interval_seconds,
        "timestamp": issued_at.isoformat(),
    }


async def publish_device_command(
    telematic_serial: str, command_payload: dict[str, object]
) -> None:
    """Publish one command to a device's command topic.

    Opens a short-lived MQTT client scoped to exactly this one publish -
    the entire connect/publish/disconnect lifecycle lives inside this
    function, so it is the sole owner of that lifecycle rather than a
    long-lived client shared across requests. The client id is suffixed
    with a random token so concurrent pushes never evict each other's
    broker session, and it deliberately differs from ``MQTT_CLIENT_ID``
    (the telemetry consumer's id) for the same reason - a broker evicts
    an existing session when a second connection claims the same id.

    Args:
        telematic_serial: Physical serial of the target device.
        command_payload: JSON-serializable command payload to publish.

    Raises:
        MqttError: If the broker connection or publish fails.
        TimeoutError: If the round trip exceeds
            ``settings.MQTT_COMMAND_TIMEOUT_SECONDS``.

    Side Effects:
        Opens a network connection to the MQTT broker and publishes one
        message at ``settings.MQTT_COMMAND_QOS``. Lets ``MqttError``/
        ``TimeoutError`` escape uncaught - the caller (the service layer)
        converts them into a domain exception, the same way the
        repository lets ``IntegrityError`` escape for the service to
        convert.
    """
    topic = build_command_topic(telematic_serial)
    client_id = f"{settings.MQTT_COMMAND_CLIENT_ID}-{uuid4().hex[:8]}"
    payload_bytes = json.dumps(command_payload).encode("utf-8")
    # The MQTTClient's own `timeout` bounds the CONNACK/PUBACK waits, but
    # aiomqtt's __aenter__ runs paho's blocking connect() in an executor
    # thread that `timeout` does not cover - wrap the whole call so a
    # black-holed host still can't hold the caller's HTTP request (and its
    # open DB transaction) open indefinitely.
    async with asyncio.timeout(settings.MQTT_COMMAND_TIMEOUT_SECONDS):
        async with MQTTClient(
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            identifier=client_id,
            username=settings.MQTT_USERNAME,
            password=settings.MQTT_PASSWORD,
            timeout=settings.MQTT_COMMAND_TIMEOUT_SECONDS,
        ) as client:
            await client.publish(
                topic,
                payload=payload_bytes,
                qos=settings.MQTT_COMMAND_QOS,
                retain=settings.MQTT_COMMAND_RETAIN,
            )
    logger.info(
        "Published device command",
        extra={"telematic_serial": telematic_serial, "topic": topic},
    )
