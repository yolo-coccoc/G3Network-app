"""Minimal MQTT consumer for telemetry ingestion MVP.

Feature code: F-A1 (Real-time vehicle telemetry ingestion)

The consumer does only three things: configure the MQTT client, receive
telemetry payloads, and put valid messages onto the in-RAM queue. Invalid
payloads or a full queue are logged and dropped; there is no retry, metrics,
or DLQ within the MVP scope.
"""

import asyncio
import logging
from typing import cast

from aiomqtt import Client as MQTTClient
from aiomqtt import Message, MqttError, Will
from pydantic import ValidationError

from app.domains.telemetry.schemas import TelemetryEnvelope, TelemetryMessage
from app.libs.common.config import settings
from app.libs.common.payload_guard import contains_nul, loads_strict

logger = logging.getLogger(__name__)


def parse_serial_from_topic(topic: str, topic_pattern: str) -> str | None:
    """Read the device serial out of a telemetry topic.

    The serial is the topic level that sits where the subscribed pattern has
    its ``+`` wildcard, so the topic is the device's identity: the broker's
    ACL lets each device publish to its own topic only (RV-OP4).

    Args:
        topic: Topic of the received message.
        topic_pattern: Subscribed pattern, e.g.
            ``g3network/telematics/+/telemetry``.

    Returns:
        The serial, or ``None`` when the topic does not have the pattern's
        shape or the pattern has no single ``+`` level.
    """
    pattern_levels = topic_pattern.split("/")
    topic_levels = topic.split("/")
    if pattern_levels.count("+") != 1 or len(pattern_levels) != len(topic_levels):
        return None
    serial = topic_levels[pattern_levels.index("+")]
    return serial or None


class MQTTConsumer:
    """Consumer that reads telemetry from the MQTT broker and pushes it onto the in-RAM queue.

    Attributes:
        host: MQTT broker host.
        port: MQTT broker port.
        client_id: MQTT client identifier.
        username: MQTT username, can be ``None``.
        password: MQTT password, can be ``None``.
        qos: QoS used when subscribing to the telemetry topic.
        topic_pattern: Topic pattern used to receive telemetry.
        queue: Destination queue for valid telemetry envelopes.
        _client: The configured MQTT client, or ``None`` if not yet connected.
        _running: Flag indicating whether the consume loop should continue.
    """

    def __init__(
        self,
        queue: asyncio.Queue[TelemetryEnvelope],
        host: str | None = None,
        port: int | None = None,
        client_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
        qos: int | None = None,
        topic_pattern: str | None = None,
    ) -> None:
        """Initialize the consumer from settings or override values.

        Args:
            queue: Destination queue for valid messages. Required: the
                entrypoint creates one queue and injects the same instance
                into the consumer and the worker.
            host: MQTT broker host.
            port: MQTT broker port.
            client_id: MQTT client identifier.
            username: MQTT username.
            password: MQTT password.
            qos: QoS used when subscribing.
            topic_pattern: Topic pattern for receiving telemetry.
        """
        self.queue = queue
        self.host = settings.MQTT_HOST if host is None else host
        self.port = settings.MQTT_PORT if port is None else port
        self.client_id = settings.MQTT_CLIENT_ID if client_id is None else client_id
        self.username = settings.MQTT_USERNAME if username is None else username
        self.password = settings.MQTT_PASSWORD if password is None else password
        self.qos = settings.MQTT_QOS if qos is None else qos
        self.topic_pattern = (
            settings.MQTT_TELEMETRY_TOPIC if topic_pattern is None else topic_pattern
        )

        self._client: MQTTClient | None = None
        self._running = False

    async def connect(self) -> None:
        """Prepare the MQTT client for the consume loop.

        Side Effects:
            Creates an MQTT client local to the process and sets the flag
            that lets the consume loop run when ``start_consuming()`` is
            called.
        """
        logger.info(
            "Connecting to MQTT broker",
            extra={
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
            },
        )

        will = Will(
            topic=settings.MQTT_STATUS_TOPIC_TEMPLATE.format(client_id=self.client_id),
            payload=b'{"status":"offline"}',
            qos=settings.MQTT_WILL_QOS,
            retain=settings.MQTT_WILL_RETAIN,
        )
        self._client = MQTTClient(
            hostname=self.host,
            port=self.port,
            identifier=self.client_id,
            username=self.username,
            password=self.password,
            will=will,
        )
        self._running = True

    async def disconnect(self) -> None:
        """Ask the consume loop to stop at the next check.

        Side Effects:
            Clears the flag for accepting new messages. The MQTT context
            closes itself when the loop exits.
        """
        self._running = False

    async def start_consuming(self) -> None:
        """Open the MQTT connection, subscribe to the topic, and process messages continuously.

        Raises:
            RuntimeError: When ``connect()`` has not been called beforehand.
            MqttError: When the MQTT connection or consume loop fails.
        """
        if self._client is None:
            raise RuntimeError("MQTT client is not configured")

        logger.info(
            "Starting MQTT consumer",
            extra={"topic": self.topic_pattern, "qos": self.qos},
        )

        try:
            async with self._client as client:
                await client.subscribe(self.topic_pattern, qos=self.qos)
                async for message in client.messages:
                    if not self._running:
                        break
                    await self._handle_message(message)
        except MqttError:
            logger.exception("MQTT consumption failed")
            raise
        finally:
            self._running = False
            self._client = None

    async def _handle_message(self, message: Message) -> None:
        """Parse, validate, and place one valid MQTT message onto the queue.

        Args:
            message: Message received from aiomqtt.
        """
        try:
            payload_dict = loads_strict(message.payload.decode("utf-8"))
            if contains_nul(payload_dict):
                logger.warning(
                    "Payload holds a NUL character, message dropped",
                    extra={"topic": str(message.topic)},
                )
                return
            telemetry_message = TelemetryMessage.model_validate(payload_dict)
            topic_serial = parse_serial_from_topic(
                str(message.topic), self.topic_pattern
            )
            if topic_serial != telemetry_message.telematic_serial:
                # A device may only report for itself: trusting the payload
                # serial would let one device write another truck's data.
                logger.warning(
                    "Payload serial differs from its topic, message dropped",
                    extra={"topic": str(message.topic)},
                )
                return
            self.queue.put_nowait(
                TelemetryEnvelope(
                    message=telemetry_message,
                    raw_payload=cast(dict[str, object], payload_dict),
                )
            )
        except asyncio.QueueFull:
            logger.warning("Queue full, message dropped")
        except UnicodeDecodeError as error:
            # Must be caught here: an exception escaping this method ends the
            # `async for` loop in `start_consuming()`, which would stop all ingestion
            # because of a single malformed message.
            logger.warning(
                "Payload is not valid UTF-8, message dropped",
                extra={"error": str(error), "topic": str(message.topic)},
            )
        except ValidationError as error:
            logger.warning(
                "Telemetry payload validation failed",
                extra={"error": str(error), "topic": str(message.topic)},
            )
        # ValidationError and UnicodeDecodeError are ValueErrors too, so this
        # clause comes after them. It catches invalid JSON, an integer over
        # Python's digit limit (ValueError) and a nesting deep enough to
        # exhaust the recursion limit: one such message must never stop
        # ingestion for every device (RV-OP1).
        except (ValueError, RecursionError) as error:
            logger.warning(
                "Payload could not be parsed, message dropped",
                extra={"error": str(error), "topic": str(message.topic)},
            )
