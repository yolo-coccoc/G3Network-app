"""Simulator OCPP 2.0.1 cho một phiên sạc local happy path.

Simulator này chỉ tạo traffic hợp lệ cho station, EVSE và connector đã
pre-provision. Nó không mô phỏng retry, duplicate, reconnect, delay hoặc lỗi
ngẫu nhiên; các nhánh đó thuộc reliability path chưa nằm trong MVP.
"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Subprotocol

OCPP_SUBPROTOCOL = "ocpp2.0.1"
DEFAULT_METER_VALUES_WH = (Decimal("1250"), Decimal("1500"))


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    """Cấu hình một lần chạy simulator phiên sạc.

    Attributes:
        url: Base WebSocket URL, ví dụ ``ws://localhost:9000``.
        identity: OCPP identity của station đã pre-provision.
        evse_id: OCPP EVSE ID hợp lệ của station.
        connector_id: OCPP connector ID hợp lệ của EVSE.
        transaction_id: Transaction identity do simulator cấp.
        meter_start_wh: Meter đầu phiên gửi trong ``Started``.
        meter_values_wh: Các meter sẽ gửi, mỗi giá trị thành một message
            ``MeterValues`` riêng.
        timeout_seconds: Timeout cho toàn bộ flow.
    """

    url: str
    identity: str
    evse_id: int = 1
    connector_id: int = 1
    transaction_id: str = "SIM-TRANSACTION-001"
    meter_start_wh: Decimal = Decimal("1000")
    meter_values_wh: tuple[Decimal, ...] = DEFAULT_METER_VALUES_WH
    timeout_seconds: float = 5.0


class OCPPChargingSessionSimulator:
    """Client OCPP nhỏ gửi một lifecycle session và chờ ACK tuần tự.

    Attributes:
        config: Identity, topology và transaction được dùng trong flow.
        _sequence_number: Sequence number của các TransactionEvent đã gửi.

    Connection chỉ tồn tại trong :meth:`run`; do đó simulator không giữ
    registry hoặc state để phục hồi sau reconnect.
    """

    def __init__(self, config: SimulatorConfig) -> None:
        """Khởi tạo simulator với cấu hình immutable của một session.

        Args:
            config: Cấu hình gateway và topology đã provision.
        """
        self.config = config
        self._sequence_number = 0

    @staticmethod
    def _timestamp() -> str:
        """Tạo timestamp OCPP UTC có timezone và độ chính xác mili-giây.

        Returns:
            Timestamp ISO-8601 kết thúc bằng ``Z``.
        """
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def _next_sequence_number(self) -> int:
        """Tăng và trả sequence number cho TransactionEvent kế tiếp.

        Returns:
            Sequence number dương theo thứ tự Started, Updated, Ended.
        """
        self._sequence_number += 1
        return self._sequence_number

    def _transaction_event_payload(
        self,
        *,
        event_type: str,
        trigger_reason: str,
        meter_wh: Decimal | None = None,
        stopped_reason: str | None = None,
    ) -> dict[str, Any]:
        """Tạo payload wire cho một TransactionEvent OCPP.

        Args:
            event_type: ``Started``, ``Updated`` hoặc ``Ended``.
            trigger_reason: Trigger reason hợp lệ theo OCPP 2.0.1.
            meter_wh: Meter tùy chọn, được gửi trong transaction event.
            stopped_reason: Lý do dừng, chỉ dùng cho ``Ended``.

        Returns:
            Dictionary dùng trực tiếp trong OCPP CALL frame.
        """
        transaction_info: dict[str, str] = {"transactionId": self.config.transaction_id}
        if stopped_reason is not None:
            transaction_info["stoppedReason"] = stopped_reason
        payload: dict[str, Any] = {
            "eventType": event_type,
            "timestamp": self._timestamp(),
            "triggerReason": trigger_reason,
            "seqNo": self._next_sequence_number(),
            "transactionInfo": transaction_info,
            "evse": {
                "id": self.config.evse_id,
                "connectorId": self.config.connector_id,
            },
        }
        if meter_wh is not None:
            payload["meterValue"] = [self._meter_value_payload(meter_wh)]
        return payload

    def _meter_value_payload(self, value_wh: Decimal) -> dict[str, Any]:
        """Tạo một nhóm meter value chỉ chứa đúng một sample Wh.

        Args:
            value_wh: Giá trị energy canonical Wh.

        Returns:
            Dictionary ``MeterValueType`` theo wire naming của OCPP.
        """
        # OCPP schema khai báo SampledValue.value là JSON number. Decimal vẫn
        # được giữ ở config để tránh làm tròn trước boundary; chỉ chuyển sang
        # số JSON tại bước serialize wire này.
        wire_value: int | float
        if value_wh == value_wh.to_integral_value():
            wire_value = int(value_wh)
        else:
            wire_value = float(value_wh)
        return {
            "timestamp": self._timestamp(),
            "sampledValue": [
                {
                    "value": wire_value,
                    "measurand": "Energy.Active.Import.Register",
                    "unitOfMeasure": {"unit": "Wh"},
                }
            ],
        }

    async def _send_call(
        self,
        websocket: ClientConnection,
        *,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        """Gửi một OCPP CALL và yêu cầu CALLRESULT đúng unique ID.

        Args:
            websocket: Connection OCPP đã negotiate subprotocol.
            action: Tên action OCPP.
            payload: Payload đã dùng wire naming.

        Side Effects:
            Gửi một frame và chờ response tương ứng theo giả định happy path;
            không retry hoặc tạo nhánh xử lý lỗi.
        """
        unique_id = uuid4().hex
        frame = json.dumps([2, unique_id, action, payload], separators=(",", ":"))
        await websocket.send(frame)
        await websocket.recv()
        print(f"ACK action={action} unique_id={unique_id}")

    async def run(self) -> None:
        """Chạy Started → MeterValues → Updated → Ended rồi đóng socket.

        Side Effects:
            Tạo một session trên gateway và chờ ACK thành công cho từng CALL.
            Context manager đóng connection ngay sau ACK của ``Ended``.
        """
        uri = (
            f"{self.config.url.rstrip('/')}/ocpp/{quote(self.config.identity, safe='')}"
        )
        async with asyncio.timeout(self.config.timeout_seconds):
            async with connect(
                uri,
                subprotocols=[Subprotocol(OCPP_SUBPROTOCOL)],
                open_timeout=self.config.timeout_seconds,
                close_timeout=self.config.timeout_seconds,
            ) as websocket:
                print(
                    f"CONNECTED identity={self.config.identity} "
                    f"subprotocol={websocket.subprotocol}"
                )
                await self._send_call(
                    websocket,
                    action="TransactionEvent",
                    payload=self._transaction_event_payload(
                        event_type="Started",
                        trigger_reason="CablePluggedIn",
                        meter_wh=self.config.meter_start_wh,
                    ),
                )
                for meter_value in self.config.meter_values_wh:
                    await self._send_call(
                        websocket,
                        action="MeterValues",
                        payload={
                            "evseId": self.config.evse_id,
                            "meterValue": [self._meter_value_payload(meter_value)],
                        },
                    )
                await self._send_call(
                    websocket,
                    action="TransactionEvent",
                    payload=self._transaction_event_payload(
                        event_type="Updated",
                        trigger_reason="MeterValuePeriodic",
                    ),
                )
                final_meter = self.config.meter_values_wh[-1]
                await self._send_call(
                    websocket,
                    action="TransactionEvent",
                    payload=self._transaction_event_payload(
                        event_type="Ended",
                        trigger_reason="EVDeparted",
                        meter_wh=final_meter,
                        stopped_reason="EVDisconnected",
                    ),
                )
                print(
                    f"COMPLETED identity={self.config.identity} "
                    f"transaction_id={self.config.transaction_id}"
                )


def _decimal_argument(value: str) -> Decimal:
    """Parse một CLI meter thành Decimal hữu hạn không âm.

    Args:
        value: Chuỗi Decimal từ argparse.

    Returns:
        Giá trị meter Wh.

    Raises:
        argparse.ArgumentTypeError: Nếu chuỗi không phải Decimal hợp lệ.
    """
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(
            f"Meter không phải Decimal: {value}"
        ) from error
    if not parsed.is_finite() or parsed < 0:
        raise argparse.ArgumentTypeError("Meter phải là Decimal hữu hạn không âm")
    return parsed


def _parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse các tham số CLI của simulator.

    Args:
        arguments: Arguments tùy chọn; ``None`` dùng ``sys.argv``.

    Returns:
        Namespace đã được argparse validate.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:9000")
    parser.add_argument("--identity", default="SIM-OCPP-001")
    parser.add_argument("--evse-id", type=int, default=1)
    parser.add_argument("--connector-id", type=int, default=1)
    parser.add_argument("--transaction-id", default="SIM-TRANSACTION-001")
    parser.add_argument(
        "--meter-start-wh", type=_decimal_argument, default=Decimal("1000")
    )
    parser.add_argument(
        "--meter-value",
        dest="meter_values_wh",
        type=_decimal_argument,
        action="append",
        help="Có thể lặp lại; mỗi giá trị tạo một message MeterValues.",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser.parse_args(arguments)


async def main(arguments: Sequence[str] | None = None) -> None:
    """Chạy duy nhất một phiên sạc OCPP happy path."""
    args = _parse_args(arguments)
    config = SimulatorConfig(
        url=args.url,
        identity=args.identity,
        evse_id=args.evse_id,
        connector_id=args.connector_id,
        transaction_id=args.transaction_id,
        meter_start_wh=args.meter_start_wh,
        meter_values_wh=tuple(args.meter_values_wh or DEFAULT_METER_VALUES_WH),
        timeout_seconds=args.timeout,
    )
    await OCPPChargingSessionSimulator(config).run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Đã dừng OCPP charging session simulator")
