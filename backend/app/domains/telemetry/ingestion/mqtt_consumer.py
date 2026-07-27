"""MQTT consumer tối giản cho telemetry ingestion MVP.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Consumer chỉ làm ba việc: cấu hình MQTT client, nhận payload telemetry và đưa
message hợp lệ vào queue trong RAM. Payload lỗi hoặc queue đầy được log rồi bỏ
qua; không có retry, metrics hay DLQ trong phạm vi MVP.
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

# Queue mặc định chỉ hỗ trợ cách khởi tạo độc lập. Entrypoint chính sẽ tạo queue
# riêng theo settings rồi inject cùng instance vào consumer và worker.
message_queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue(maxsize=10000)


class MQTTConsumer:
    """Consumer đọc telemetry từ MQTT broker và đẩy vào queue trong RAM.

    Attributes:
        host: MQTT broker host.
        port: MQTT broker port.
        client_id: MQTT client identifier.
        username: MQTT username, có thể ``None``.
        password: MQTT password, có thể ``None``.
        qos: QoS dùng khi subscribe topic telemetry.
        topic_pattern: Topic pattern dùng để nhận telemetry.
        queue: Queue đích cho các telemetry envelope hợp lệ.
        _client: MQTT client đã được cấu hình, hoặc ``None`` nếu chưa connect.
        _running: Cờ cho biết vòng lặp consume có nên tiếp tục hay không.
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
        """Khởi tạo consumer từ settings hoặc giá trị override.

        Args:
            host: MQTT broker host.
            port: MQTT broker port.
            client_id: MQTT client identifier.
            username: MQTT username.
            password: MQTT password.
            qos: QoS dùng khi subscribe.
            topic_pattern: Topic pattern nhận telemetry.
            queue: Queue đích cho message hợp lệ.
        """
        self.host = host or settings.MQTT_HOST
        self.port = port or settings.MQTT_PORT
        self.client_id = client_id or settings.MQTT_CLIENT_ID
        self.username = username or settings.MQTT_USERNAME
        self.password = password or settings.MQTT_PASSWORD
        self.qos = settings.MQTT_QOS if qos is None else qos
        self.topic_pattern = topic_pattern or settings.MQTT_TELEMETRY_TOPIC
        self.queue = message_queue if queue is None else queue

        self._client: MQTTClient | None = None
        self._running = False

    async def connect(self) -> None:
        """Chuẩn bị MQTT client cho vòng lặp consume.

        Side Effects:
            Tạo MQTT client cục bộ cho process và bật cờ cho phép consume loop
            chạy khi ``start_consuming()`` được gọi.
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

    async def disconnect(self) -> None:
        """Yêu cầu consume loop dừng ở lần kiểm tra kế tiếp.

        Side Effects:
            Hạ cờ nhận message mới. MQTT context sẽ tự đóng khi vòng lặp thoát.
        """
        self._running = False

    async def start_consuming(self) -> None:
        """Mở MQTT connection, subscribe topic và xử lý message liên tục.

        Raises:
            RuntimeError: Khi chưa gọi ``connect()`` trước đó.
            MqttError: Khi MQTT connection hoặc consume loop thất bại.
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
        """Parse, validate và đưa một MQTT message hợp lệ vào queue.

        Args:
            message: Message nhận từ aiomqtt.
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
