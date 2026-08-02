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
    """Trích xuất OCPP identity từ URL path hợp lệ."""
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
    """Tạo HTTP response dùng để từ chối WebSocket handshake."""
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
    """Đọc danh sách subprotocol client gửi trong handshake."""
    header = request.headers.get("Sec-WebSocket-Protocol", "")
    return {item.strip() for item in header.split(",") if item.strip()}


class OCPPChargePoint(ChargePoint):  # type: ignore[misc]
    """Adapter ``python-ocpp`` gắn một WebSocket vào station identity."""

    def __init__(self, identity: str, connection: ServerConnection) -> None:
        """Khởi tạo adapter OCPP v201 cho connection đã validate."""
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
        """Khởi tạo gateway với shared dependency của backend."""
        self.host = host
        self.port = port
        self.session_factory = session_factory
        self._server: Server | None = None

    async def start(self) -> None:
        """Mở WebSocket listener chỉ cho OCPP 2.0.1."""
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
            extra={"host": self.host, "port": self.port},
        )

    async def stop(self) -> None:
        """Dừng listener và giải phóng socket."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("OCPP gateway stopped")

    async def _process_request(
        self, _connection: ServerConnection, request: Request
    ) -> Response | None:
        """Validate path, subprotocol và identity trước WebSocket upgrade."""
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
        """Chọn duy nhất subprotocol OCPP 2.0.1 được gateway cho phép."""
        return (
            Subprotocol(OCPP_SUBPROTOCOL)
            if Subprotocol(OCPP_SUBPROTOCOL) in client_subprotocols
            else None
        )

    async def _handle_connection(self, connection: ServerConnection) -> None:
        """Chạy vòng đời một WebSocket sau khi handshake thành công."""
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
    """Chạy gateway cho tới khi nhận tín hiệu dừng."""
    server = server_factory()
    await server.start()
    try:
        await stop_event.wait()
    finally:
        await server.stop()
