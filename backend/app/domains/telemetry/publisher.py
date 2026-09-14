"""MQTT publisher tối giản cho command cấu hình telemetry.

Publisher mở một kết nối cho từng command, publish xong rồi đóng kết nối. Cách
này giữ lifecycle external client trong một component và phù hợp với tần suất
thấp của API cấu hình MVP; luồng ingest telemetry vẫn sở hữu consumer riêng.
"""

import json
from collections.abc import Mapping

from aiomqtt import Client as MQTTClient

from app.libs.common.config import settings


class MQTTPublisher:
    """Gửi JSON command tới một topic MQTT.

    Attributes:
        host: Host MQTT broker.
        port: Cổng MQTT broker.
        client_id: Client ID riêng cho publisher API.
        qos: QoS khi publish.
        retain: Có giữ command mới nhất trên broker.
    """

    def __init__(self) -> None:
        """Khởi tạo publisher từ settings hiện tại."""
        self.host = settings.MQTT_HOST
        self.port = settings.MQTT_PORT
        self.client_id = settings.MQTT_COMMAND_CLIENT_ID
        self.qos = settings.MQTT_COMMAND_QOS
        self.retain = settings.MQTT_COMMAND_RETAIN

    async def publish_json(
        self,
        topic: str,
        payload: Mapping[str, object],
    ) -> None:
        """Publish một payload JSON và chờ broker nhận command.

        Args:
            topic: Topic command đích.
            payload: Object JSON cần gửi.

        Side Effects:
            Mở kết nối MQTT, publish một message rồi đóng kết nối.

        Raises:
            Exception: Lỗi kết nối hoặc publish được chuyển lên service gọi.
        """
        async with MQTTClient(
            hostname=self.host,
            port=self.port,
            identifier=self.client_id,
            username=settings.MQTT_USERNAME,
            password=settings.MQTT_PASSWORD,
        ) as client:
            await client.publish(
                topic,
                json.dumps(payload, ensure_ascii=False),
                qos=self.qos,
                retain=self.retain,
            )
