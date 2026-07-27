"""
MQTT consumer nhận các message telemetry.

Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực)

Consumer kết nối đến EMQX broker, subscribe topic telemetry và
đưa message hợp lệ vào asyncio.Queue để batch worker xử lý.

Luồng dữ liệu:
    Telematic Device → MQTT Broker → MQTTConsumer → asyncio.Queue → BatchWorker

Lưu ý:
    - Subscribe với QoS 0 (fire-and-forget)
    - Message không hợp lệ được log warning và bỏ qua (không có DLQ trong MVP)
    - Lỗi kết nối làm consumer dừng trong phạm vi MVP
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

# Queue mặc định hỗ trợ cách khởi tạo độc lập hiện có. Runtime entrypoint sẽ tạo
# một queue theo settings rồi inject cùng instance vào consumer và worker.
message_queue: asyncio.Queue[TelemetryEnvelope] = asyncio.Queue(maxsize=10000)


class Metrics:
    """
    Lưu các counter MQTT cục bộ trong bộ nhớ cho MVP.

    Attributes:
        messages_received_total: Tổng message callback đã nhận.
        messages_valid_total: Tổng message hợp lệ đã đưa vào queue.
        messages_invalid_total: Tổng message lỗi JSON hoặc Pydantic validation.
        messages_dropped_total: Tổng message bị drop do queue đầy.
    """

    def __init__(self) -> None:
        """Khởi tạo toàn bộ counter trong bộ nhớ bằng không."""
        self.messages_received_total = 0
        self.messages_valid_total = 0
        self.messages_invalid_total = 0
        self.messages_dropped_total = 0

    def increment(self, metric_name: str) -> None:
        """
        Tăng một counter đã khai báo lên một đơn vị.

        Args:
            metric_name: Tên attribute counter cần tăng.

        Side Effects:
            Thay đổi counter tương ứng nếu attribute tồn tại; tên không hợp lệ
            được bỏ qua để metrics không làm gián đoạn ingestion MVP.
        """
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
        _client: MQTT client đã cấu hình, hoặc ``None`` ngoài lifecycle.
        _running: Cờ cho phép vòng lặp consumer tiếp tục nhận message.
        _consuming: Trạng thái đã kết nối, subscribe và đang ở consume loop.

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
        qos: int | None = None,
        topic_pattern: str | None = None,
        queue: asyncio.Queue[TelemetryEnvelope] | None = None,
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
            topic_pattern: Topic MQTT nhận telemetry (default từ settings)
            queue: asyncio.Queue để batch worker consume (default module-level queue)
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
        self._consuming = False

    @property
    def is_consuming(self) -> bool:
        """
        Cho biết consumer có đang nhận message sau khi subscribe hay không.

        Returns:
            ``True`` chỉ khi MQTT context đã mở, subscribe thành công và consume
            loop chưa nhận yêu cầu dừng.
        """
        return self._consuming and self._running

    async def connect(self) -> None:
        """
        Cấu hình MQTT client trước khi mở async network context.

        Method này chưa tạo kết nối network thật. Kết nối và subscribe diễn ra
        trong ``start_consuming()`` để một component duy nhất sở hữu lifecycle
        của aiomqtt context manager.

        Side Effects:
            Tạo MQTT client và bật cờ cho phép consume loop chạy.
        """
        logger.info(
            "Connecting to MQTT broker",
            extra={
                "host": self.host,
                "port": self.port,
                "client_id": self.client_id,
            },
        )

        # Last Will giúp broker công bố trạng thái offline nếu connection mất đột
        # ngột; đây là status message, không thay đổi QoS 0 của telemetry topic.
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
        logger.info("MQTT client configured", extra={"client_id": self.client_id})

    async def disconnect(self) -> None:
        """
        Ngắt kết nối từ MQTT broker.

        Phương thức này được gọi khi graceful shutdown.

        Side Effects:
            Tắt cờ nhận message mới. Async MQTT context trong
            ``start_consuming()`` sẽ tự đóng connection khi vòng lặp thoát.
        """
        if self._client is not None and self._running:
            logger.info("Disconnecting from MQTT broker")
            self._running = False

    async def subscribe(self, topic_pattern: str) -> None:
        """
        Subscribe đến topic pattern.

        Args:
            topic_pattern: Topic pattern để subscribe (VD: "g3network/telematics/+/telemetry")
        """
        self.topic_pattern = topic_pattern
        logger.info(
            "MQTT subscription configured",
            extra={
                "topic": topic_pattern,
                "qos": self.qos,
            },
        )

    async def start_consuming(self) -> None:
        """
        Bắt đầu vòng lặp nhận message.

        Vòng lặp này sẽ chạy cho đến khi disconnect() được gọi.
        Mỗi message được parse, validate và đưa vào queue nếu hợp lệ.

        Raises:
            RuntimeError: Khi client chưa được cấu hình bằng ``connect()``.
            MqttError: Khi kết nối, subscribe hoặc consume MQTT thất bại.

        Side Effects:
            Mở/đóng MQTT connection, cập nhật trạng thái ``is_consuming`` và đưa
            các message hợp lệ vào queue.
        """
        if not self._client:
            raise RuntimeError("Not connected to MQTT broker")

        logger.info("Starting message consumption loop")

        try:
            async with self._client as client:
                # Subscribe bên trong context để chỉ công bố trạng thái consuming
                # sau khi broker thực sự chấp nhận subscription.
                await client.subscribe(self.topic_pattern, qos=self.qos)
                self._consuming = True
                logger.info(
                    "Subscribed to MQTT telemetry",
                    extra={"topic": self.topic_pattern, "qos": self.qos},
                )

                messages = client.messages
                while self._running:
                    try:
                        message = await asyncio.wait_for(anext(messages), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    await self._handle_message(message)

        except MqttError:
            if self._running:
                logger.exception("MQTT consumption failed")
                raise
        finally:
            self._consuming = False
            self._running = False
            self._client = None

    async def _handle_message(self, message: Message) -> None:
        """
        Xử lý một message từ MQTT broker.

        Args:
            message: Message từ aiomqtt
        """
        metrics.increment("messages_received_total")

        try:
            # Parse trước khi validate để giữ lại đúng object gốc trong envelope.
            payload_str = message.payload.decode("utf-8")
            payload_dict = json.loads(payload_str)

            # Validation tại MQTT boundary ngăn payload sai vào queue nghiệp vụ.
            telemetry_msg = TelemetryMessage.model_validate(payload_dict)

            # put_nowait bảo vệ callback khỏi block khi producer vượt consumer.
            try:
                self.queue.put_nowait(
                    TelemetryEnvelope(
                        message=telemetry_msg,
                        raw_payload=payload_dict,
                    )
                )
                metrics.increment("messages_valid_total")

                logger.debug(
                    "Message validated and queued",
                    extra={
                        "message_uuid": str(telemetry_msg.message_uuid),
                        "telematic_serial": telemetry_msg.telematic_serial,
                        "queue_size": self.queue.qsize(),
                    },
                )

            except asyncio.QueueFull:
                metrics.increment("messages_dropped_total")
                logger.warning(
                    "Queue full, message dropped",
                    extra={
                        "message_uuid": str(telemetry_msg.message_uuid),
                        "telematic_serial": telemetry_msg.telematic_serial,
                    },
                )

        except json.JSONDecodeError as e:
            metrics.increment("messages_invalid_total")
            logger.warning(
                "Invalid JSON payload",
                extra={
                    "error": str(e),
                    "topic": str(message.topic),
                },
            )

        except ValidationError as e:
            metrics.increment("messages_invalid_total")
            logger.warning(
                "Validation error",
                extra={
                    "error": str(e),
                    "topic": str(message.topic),
                },
            )
