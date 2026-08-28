"""WebSocket gateway tối thiểu cho OCPP 2.0.1.

Gateway chỉ validate path, subprotocol và identity station đã pre-provision,
sau đó giữ một connection ổn định trong process. Reliability production,
reconnect và technical status history nằm ngoài active path.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final
from urllib.parse import unquote, urlsplit
from uuid import UUID

from ocpp.routing import on  # type: ignore[import-untyped]
from ocpp.v201 import ChargePoint  # type: ignore[import-untyped]
from ocpp.v201 import call_result
from ocpp.v201.datatypes import (  # type: ignore[import-untyped]
    EVSEType,
    MeterValueType,
    TransactionType,
)
from ocpp.v201.enums import (  # type: ignore[import-untyped]
    Action,
    TransactionEventEnumType,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.typing import Subprotocol

from app.domains.charging_sessions import service as charging_sessions_service
from app.domains.charging_sessions.types import (
    MeterSampleInput,
    SessionEventType,
)
from app.domains.charging_stations import repository
from app.domains.charging_stations import service as charging_stations_service
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


def parse_ocpp_timestamp(value: str) -> datetime:
    """Parse timestamp OCPP thành datetime timezone-aware.

    Args:
        value: Timestamp ISO-8601 trong OCPP payload.

    Returns:
        Datetime giữ timezone để service chuẩn hóa về UTC.

    Raises:
        ValueError: Nếu timestamp không có timezone hoặc sai định dạng.
    """
    normalized = value.replace("Z", "+00:00")
    timestamp = datetime.fromisoformat(normalized)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("OCPP timestamp phải có timezone")
    return timestamp


def extract_meter_samples(
    meter_values: list[MeterValueType],
) -> list[MeterSampleInput]:
    """Đưa các sample trong OCPP message vào input persistence.

    Args:
        meter_values: Các nhóm sample do ``python-ocpp`` parse.

    Returns:
        Danh sách sample theo đúng thứ tự payload.
    """
    samples: list[MeterSampleInput] = []
    for meter_value in meter_values:
        sampled_at = parse_ocpp_timestamp(meter_value.timestamp)
        for sampled_value in meter_value.sampled_value:
            samples.append(
                MeterSampleInput(
                    sampled_at=sampled_at,
                    value_wh=Decimal(str(sampled_value.value)),
                )
            )
    return samples


async def resolve_ocpp_topology(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    evse: EVSEType | None,
) -> tuple[UUID, UUID, UUID]:
    """Resolve EVSE/connector OCPP identity thành UUID primitive.

    Args:
        db: Async session của action transaction.
        ocpp_identity: Identity của station OCPP.
        evse: EVSE object từ OCPP payload.

    Returns:
        Tuple internal IDs của station, EVSE và connector.

    Raises:
        ValueError: Nếu payload thiếu EVSE hoặc connector.
    """
    if evse is None or evse.connector_id is None:
        raise ValueError("TransactionEvent phải có EVSE và connector")
    return await charging_stations_service.resolve_ocpp_topology(
        db,
        ocpp_identity=ocpp_identity,
        ocpp_evse_id=evse.id,
        ocpp_connector_id=evse.connector_id,
    )


def select_ocpp_subprotocol(
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


class OCPPChargePoint(ChargePoint):  # type: ignore[misc]
    """Adapter ``python-ocpp`` gắn WebSocket đã accept vào station identity.

    Attributes:
        id: Identity station được ``ChargePoint`` dùng khi dispatch OCPP.
        connection: WebSocket connection do ``websockets`` tạo sau handshake.
        session_factory: Shared factory dùng cho mỗi operation persistence.
        _session_by_evse: Mapping OCPP EVSE ID sang session UUID cho MeterValues.

    Note:
        Class nhận OCPP action sau handshake, chuyển payload thành primitive
        values và không tự tạo WebSocket connection.
    """

    def __init__(
        self,
        identity: str,
        connection: ServerConnection,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Khởi tạo adapter OCPP v201 cho connection đã validate.

        Args:
            identity: OCPP identity đã được resolve trong database.
            connection: WebSocket connection đã hoàn tất handshake.
            session_factory: Shared factory sở hữu transaction cho action handler.

        Side Effects:
            Khởi tạo state của lớp ``python-ocpp`` và gắn logger gateway.
        """
        super().__init__(identity, connection, logger=logger)
        self.session_factory = session_factory
        self._session_by_evse: dict[int, UUID] = {}

    @on(Action.transaction_event)  # type: ignore[untyped-decorator]
    async def on_transaction_event(
        self,
        event_type: TransactionEventEnumType,
        timestamp: str,
        trigger_reason: object,
        seq_no: int,
        transaction_info: TransactionType,
        meter_value: list[MeterValueType] | None = None,
        evse: EVSEType | None = None,
        **_: object,
    ) -> call_result.TransactionEvent:
        """Persist TransactionEvent bằng primitive values rồi ACK OCPP.

        Args:
            event_type: ``Started``, ``Updated`` hoặc ``Ended``.
            timestamp: Thời điểm event theo OCPP.
            trigger_reason: Trigger OCPP, hiện chỉ được parse để giữ contract.
            seq_no: Sequence OCPP, chưa thuộc active persistence schema.
            transaction_info: Transaction dataclass do parser OCPP tạo.
            meter_value: Meter đầu/cuối tùy event, dạng dataclass OCPP.
            evse: EVSE và connector OCPP cần resolve.
            **_: Các field OCPP optional không thuộc MVP.

        Returns:
            Response rỗng hợp lệ cho TransactionEvent.

        Side Effects:
            Gọi public service ``charging_sessions`` trong transaction atomic;
            chỉ cập nhật mapping EVSE → session sau khi transaction commit.
        """
        del trigger_reason, seq_no
        event_map = {
            TransactionEventEnumType.started: SessionEventType.STARTED,
            TransactionEventEnumType.updated: SessionEventType.UPDATED,
            TransactionEventEnumType.ended: SessionEventType.ENDED,
        }
        session_event = event_map[event_type]
        samples = extract_meter_samples(meter_value) if meter_value else []
        meter_start_wh = samples[0].value_wh if samples else None
        meter_end_wh = samples[-1].value_wh if samples else None
        transaction_id = transaction_info.transaction_id
        async with self.session_factory.begin() as db:
            station_id, evse_id, connector_id = await resolve_ocpp_topology(
                db,
                ocpp_identity=self.id,
                evse=evse,
            )
            result = await charging_sessions_service.ingest_transaction_event(
                db,
                station_id=station_id,
                evse_id=evse_id,
                connector_id=connector_id,
                transaction_id=transaction_id,
                event_type=session_event,
                event_occurred_at=parse_ocpp_timestamp(timestamp),
                meter_start_wh=(
                    meter_start_wh
                    if session_event is SessionEventType.STARTED
                    else None
                ),
                meter_end_wh=(
                    meter_end_wh
                    if session_event is not SessionEventType.STARTED
                    else None
                ),
            )
        if session_event is SessionEventType.ENDED:
            if evse is not None:
                self._session_by_evse.pop(evse.id, None)
        else:
            if evse is not None:
                self._session_by_evse[evse.id] = result.session_id
        return call_result.TransactionEvent()

    @on(Action.meter_values)  # type: ignore[untyped-decorator]
    async def on_meter_values(
        self,
        evse_id: int,
        meter_value: list[MeterValueType],
        **_: object,
    ) -> call_result.MeterValues:
        """Persist từng energy sample MeterValues trong một transaction.

        Args:
            evse_id: OCPP EVSE ID dùng để tìm session trên connection.
            meter_value: Nhóm sample dataclass OCPP cần chuyển về Wh.
            **_: Field OCPP optional không thuộc MVP.

        Returns:
            Response rỗng hợp lệ cho MeterValues.

        Side Effects:
            Gọi ``ingest_meter_values`` từng sample trên cùng AsyncSession;
            exception làm entry transaction rollback toàn bộ message.
        """
        session_id = self._session_by_evse[evse_id]
        samples = extract_meter_samples(meter_value)
        async with self.session_factory.begin() as db:
            for sample in samples:
                await charging_sessions_service.ingest_meter_values(
                    db, session_id=session_id, sample=sample
                )
        return call_result.MeterValues()


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
            select_subprotocol=select_ocpp_subprotocol,
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
            reject identity chưa được pre-provision.
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
        async with self.session_factory.begin() as db:
            station = await repository.get_station_by_identity(
                db, identity, include_deleted=False
            )
        if station is None:
            logger.warning(
                "Rejected OCPP connection for unknown station",
                extra={"ocpp_identity": identity},
            )
            return _http_rejection(404, "Not Found", "Unknown OCPP station identity")
        return None

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
        # ``process_request`` đã kiểm tra request và path trước khi upgrade;
        # assertions chỉ ghi lại invariant đó cho type checker ở happy path.
        assert request is not None, "WebSocket request phải tồn tại sau handshake"
        identity = parse_ocpp_identity(request.path)
        assert identity is not None, "OCPP identity phải hợp lệ sau handshake"
        # ConnectionRegistry, reconnect replacement, offline detector, timeout
        # và retry recovery thuộc production path bị hoãn; MVP giữ state trong
        # đúng connection này và để process boundary sở hữu lifecycle socket.
        charge_point = OCPPChargePoint(identity, connection, self.session_factory)
        logger.info(
            "OCPP station connected",
            extra={"ocpp_identity": identity, "subprotocol": connection.subprotocol},
        )
        try:
            await charge_point.start()
        except ConnectionClosed:
            # Station đóng kết nối bình thường sau khi happy path nhận ACK.
            pass
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
