"""WebSocket gateway tối thiểu cho OCPP 2.0.1.

Gateway chỉ validate path, subprotocol và identity station đã pre-provision,
sau đó giữ một connection ổn định trong process. Reliability production,
reconnect và technical status history nằm ngoài active path.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Final
from urllib.parse import unquote, urlsplit

from ocpp.v201 import ChargePoint  # type: ignore[import-untyped]
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.typing import Subprotocol

from app.domains.charging_stations import repository
from app.libs.common.config import settings
from app.libs.db.session import async_session_factory

logger = logging.getLogger(__name__)

OCPP_SUBPROTOCOL: Final[str] = "ocpp2.0.1"
OCPP_PATH_PREFIX: Final[str] = "/ocpp/"
_MAX_IDENTITY_LENGTH: Final[int] = 255


def parse_ocpp_identity(request_path: str) -> str | None:
    """Trích xuất OCPP identity từ URL path hợp lệ.

    Args:
        request_path: Request target có thể chứa query string.

    Returns:
        Identity đã URL-decode hoặc ``None`` nếu path không đúng contract.
    """
    path = urlsplit(request_path).path
    if not path.startswith(OCPP_PATH_PREFIX):
        return None
    encoded_identity = path[len(OCPP_PATH_PREFIX) :]
    if not encoded_identity or "/" in encoded_identity:
        return None
    identity = unquote(encoded_identity)
    if not identity or len(identity) > _MAX_IDENTITY_LENGTH:
        return None
    return identity


def _http_rejection(status_code: int, reason: str, detail: str) -> Response:
    """Tạo HTTP response dùng để từ chối WebSocket handshake.

    Args:
        status_code: HTTP status trả về cho client.
        reason: Reason phrase tương ứng với status.
        detail: Nội dung lỗi dạng text/plain.

    Returns:
        Response tương thích callback ``process_request`` của websockets.
    """
    body = f"{detail}\n".encode("utf-8")
    return Response(
        status_code,
        reason,
        Headers(
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ]
        ),
        body,
    )


def _requested_subprotocols(request: Request) -> set[str]:
    """Đọc danh sách subprotocol client gửi trong handshake.

    Args:
        request: HTTP request của WebSocket handshake.

    Returns:
        Set subprotocol đã trim; giá trị rỗng bị loại bỏ.
    """
    header = request.headers.get("Sec-WebSocket-Protocol", "")
    return {item.strip() for item in header.split(",") if item.strip()}


class OCPPChargePoint(ChargePoint):  # type: ignore[misc]
    """Adapter ``python-ocpp`` gắn WebSocket đã accept vào station identity.

    Attributes:
        id: Identity station được ``ChargePoint`` dùng khi dispatch OCPP.
        connection: WebSocket connection do ``websockets`` tạo sau handshake.

    Note:
        Class hiện chỉ cấu hình logger và giữ extension point cho OCPP action
        handlers; nó không tự tạo WebSocket connection.
    """

    def __init__(self, identity: str, connection: ServerConnection) -> None:
        """Khởi tạo adapter OCPP v201 cho connection đã validate.

        Args:
            identity: OCPP identity đã được resolve trong database.
            connection: WebSocket connection đã hoàn tất handshake.

        Side Effects:
            Khởi tạo state của lớp ``python-ocpp`` và gắn logger gateway.
        """
        super().__init__(identity, connection, logger=logger)


class OCPPServer:
    """Lifecycle và handshake policy của OCPP WebSocket gateway.

    Attributes:
        host: Địa chỉ bind lấy từ charging settings.
        port: Cổng bind lấy từ charging settings.
        session_factory: Shared async session factory để resolve station.
        _server: WebSocket server sau khi start thành công.
    """

    def __init__(
        self,
        *,
        host: str = settings.CHARGING_OCPP_HOST,
        port: int = settings.CHARGING_OCPP_PORT,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        """Khởi tạo gateway với shared dependency của backend.

        Args:
            host: Địa chỉ bind WebSocket listener.
            port: Cổng bind WebSocket listener.
            session_factory: Factory shared để validate station identity.

        Side Effects:
            Chưa mở socket; listener chỉ được bind khi gọi :meth:`start`.
        """
        self.host = host
        self.port = port
        self.session_factory = session_factory
        self._server: Server | None = None

    async def start(self) -> None:
        """Mở WebSocket listener chỉ cho OCPP 2.0.1.

        Raises:
            RuntimeError: Nếu listener đã được start trước đó.

        Side Effects:
            Bind host/port và đăng ký callback xử lý handshake, subprotocol và
            connection. Server instance được lưu vào ``self._server``.
        """
        if self._server is not None:
            raise RuntimeError("OCPP gateway is already running")
        # ``serve`` sở hữu TCP accept và WebSocket handshake; gateway chỉ cung
        # cấp policy validation cùng callback xử lý connection đã được accept.
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            subprotocols=[Subprotocol(OCPP_SUBPROTOCOL)],
            select_subprotocol=self._select_subprotocol,
            process_request=self._process_request,
            logger=logger,
        )
        logger.info(
            "OCPP gateway started",
            extra={"host": self.host, "port": self.port},
        )

    async def stop(self) -> None:
        """Dừng listener và giải phóng socket.

        Side Effects:
            Đóng listener hiện tại và chờ socket được giải phóng. Hàm an toàn
            khi listener chưa được start.
        """
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("OCPP gateway stopped")

    async def _process_request(
        self, _connection: ServerConnection, request: Request
    ) -> Response | None:
        """Validate path, subprotocol và identity trước WebSocket upgrade.

        Args:
            _connection: Connection tạm do websockets truyền vào callback.
            request: HTTP request handshake cần kiểm tra.

        Returns:
            HTTP rejection nếu handshake không hợp lệ; ``None`` để tiếp tục
            upgrade thành WebSocket.

        Side Effects:
            Mở một transaction read-only ngắn để resolve station identity và
            ghi log khi validation/database thất bại.
        """
        identity = parse_ocpp_identity(request.path)
        if identity is None:
            return _http_rejection(404, "Not Found", "Invalid OCPP station path")
        if OCPP_SUBPROTOCOL not in _requested_subprotocols(request):
            return _http_rejection(
                426,
                "Upgrade Required",
                f"Required WebSocket subprotocol: {OCPP_SUBPROTOCOL}",
            )
        try:
            async with self.session_factory.begin() as db:
                station = await repository.get_station_by_identity(
                    db, identity, include_deleted=False
                )
        except SQLAlchemyError:
            logger.exception(
                "Unable to validate OCPP station identity",
                extra={"ocpp_identity": identity},
            )
            return _http_rejection(
                503, "Service Unavailable", "Station validation unavailable"
            )
        if station is None:
            logger.warning(
                "Rejected OCPP connection for unknown station",
                extra={"ocpp_identity": identity},
            )
            return _http_rejection(404, "Not Found", "Unknown OCPP station identity")
        return None

    @staticmethod
    def _select_subprotocol(
        _connection: ServerConnection, client_subprotocols: Sequence[Subprotocol]
    ) -> Subprotocol | None:
        """Chọn duy nhất subprotocol OCPP 2.0.1 được gateway cho phép.

        Args:
            _connection: Connection đang thương lượng handshake.
            client_subprotocols: Các protocol client đề xuất.

        Returns:
            ``ocpp2.0.1`` nếu client đề xuất; ngược lại ``None``.
        """
        return (
            Subprotocol(OCPP_SUBPROTOCOL)
            if Subprotocol(OCPP_SUBPROTOCOL) in client_subprotocols
            else None
        )

    async def _handle_connection(self, connection: ServerConnection) -> None:
        """Chạy vòng đời một WebSocket sau khi handshake thành công.

        Args:
            connection: WebSocket connection đã được websockets accept.

        Side Effects:
            Tạo adapter ``OCPPChargePoint`` và chờ ``python-ocpp`` đọc message
            cho tới khi station ngắt kết nối hoặc handler bị hủy. Lỗi handler
            được log; ``CancelledError`` được giữ nguyên để shutdown hoạt động.
        """
        request = connection.request
        if request is None:
            await connection.close(code=1008, reason="Missing OCPP request path")
            return
        identity = parse_ocpp_identity(request.path)
        if identity is None:
            await connection.close(code=1008, reason="Invalid OCPP station path")
            return
        charge_point = OCPPChargePoint(identity, connection)
        logger.info(
            "OCPP station connected",
            extra={"ocpp_identity": identity, "subprotocol": connection.subprotocol},
        )
        try:
            await charge_point.start()
        except ConnectionClosed as error:
            logger.info(
                "OCPP station disconnected",
                extra={
                    "ocpp_identity": identity,
                    "close_code": error.rcvd.code if error.rcvd else None,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "OCPP station handler failed",
                extra={"ocpp_identity": identity},
            )


async def run_server(
    stop_event: asyncio.Event,
    *,
    server_factory: Callable[[], OCPPServer] = OCPPServer,
) -> None:
    """Chạy gateway cho tới khi nhận tín hiệu dừng.

    Args:
        stop_event: Event do entrypoint set khi nhận SIGINT/SIGTERM.
        server_factory: Factory tạo server; injectable để kiểm tra lifecycle.

    Side Effects:
        Start listener, chờ stop event và luôn stop listener trong ``finally``.
        Không tự đóng database vì database lifecycle thuộc entrypoint process.
    """
    server = server_factory()
    await server.start()
    try:
        await stop_event.wait()
    finally:
        await server.stop()
