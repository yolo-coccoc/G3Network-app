"""Minimal MQTT consumer for telemetry ingestion MVP.

Feature code: F-A1 (Real-time vehicle telemetry ingestion)

The consumer does only three things: configure the MQTT client, receive
telemetry payloads, and put valid messages onto the in-RAM queue. Invalid
payloads or a full queue are logged and dropped; there is no retry, metrics,
or DLQ within the MVP scope.
"""

import asyncio
import json
import logging

from aiomqtt import Client as MQTTClient
from aiomqtt import Message, MqttError, Will
from pydantic import ValidationError

from app.domains.telemetry.schemas import TelemetryEnvelope, TelemetryMessage
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

# The default queue only supports standalone construction. The main
# entrypoint creates its own queue from settings and injects the same
# instance into the consumer and worker.
message_queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue(
    maxsize=settings.TELEMETRY_QUEUE_SIZE
)


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
        host: str | None = None,
        port: int | None = None,
        client_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
        qos: int | None = None,
        topic_pattern: str | None = None,
        queue: asyncio.Queue[TelemetryEnvelope] | None = None,
    ) -> None:
        """Initialize the consumer from settings or override values.

        Args:
            host: MQTT broker host.
            port: MQTT broker port.
            client_id: MQTT client identifier.
            username: MQTT username.
            password: MQTT password.
            qos: QoS used when subscribing.
            topic_pattern: Topic pattern for receiving telemetry.
            queue: Destination queue for valid messages.
        """
        self.host = settings.MQTT_HOST if host is None else host
        self.port = settings.MQTT_PORT if port is None else port
        self.client_id = settings.MQTT_CLIENT_ID if client_id is None else client_id
        self.username = settings.MQTT_USERNAME if username is None else username
        self.password = settings.MQTT_PASSWORD if password is None else password
        self.qos = settings.MQTT_QOS if qos is None else qos
        self.topic_pattern = (
            settings.MQTT_TELEMETRY_TOPIC if topic_pattern is None else topic_pattern
        )
        self.queue = message_queue if queue is None else queue

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
            payload_dict = json.loads(message.payload.decode("utf-8"))
            telemetry_message = TelemetryMessage.model_validate(payload_dict)
            self.queue.put_nowait(
                TelemetryEnvelope(
                    message=telemetry_message,
                    raw_payload=payload_dict,
                )
            )
        except asyncio.QueueFull:
            logger.warning("Queue full, message dropped")
        except UnicodeDecodeError as error:
            # Must be caught here: an exception escaping this method ends the
            # `async for` loop in `run()`, which would stop all ingestion
            # because of a single malformed message.
            logger.warning(
                "Payload is not valid UTF-8, message dropped",
                extra={"error": str(error), "topic": str(message.topic)},
            )
        except json.JSONDecodeError as error:
            logger.warning(
                "Invalid JSON payload",
                extra={"error": str(error), "topic": str(message.topic)},
            )
        except ValidationError as error:
            logger.warning(
                "Telemetry payload validation failed",
                extra={"error": str(error), "topic": str(message.topic)},
            )
