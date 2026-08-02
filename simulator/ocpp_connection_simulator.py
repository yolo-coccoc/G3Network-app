"""Simulator OCPP tối thiểu để kiểm tra connect/reject của gateway.

Simulator này chỉ kiểm tra WebSocket handshake, không mô phỏng phiên sạc,
TransactionEvent, MeterValues hoặc command. Station identity phải được
pre-provision qua Charging Station API trước khi chạy case kết nối hợp lệ.
"""

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

OCPP_SUBPROTOCOL = "ocpp2.0.1"


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    """Cấu hình một lần chạy simulator connect/reject.

    Attributes:
        url: Base WebSocket URL, ví dụ ``ws://localhost:9000``.
        identity: OCPP identity cần test.
        timeout_seconds: Thời gian chờ handshake thành công.
    """

    url: str
    identity: str
    timeout_seconds: float = 5.0


async def connect_valid_station(config: SimulatorConfig) -> None:
    """Kết nối station với protocol OCPP 2.0.1 rồi đóng sạch.

    Args:
        config: Cấu hình gateway và identity đã pre-provision.

    Raises:
        RuntimeError: Khi gateway negotiate sai protocol.
        Exception: Khi handshake hoặc connection thất bại.
    """
    uri = f"{config.url.rstrip('/')}/ocpp/{quote(config.identity, safe='')}"
    async with asyncio.timeout(config.timeout_seconds):
        async with connect(uri, subprotocols=[OCPP_SUBPROTOCOL]) as websocket:
            if websocket.subprotocol != OCPP_SUBPROTOCOL:
                raise RuntimeError(
                    f"Gateway negotiated unexpected subprotocol: {websocket.subprotocol}"
                )
            print(
                f"ACCEPT identity={config.identity} subprotocol={websocket.subprotocol}"
            )


async def reject_unknown_station(config: SimulatorConfig) -> None:
    """Xác nhận identity chưa pre-provision bị gateway từ chối.

    Args:
        config: Cấu hình gateway và identity không tồn tại.

    Raises:
        AssertionError: Nếu gateway lại chấp nhận identity lạ.
    """
    uri = f"{config.url.rstrip('/')}/ocpp/{quote(config.identity, safe='')}"
    try:
        async with asyncio.timeout(config.timeout_seconds):
            async with connect(uri, subprotocols=[OCPP_SUBPROTOCOL]):
                raise AssertionError(
                    f"Gateway accepted unknown station identity: {config.identity}"
                )
    except InvalidStatus as error:
        print(
            f"REJECT identity={config.identity} " f"status={error.response.status_code}"
        )


async def reject_wrong_protocol(config: SimulatorConfig) -> None:
    """Xác nhận client không gửi OCPP 2.0.1 bị từ chối ở handshake.

    Args:
        config: Cấu hình gateway và identity hợp lệ.

    Raises:
        AssertionError: Nếu gateway chấp nhận protocol không được phép.
    """
    uri = f"{config.url.rstrip('/')}/ocpp/{quote(config.identity, safe='')}"
    try:
        async with asyncio.timeout(config.timeout_seconds):
            async with connect(uri, subprotocols=["ocpp1.6"]):
                raise AssertionError("Gateway accepted unsupported OCPP protocol")
    except InvalidStatus as error:
        print(f"REJECT protocol=ocpp1.6 status={error.response.status_code}")


def _parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments cho simulator.

    Args:
        arguments: Arguments tùy chọn; ``None`` dùng ``sys.argv``.

    Returns:
        Namespace đã được argparse validate.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:9000")
    parser.add_argument("--identity", default="SIM-OCPP-001")
    parser.add_argument("--unknown-identity", default="SIM-OCPP-UNKNOWN")
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser.parse_args(arguments)


async def main(arguments: Sequence[str] | None = None) -> None:
    """Chạy lần lượt ba case connect hợp lệ và reject tối thiểu."""
    args = _parse_args(arguments)
    valid_config = SimulatorConfig(args.url, args.identity, args.timeout)
    unknown_config = SimulatorConfig(args.url, args.unknown_identity, args.timeout)
    await connect_valid_station(valid_config)
    await reject_unknown_station(unknown_config)
    await reject_wrong_protocol(valid_config)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Đã dừng OCPP simulator")
