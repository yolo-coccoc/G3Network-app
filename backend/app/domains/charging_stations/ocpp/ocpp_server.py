"""WebSocket gateway tối thiểu cho OCPP 2.0.1.

Module này chỉ sở hữu transport connection và connection registry của station.
Nó validate URL/subprotocol/identity, giữ tối đa một kết nối active cho mỗi
station và dùng ``ocpp.v201.ChargePoint`` để đọc message. Handler nghiệp vụ cho
BootNotification, status, transaction và meter được triển khai ở các bước sau;
module này không tự tạo database engine hoặc session factory.
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
        request_path: Path có thể kèm query string từ WebSocket handshake.

    Returns:
        Identity đã URL-decode, hoặc ``None`` nếu path không đúng contract
        ``/ocpp/{ocpp_identity}``.
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
        status_code: Mã HTTP trả cho client.
        reason: Reason phrase ASCII của response.
        detail: Nội dung ngắn giải thích lỗi, không chứa credential.

    Returns:
        Response phù hợp với ``websockets.process_request``.
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
    """Đọc danh sách subprotocol client gửi trong WebSocket handshake.

    Args:
        request: Request handshake của thư viện ``websockets``.

    Returns:
        Tập subprotocol đã trim khoảng trắng.
    """
    header = request.headers.get("Sec-WebSocket-Protocol", "")
    return {item.strip() for item in header.split(",") if item.strip()}


# Legacy code intentionally disabled for the ideal MVP:
# class ConnectionRegistry:
#     """Registry dùng cho reconnect và thay thế connection cũ."""
#
#     # MVP giả định một WebSocket ổn định cho mỗi station; registry, lock và
#     # close_all chỉ được khôi phục khi planner reliability được mở lại.
#     ...


class OCPPChargePoint(ChargePoint):  # type: ignore[misc]
    """Adapter ``python-ocpp`` gắn một WebSocket vào identity của station.

    Bước 5 chưa đăng ký action handler. Vì vậy các OCPP CALL nghiệp vụ sẽ được
    thư viện trả về ``NotImplemented`` cho tới khi Bước 6/7 thêm handler.

    Attributes:
        id: OCPP identity do ``ChargePoint`` quản lý.
        _connection: WebSocket connection do gateway sở hữu lifecycle.
    """

    def __init__(
        self,
        identity: str,
        connection: ServerConnection,
    ) -> None:
        """Khởi tạo adapter OCPP v201 cho một connection.

        Args:
            identity: OCPP identity đã được gateway validate.
            connection: WebSocket connection đã negotiate subprotocol.
        """
        super().__init__(
            identity,
            connection,
            logger=logger,
        )


class OCPPServer:
    """Lifecycle và handshake policy của OCPP WebSocket gateway.

    Attributes:
        host: Địa chỉ bind lấy từ charging settings.
        port: Cổng bind lấy từ charging settings.
        session_factory: Shared async session factory dùng để resolve station.
        _server: WebSocket server sau khi ``start`` thành công.
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
            host: Host bind, mặc định từ settings.
            port: Port bind, mặc định từ settings.
            session_factory: Factory dùng chung từ ``app.libs.db.session``.
        """
        self.host = host
        self.port = port
        self.session_factory = session_factory
        self._server: Server | None = None

    async def start(self) -> None:
        """Mở WebSocket listener chỉ cho OCPP 2.0.1.

        Side Effects:
            Bind host/port và bắt đầu nhận handshake. Không tạo database engine
            hoặc logger mới.

        Raises:
            OSError: Nếu host/port không thể bind.
        """
        if self._server is not None:
            raise RuntimeError("OCPP gateway is already running")

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
            extra={
                "host": self.host,
                "port": self.port,
                "subprotocol": OCPP_SUBPROTOCOL,
            },
        )

    async def stop(self) -> None:
        """Dừng listener rồi đóng connection active.

        Side Effects:
            Ngừng nhận handshake mới, đóng WebSocket hiện tại và giải phóng
            socket listener. Database engine thuộc lifecycle entrypoint khác.
        """
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("OCPP gateway stopped")

    async def _process_request(
        self, _connection: ServerConnection, request: Request
    ) -> Response | None:
        """Validate path, subprotocol và station trước WebSocket upgrade.

        Args:
            _connection: Server connection do ``websockets`` cung cấp.
            request: HTTP upgrade request.

        Returns:
            ``None`` khi handshake hợp lệ; HTTP rejection response nếu sai
            protocol, path hoặc station identity chưa được pre-provision.
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
        """Chọn duy nhất subprotocol OCPP 2.0.1 đã được gateway cho phép."""
        return (
            Subprotocol(OCPP_SUBPROTOCOL)
            if Subprotocol(OCPP_SUBPROTOCOL) in client_subprotocols
            else None
        )

    async def _handle_connection(self, connection: ServerConnection) -> None:
        """Chạy vòng đời một WebSocket sau khi handshake thành công.

        Args:
            connection: Connection đã qua process request và negotiate protocol.

        Side Effects:
            Đăng ký connection, thay thế connection cũ của cùng identity, chạy
            ``ChargePoint.start`` và xóa registry khi connection kết thúc.
        """
        request = connection.request
        if request is None:
            await connection.close(code=1008, reason="Missing OCPP request path")
            return

        identity = parse_ocpp_identity(request.path)
        if identity is None:
            # process_request đã chặn trường hợp này; guard giữ invariant nếu
            # websockets thay đổi thứ tự callback trong phiên bản tương lai.
            await connection.close(code=1008, reason="Invalid OCPP station path")
            return

        charge_point = OCPPChargePoint(
            identity,
            connection,
        )
        logger.info(
            "OCPP station connected",
            extra={
                "ocpp_identity": identity,
                "subprotocol": connection.subprotocol,
            },
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
        stop_event: Event do process entrypoint set khi nhận SIGINT/SIGTERM.
        server_factory: Factory để test inject server giả mà không bind socket.

    Side Effects:
        Mở listener, chờ event và dừng gateway graceful khi event được set.
    """
    server = server_factory()
    await server.start()
    try:
        await stop_event.wait()
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# LEGACY IMPLEMENTATION (COMMENTED OUT FOR THE IDEAL MVP)
# ---------------------------------------------------------------------------
# """WebSocket gateway tối thiểu cho OCPP 2.0.1.
#
# Module này chỉ sở hữu transport connection và connection registry của station.
# Nó validate URL/subprotocol/identity, giữ tối đa một kết nối active cho mỗi
# station và dùng ``ocpp.v201.ChargePoint`` để đọc message. Handler nghiệp vụ cho
# BootNotification, status, transaction và meter được triển khai ở các bước sau;
# module này không tự tạo database engine hoặc session factory.
# """
#
# import asyncio
# import logging
# from collections.abc import Callable, Sequence
# from typing import Final
# from urllib.parse import unquote, urlsplit
#
# from ocpp.v201 import ChargePoint  # type: ignore[import-untyped]
# from sqlalchemy.exc import SQLAlchemyError
# from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
# from websockets.asyncio.server import Server, ServerConnection, serve
# from websockets.datastructures import Headers
# from websockets.exceptions import ConnectionClosed
# from websockets.http11 import Request, Response
# from websockets.typing import Subprotocol
#
# from app.domains.charging_stations import repository
# from app.libs.common.config import settings
# from app.libs.db.session import async_session_factory
#
# logger = logging.getLogger(__name__)
#
# OCPP_SUBPROTOCOL: Final[str] = "ocpp2.0.1"
# OCPP_PATH_PREFIX: Final[str] = "/ocpp/"
# _MAX_IDENTITY_LENGTH: Final[int] = 255
#
#
# def parse_ocpp_identity(request_path: str) -> str | None:
#     """Trích xuất OCPP identity từ URL path hợp lệ.
#
#     Args:
#         request_path: Path có thể kèm query string từ WebSocket handshake.
#
#     Returns:
#         Identity đã URL-decode, hoặc ``None`` nếu path không đúng contract
#         ``/ocpp/{ocpp_identity}``.
#     """
#     path = urlsplit(request_path).path
#     if not path.startswith(OCPP_PATH_PREFIX):
#         return None
#
#     encoded_identity = path[len(OCPP_PATH_PREFIX) :]
#     if not encoded_identity or "/" in encoded_identity:
#         return None
#
#     identity = unquote(encoded_identity)
#     if not identity or len(identity) > _MAX_IDENTITY_LENGTH:
#         return None
#     return identity
#
#
# def _http_rejection(status_code: int, reason: str, detail: str) -> Response:
#     """Tạo HTTP response dùng để từ chối WebSocket handshake.
#
#     Args:
#         status_code: Mã HTTP trả cho client.
#         reason: Reason phrase ASCII của response.
#         detail: Nội dung ngắn giải thích lỗi, không chứa credential.
#
#     Returns:
#         Response phù hợp với ``websockets.process_request``.
#     """
#     body = f"{detail}\n".encode("utf-8")
#     return Response(
#         status_code,
#         reason,
#         Headers(
#             [
#                 ("Content-Type", "text/plain; charset=utf-8"),
#                 ("Content-Length", str(len(body))),
#             ]
#         ),
#         body,
#     )
#
#
# def _requested_subprotocols(request: Request) -> set[str]:
#     """Đọc danh sách subprotocol client gửi trong WebSocket handshake.
#
#     Args:
#         request: Request handshake của thư viện ``websockets``.
#
#     Returns:
#         Tập subprotocol đã trim khoảng trắng.
#     """
#     header = request.headers.get("Sec-WebSocket-Protocol", "")
#     return {item.strip() for item in header.split(",") if item.strip()}
#
#
# class ConnectionRegistry:
#     """Registry in-memory cho connection active của từng OCPP identity.
#
#     Registry không phải nguồn trạng thái thiết bị bền vững; nó chỉ bảo đảm một
#     station không có hai WebSocket active trong cùng process. Khi reconnect,
#     connection mới thay thế connection cũ và connection cũ được đóng trước khi
#     handler mới bắt đầu đọc message.
#
#     Attributes:
#         _connections: Mapping identity tới WebSocket đang được giữ active.
#         _lock: Lock bảo vệ thao tác thay thế/xóa/đóng đồng thời.
#     """
#
#     def __init__(self) -> None:
#         """Khởi tạo registry rỗng cho một process gateway."""
#         self._connections: dict[str, ServerConnection] = {}
#         self._lock = asyncio.Lock()
#
#     async def replace(
#         self, identity: str, connection: ServerConnection
#     ) -> ServerConnection | None:
#         """Đăng ký connection mới và trả connection cũ cần đóng.
#
#         Args:
#             identity: OCPP identity của station.
#             connection: WebSocket connection mới.
#
#         Returns:
#             Connection cũ nếu identity đã có connection active; ngược lại
#             ``None``.
#         """
#         async with self._lock:
#             previous = self._connections.get(identity)
#             self._connections[identity] = connection
#             return previous
#
#     async def remove(self, identity: str, connection: ServerConnection) -> None:
#         """Xóa connection nếu nó vẫn là connection active hiện tại.
#
#         Args:
#             identity: OCPP identity cần xóa.
#             connection: Connection của handler đang kết thúc.
#
#         Side Effects:
#             Không xóa connection mới hơn đã thay thế connection truyền vào.
#         """
#         async with self._lock:
#             if self._connections.get(identity) is connection:
#                 del self._connections[identity]
#
#     async def close_all(self) -> None:
#         """Đóng tất cả connection hiện tại trong graceful shutdown.
#
#         Side Effects:
#             Xóa registry và gửi close code 1001 tới các station còn kết nối.
#             Lỗi đóng từng connection được thu thập để không ngăn connection
#             khác được đóng.
#         """
#         async with self._lock:
#             connections = list(self._connections.values())
#             self._connections.clear()
#
#         await asyncio.gather(
#             *(
#                 connection.close(code=1001, reason="Gateway shutting down")
#                 for connection in connections
#             ),
#             return_exceptions=True,
#         )
#
#
# class OCPPChargePoint(ChargePoint):  # type: ignore[misc]
#     """Adapter ``python-ocpp`` gắn một WebSocket vào identity của station.
#
#     Bước 5 chưa đăng ký action handler. Vì vậy các OCPP CALL nghiệp vụ sẽ được
#     thư viện trả về ``NotImplemented`` cho tới khi Bước 6/7 thêm handler.
#
#     Attributes:
#         id: OCPP identity do ``ChargePoint`` quản lý.
#         _connection: WebSocket connection do gateway sở hữu lifecycle.
#     """
#
#     def __init__(
#         self,
#         identity: str,
#         connection: ServerConnection,
#         response_timeout: float,
#     ) -> None:
#         """Khởi tạo adapter OCPP v201 cho một connection.
#
#         Args:
#             identity: OCPP identity đã được gateway validate.
#             connection: WebSocket connection đã negotiate subprotocol.
#             response_timeout: Timeout chờ response khi gateway gửi CALL.
#         """
#         super().__init__(
#             identity,
#             connection,
#             response_timeout=response_timeout,
#             logger=logger,
#         )
#
#
# class OCPPServer:
#     """Lifecycle và handshake policy của OCPP WebSocket gateway.
#
#     Attributes:
#         host: Địa chỉ bind lấy từ charging settings.
#         port: Cổng bind lấy từ charging settings.
#         session_factory: Shared async session factory dùng để resolve station.
#         registry: Connection registry của process hiện tại.
#         _server: WebSocket server sau khi ``start`` thành công.
#     """
#
#     def __init__(
#         self,
#         *,
#         host: str = settings.CHARGING_OCPP_HOST,
#         port: int = settings.CHARGING_OCPP_PORT,
#         session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
#     ) -> None:
#         """Khởi tạo gateway với shared dependency của backend.
#
#         Args:
#             host: Host bind, mặc định từ settings.
#             port: Port bind, mặc định từ settings.
#             session_factory: Factory dùng chung từ ``app.libs.db.session``.
#         """
#         self.host = host
#         self.port = port
#         self.session_factory = session_factory
#         self.registry = ConnectionRegistry()
#         self._server: Server | None = None
#
#     async def start(self) -> None:
#         """Mở WebSocket listener chỉ cho OCPP 2.0.1.
#
#         Side Effects:
#             Bind host/port và bắt đầu nhận handshake. Không tạo database engine
#             hoặc logger mới.
#
#         Raises:
#             OSError: Nếu host/port không thể bind.
#         """
#         if self._server is not None:
#             raise RuntimeError("OCPP gateway is already running")
#
#         self._server = await serve(
#             self._handle_connection,
#             self.host,
#             self.port,
#             subprotocols=[Subprotocol(OCPP_SUBPROTOCOL)],
#             select_subprotocol=self._select_subprotocol,
#             process_request=self._process_request,
#             logger=logger,
#         )
#         logger.info(
#             "OCPP gateway started",
#             extra={
#                 "host": self.host,
#                 "port": self.port,
#                 "subprotocol": OCPP_SUBPROTOCOL,
#             },
#         )
#
#     async def stop(self) -> None:
#         """Dừng listener rồi đóng connection active.
#
#         Side Effects:
#             Ngừng nhận handshake mới, đóng WebSocket hiện tại và giải phóng
#             socket listener. Database engine thuộc lifecycle entrypoint khác.
#         """
#         if self._server is not None:
#             self._server.close()
#             await self._server.wait_closed()
#             self._server = None
#         await self.registry.close_all()
#         logger.info("OCPP gateway stopped")
#
#     async def _process_request(
#         self, _connection: ServerConnection, request: Request
#     ) -> Response | None:
#         """Validate path, subprotocol và station trước WebSocket upgrade.
#
#         Args:
#             _connection: Server connection do ``websockets`` cung cấp.
#             request: HTTP upgrade request.
#
#         Returns:
#             ``None`` khi handshake hợp lệ; HTTP rejection response nếu sai
#             protocol, path hoặc station identity chưa được pre-provision.
#         """
#         identity = parse_ocpp_identity(request.path)
#         if identity is None:
#             return _http_rejection(404, "Not Found", "Invalid OCPP station path")
#
#         if OCPP_SUBPROTOCOL not in _requested_subprotocols(request):
#             return _http_rejection(
#                 426,
#                 "Upgrade Required",
#                 f"Required WebSocket subprotocol: {OCPP_SUBPROTOCOL}",
#             )
#
#         try:
#             async with self.session_factory.begin() as db:
#                 station = await repository.get_station_by_identity(
#                     db, identity, include_deleted=False
#                 )
#         except SQLAlchemyError:
#             logger.exception(
#                 "Unable to validate OCPP station identity",
#                 extra={"ocpp_identity": identity},
#             )
#             return _http_rejection(
#                 503, "Service Unavailable", "Station validation unavailable"
#             )
#
#         if station is None:
#             logger.warning(
#                 "Rejected OCPP connection for unknown station",
#                 extra={"ocpp_identity": identity},
#             )
#             return _http_rejection(404, "Not Found", "Unknown OCPP station identity")
#         return None
#
#     @staticmethod
#     def _select_subprotocol(
#         _connection: ServerConnection, client_subprotocols: Sequence[Subprotocol]
#     ) -> Subprotocol | None:
#         """Chọn duy nhất subprotocol OCPP 2.0.1 đã được gateway cho phép."""
#         return (
#             Subprotocol(OCPP_SUBPROTOCOL)
#             if Subprotocol(OCPP_SUBPROTOCOL) in client_subprotocols
#             else None
#         )
#
#     async def _handle_connection(self, connection: ServerConnection) -> None:
#         """Chạy vòng đời một WebSocket sau khi handshake thành công.
#
#         Args:
#             connection: Connection đã qua process request và negotiate protocol.
#
#         Side Effects:
#             Đăng ký connection, thay thế connection cũ của cùng identity, chạy
#             ``ChargePoint.start`` và xóa registry khi connection kết thúc.
#         """
#         request = connection.request
#         if request is None:
#             await connection.close(code=1008, reason="Missing OCPP request path")
#             return
#
#         identity = parse_ocpp_identity(request.path)
#         if identity is None:
#             # process_request đã chặn trường hợp này; guard giữ invariant nếu
#             # websockets thay đổi thứ tự callback trong phiên bản tương lai.
#             await connection.close(code=1008, reason="Invalid OCPP station path")
#             return
#
#         previous = await self.registry.replace(identity, connection)
#         if previous is not None and previous is not connection:
#             logger.info(
#                 "Replacing previous OCPP connection after reconnect",
#                 extra={"ocpp_identity": identity},
#             )
#             await previous.close(code=1000, reason="Replaced by reconnect")
#
#         charge_point = OCPPChargePoint(
#             identity,
#             connection,
#             response_timeout=settings.CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS,
#         )
#         logger.info(
#             "OCPP station connected",
#             extra={
#                 "ocpp_identity": identity,
#                 "subprotocol": connection.subprotocol,
#             },
#         )
#         try:
#             await charge_point.start()
#         except ConnectionClosed as error:
#             logger.info(
#                 "OCPP station disconnected",
#                 extra={
#                     "ocpp_identity": identity,
#                     "close_code": error.rcvd.code if error.rcvd else None,
#                 },
#             )
#         except asyncio.CancelledError:
#             raise
#         except Exception:
#             logger.exception(
#                 "OCPP station handler failed",
#                 extra={"ocpp_identity": identity},
#             )
#         finally:
#             await self.registry.remove(identity, connection)
#
#
# async def run_server(
#     stop_event: asyncio.Event,
#     *,
#     server_factory: Callable[[], OCPPServer] = OCPPServer,
# ) -> None:
#     """Chạy gateway cho tới khi nhận tín hiệu dừng.
#
#     Args:
#         stop_event: Event do process entrypoint set khi nhận SIGINT/SIGTERM.
#         server_factory: Factory để test inject server giả mà không bind socket.
#
#     Side Effects:
#         Mở listener, chờ event và dừng gateway graceful khi event được set.
#     """
#     server = server_factory()
#     await server.start()
#     try:
#         await stop_event.wait()
#     finally:
#         await server.stop()
#
