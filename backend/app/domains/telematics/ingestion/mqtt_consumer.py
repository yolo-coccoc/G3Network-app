"""MQTT consumer of the T-Box status reports (DEV-03, mqtt-spec.md 2.2).

Counterpart of the telemetry consumer for the topic
``g3network/telematics/{serial}/status``: it configures the MQTT client,
reads the serial from the topic, validates the tolerant message and puts it
on the in-RAM queue. An unreadable payload or a full queue is logged and
dropped; there is no retry or DLQ in the MVP (same policy as telemetry).
"""

import asyncio
import logging

from aiomqtt import Client as MQTTClient
from aiomqtt import Message, MqttError, Will
from pydantic import ValidationError

from app.domains.telematics.schemas import (
    TelematicStatusEnvelope,
    TelematicStatusMessage,
)
from app.libs.common.config import settings
from app.libs.common.payload_guard import contains_nul, loads_strict

logger = logging.getLogger(__name__)


def parse_serial_from_topic(topic: str) -> str | None:
    """Read the device serial out of a status topic.

    Args:
        topic: Topic of the message, ``g3network/telematics/{serial}/status``.

    Returns:
        The serial, or `None` when the topic does not have the expected
        shape (four levels, the third being a non-empty serial).
    """
    levels = topic.split("/")
    if len(levels) != 4 or not levels[2]:
        return None
    return levels[2]


class StatusReportConsumer:
    """Reads status messages from the broker and queues the valid ones.

    Attributes:
        queue: Destination queue for validated envelopes.
        host: MQTT broker host.
        port: MQTT broker port.
        client_id: MQTT client identifier (not the telemetry consumer's).
        username: MQTT username, can be ``None``.
        password: MQTT password, can be ``None``.
        qos: QoS used when subscribing.
        topic_pattern: Topic pattern of the status reports.
        _client: The configured MQTT client, or ``None`` before ``connect``.
        _running: Whether the consume loop should continue.
    """

    def __init__(
        self,
        queue: asyncio.Queue[TelematicStatusEnvelope],
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
            queue: Destination queue, shared with the worker.
            host: MQTT broker host.
            port: MQTT broker port.
            client_id: MQTT client identifier.
            username: MQTT username.
            password: MQTT password.
            qos: QoS used when subscribing.
            topic_pattern: Topic pattern for the status reports.
        """
        self.queue = queue
        self.host = settings.MQTT_HOST if host is None else host
        self.port = settings.MQTT_PORT if port is None else port
        self.client_id = (
            settings.MQTT_STATUS_CLIENT_ID if client_id is None else client_id
        )
        self.username = settings.MQTT_USERNAME if username is None else username
        self.password = settings.MQTT_PASSWORD if password is None else password
        self.qos = settings.MQTT_QOS if qos is None else qos
        self.topic_pattern = (
            settings.MQTT_STATUS_REPORT_TOPIC
            if topic_pattern is None
            else topic_pattern
        )
        self._client: MQTTClient | None = None
        self._running = False

    async def connect(self) -> None:
        """Prepare the MQTT client for the consume loop.

        Side Effects:
            Creates an MQTT client local to the process and lets the consume
            loop run when ``start_consuming()`` is called.
        """
        logger.info(
            "Connecting status-report consumer to MQTT broker",
            extra={"host": self.host, "port": self.port, "client_id": self.client_id},
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
        """Ask the consume loop to stop at the next check."""
        self._running = False

    async def start_consuming(self) -> None:
        """Open the connection, subscribe and process messages continuously.

        Raises:
            RuntimeError: When ``connect()`` has not been called beforehand.
            MqttError: When the MQTT connection or consume loop fails.
        """
        if self._client is None:
            raise RuntimeError("MQTT client is not configured")
        logger.info(
            "Starting status-report consumer",
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
            logger.exception("MQTT status-report consumption failed")
            raise
        finally:
            self._running = False
            self._client = None

    async def _handle_message(self, message: Message) -> None:
        """Parse one message and queue it when it is a usable status message.

        An exception escaping this method would end the ``async for`` loop and
        stop all ingestion because of one bad message, so every expected
        failure is caught and logged here.

        Args:
            message: Message received from aiomqtt.
        """
        topic = str(message.topic)
        telematic_serial = parse_serial_from_topic(topic)
        if telematic_serial is None:
            logger.warning("Status topic has no serial", extra={"topic": topic})
            return
        try:
            payload = loads_strict(message.payload.decode("utf-8"))
            if not isinstance(payload, dict) or contains_nul(payload):
                logger.warning(
                    "Status payload is not a JSON object or holds a NUL character",
                    extra={"topic": topic},
                )
                return
            self.queue.put_nowait(
                TelematicStatusEnvelope(
                    telematic_serial=telematic_serial,
                    message=TelematicStatusMessage.model_validate(payload),
                )
            )
        except asyncio.QueueFull:
            logger.warning("Status queue full, message dropped")
        except UnicodeDecodeError as error:
            logger.warning(
                "Status payload is not valid UTF-8, message dropped",
                extra={"error": str(error), "topic": topic},
            )
        except ValidationError as error:
            logger.warning(
                "Status payload validation failed",
                extra={"error": str(error), "topic": topic},
            )
        # ValidationError and UnicodeDecodeError are ValueErrors too, so this
        # clause comes after them. It catches invalid JSON, an integer over
        # Python's digit limit (ValueError) and a nesting deep enough to
        # exhaust the recursion limit: one such message must never stop
        # ingestion for every device (RV-OP1).
        except (ValueError, RecursionError) as error:
            logger.warning(
                "Status payload could not be parsed, message dropped",
                extra={"error": str(error), "topic": topic},
            )
