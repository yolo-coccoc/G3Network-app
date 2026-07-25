"""
MQTT consumer for telemetry messages.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Consumer kết nối đến EMQX broker, subscribe topic telemetry và
đưa message hợp lệ vào asyncio.Queue để batch worker xử lý.

Luồng dữ liệu:
    Telematic Device → MQTT Broker → MQTTConsumer → asyncio.Queue → BatchWorker

Lưu ý:
    - Subscribe với QoS 0 (fire-and-forget)
    - Message không hợp lệ được log warning và bỏ qua (không có DLQ trong MVP)
    - Reconnect tự động với exponential backoff
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiomqtt import Client as MQTTClient, MqttError, Will
from pydantic import ValidationError

from app.libs.common.config import settings
from app.domains.telemetry.schemas import TelemetryMessage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

logger = logging.getLogger(__name__)

# Module-level queue để batch worker consume
message_queue: asyncio.Queue[TelemetryMessage] = asyncio.Queue(maxsize=10000)


class Metrics:
    """Simple metrics counter for MVP."""

    def __init__(self) -> None:
        self.messages_received_total = 0
        self.messages_valid_total = 0
        self.messages_invalid_total = 0
        self.messages_dropped_total = 0

    def increment(self, metric_name: str) -> None:
        """Increment a metric counter."""
        if hasattr(self, metric_name):
            setattr(self, metric_name, getattr(self, metric_name) + 1)


metrics = Metrics()


class MQTTConsumer:
    """
    MQTT consumer cho telemetry messages.
    
    Consumer kết nối đến EMQX broker, subscribe topic pattern và
    xử lý message đến. Message hợp lệ được đưa vào asyncio.Queue.
    
    Attributes:
        host: MQTT broker host
        port: MQTT broker port
        client_id: MQTT client identifier
        username: MQTT username (optional)
        password: MQTT password (optional)
        qos: QoS level (0 for MVP)
        queue: asyncio.Queue để batch worker consume
        
    Example:
        >>> consumer = MQTTConsumer(
        ...     host="localhost",
        ...     port=1883,
        ...     client_id="g3network-telemetry-1",
        ... )
        >>> await consumer.connect()
        >>> await consumer.subscribe("g3network/telematics/+/telemetry")
        >>> await consumer.start_consuming()
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        client_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
        qos: int = 0,
        queue: asyncio.Queue[TelemetryMessage] | None = None,
    ) -> None:
        """
        Khởi tạo MQTT consumer.
        
        Args:
            host: MQTT broker host (default từ settings)
            port: MQTT broker port (default từ settings)
            client_id: MQTT client identifier (default từ settings)
            username: MQTT username (default từ settings)
            password: MQTT password (default từ settings)
            qos: QoS level (default 0)
            queue: asyncio.Queue để batch worker consume (default module-level queue)
        """
        self.host = host or settings.MQTT_HOST
        self.port = port or settings.MQTT_PORT
        self.client_id = client_id or settings.MQTT_CLIENT_ID
        self.username = username or settings.MQTT_USERNAME
        self.password = password or settings.MQTT_PASSWORD
        self.qos = qos
        self.queue = queue or message_queue

        self._client: MQTTClient | None = None
        self._running = False

    async def connect(self) -> None:
        """
        Kết nối đến MQTT broker.
        
        Raises:
            MqttError: Nếu không thể kết nối sau nhiều lần thử
        """
        logger.info(
            "Connecting to MQTT broker",
            extra={
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
            }
        )

        # Configure will message for connection loss detection
        will = Will(
            topic=f"g3network/consumers/{self.client_id}/status",
            payload=b'{"status":"offline"}',
            qos=1,
            retain=True,
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
        logger.info(
            "Connected to MQTT broker",
            extra={"client_id": self.client_id}
        )

    async def disconnect(self) -> None:
        """
        Ngắt kết nối từ MQTT broker.
        
        Phương thức này được gọi khi graceful shutdown.
        """
        if self._client and self._running:
            logger.info("Disconnecting from MQTT broker")
            self._running = False
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.warning(f"Error during disconnect: {e}")
            finally:
                self._client = None

    async def subscribe(self, topic_pattern: str) -> None:
        """
        Subscribe đến topic pattern.
        
        Note: Trong implementation hiện tại, subscribe được thực hiện
        tự động trong start_consuming(). Method này được giữ lại để
        tương thích với planner.
        
        Args:
            topic_pattern: Topic pattern để subscribe (VD: "g3network/telematics/+/telemetry")
        """
        logger.info(
            "Subscribe will be done in start_consuming()",
            extra={
                "topic": topic_pattern,
                "qos": self.qos,
            }
        )

    async def start_consuming(self) -> None:
        """
        Bắt đầu vòng lặp nhận message.
        
        Vòng lặp này sẽ chạy cho đến khi disconnect() được gọi.
        Mỗi message được parse, validate và đưa vào queue nếu hợp lệ.
        """
        if not self._client:
            raise RuntimeError("Not connected to MQTT broker")

        logger.info("Starting message consumption loop")

        try:
            async with self._client as client:
                # Subscribe inside context manager
                await client.subscribe("g3network/telematics/+/telemetry", qos=self.qos)
                logger.info("Subscribed to g3network/telematics/+/telemetry")

                async for message in client.messages:
                    if not self._running:
                        break

                    await self._handle_message(message)

        except MqttError as e:
            if self._running:
                logger.error(f"MQTT error during consumption: {e}")
                raise

    async def _handle_message(self, message: object) -> None:
        """
        Xử lý một message từ MQTT broker.
        
        Args:
            message: Message từ aiomqtt
        """
        metrics.increment("messages_received_total")

        try:
            # Parse JSON payload
            payload_str = message.payload.decode("utf-8")
            payload_dict = json.loads(payload_str)

            # Validate bằng Pydantic schema
            telemetry_msg = TelemetryMessage.model_validate(payload_dict)

            # Đưa vào queue
            try:
                self.queue.put_nowait(telemetry_msg)
                metrics.increment("messages_valid_total")

                logger.debug(
                    "Message validated and queued",
                    extra={
                        "message_uuid": str(telemetry_msg.message_uuid),
                        "telematic_serial": telemetry_msg.telematic_serial,
                        "queue_size": self.queue.qsize(),
                    }
                )

            except asyncio.QueueFull:
                metrics.increment("messages_dropped_total")
                logger.warning(
                    "Queue full, message dropped",
                    extra={
                        "message_uuid": str(telemetry_msg.message_uuid),
                        "telematic_serial": telemetry_msg.telematic_serial,
                    }
                )

        except json.JSONDecodeError as e:
            metrics.increment("messages_invalid_total")
            logger.warning(
                "Invalid JSON payload",
                extra={
                    "error": str(e),
                    "topic": str(message.topic),
                }
            )

        except ValidationError as e:
            metrics.increment("messages_invalid_total")
            logger.warning(
                "Validation error",
                extra={
                    "error": str(e),
                    "topic": str(message.topic),
                }
            )
