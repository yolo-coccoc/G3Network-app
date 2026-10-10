"""Minimal WebSocket gateway for OCPP 2.0.1 and OCPP 1.6J.

The gateway only validates the path, subprotocol, and identity of a
pre-provisioned station, then keeps one stable connection within the
process. The negotiated subprotocol (``ocpp2.0.1`` or ``ocpp1.6``) selects
the adapter class: ``OCPP201ChargePoint`` (``ocpp201_charge_point.py``) or
``OCPP16ChargePoint`` (``ocpp16_charge_point.py``); this module holds no
protocol handler itself. It also records which chargers are connected to this
process and runs the command loop (``command_loop.py``) that sends the queued
``charging_station_commands`` of those chargers (PR-16). Every frame exchanged on a connection is stored
verbatim by ``RecordingConnection`` (see ``raw_log.py``) before any parsing.
Production reliability, reconnect, and technical status history are outside
the active path.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Final
from urllib.parse import unquote, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.typing import Subprotocol

import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.ocpp.command_loop import (
    StationConnectionRegistry,
    run_command_loop,
)
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from app.domains.charging_stations.ocpp.ocpp201_charge_point import (
    OCPP201ChargePoint,
)
from app.domains.charging_stations.ocpp.raw_log import RecordingConnection
from app.domains.charging_stations.types import ChargingResourceStatus
from app.libs.common.config import settings
from app.libs.db.session import async_session_factory

logger = logging.getLogger(__name__)

OCPP_SUBPROTOCOL: Final[str] = "ocpp2.0.1"
OCPP16_SUBPROTOCOL: Final[str] = "ocpp1.6"
# Server preference order: when a client offers both, the newer protocol wins.
SUPPORTED_SUBPROTOCOLS: Final[tuple[str, ...]] = (
    OCPP_SUBPROTOCOL,
    OCPP16_SUBPROTOCOL,
)
OCPP_PATH_PREFIX: Final[str] = "/ocpp/"
_MAX_IDENTITY_LENGTH: Final[int] = 255


def parse_ocpp_identity(request_path: str) -> str | None:
    """Extract the OCPP identity from a valid URL path.

    Args:
        request_path: Request target, which may contain a query string.

    Returns:
        The URL-decoded identity, or ``None`` if the path does not match the
        contract.
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
    """Create an HTTP response used to reject a WebSocket handshake.

    Args:
        status_code: HTTP status returned to the client.
        reason: Reason phrase matching the status.
        detail: Error content as text/plain.

    Returns:
        A response compatible with the websockets ``process_request``
        callback.
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
    """Read the list of subprotocols the client sent in the handshake.

    Args:
        request: HTTP request of the WebSocket handshake.

    Returns:
        A set of trimmed subprotocols; empty values are dropped.
    """
    header = request.headers.get("Sec-WebSocket-Protocol", "")
    return {item.strip() for item in header.split(",") if item.strip()}


def select_ocpp_subprotocol(
    _connection: ServerConnection, client_subprotocols: Sequence[Subprotocol]
) -> Subprotocol | None:
    """Select the OCPP subprotocol to use for a connection.

    Args:
        _connection: Connection currently negotiating the handshake.
        client_subprotocols: Protocols proposed by the client.

    Returns:
        The first protocol in the server's preference order
        (``SUPPORTED_SUBPROTOCOLS``) that the client proposed, or ``None``
        if the client proposed none of them.
    """
    for supported in SUPPORTED_SUBPROTOCOLS:
        if Subprotocol(supported) in client_subprotocols:
            return Subprotocol(supported)
    return None


def create_charge_point(
    ocpp_subprotocol: str | None,
    identity: str,
    connection: RecordingConnection,
    session_factory: async_sessionmaker[AsyncSession],
) -> "OCPP201ChargePoint | OCPP16ChargePoint":
    """Create the adapter class that matches the negotiated subprotocol.

    Args:
        ocpp_subprotocol: Subprotocol negotiated for this connection.
        identity: OCPP identity already resolved in the database.
        connection: Recording wrapper around the accepted WebSocket.
        session_factory: Shared async session factory.

    Returns:
        An ``OCPP16ChargePoint`` for ``ocpp1.6``; otherwise an
        ``OCPP201ChargePoint`` (the default protocol of this gateway).
    """
    if ocpp_subprotocol == OCPP16_SUBPROTOCOL:
        return OCPP16ChargePoint(identity, connection, session_factory)
    return OCPP201ChargePoint(identity, connection, session_factory)


class OCPPServer:
    """Lifecycle and handshake policy of the OCPP WebSocket gateway.

    Attributes:
        host: Bind address taken from charging settings.
        port: Bind port taken from charging settings.
        session_factory: Shared async session factory used to resolve
            stations.
        connections: The chargers currently connected to this process.
        _server: WebSocket server after it has started successfully.
        _command_stop: Event that ends the command loop.
        _command_task: The running command loop, while the server runs.
    """

    def __init__(
        self,
        *,
        host: str = settings.CHARGING_OCPP_HOST,
        port: int = settings.CHARGING_OCPP_PORT,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        """Initialize the gateway with the backend's shared dependencies.

        Args:
            host: Bind address for the WebSocket listener.
            port: Bind port for the WebSocket listener.
            session_factory: Shared factory used to validate station
                identity.

        Side Effects:
            Does not open a socket yet; the listener is only bound when
            :meth:`start` is called.
        """
        self.host = host
        self.port = port
        self.session_factory = session_factory
        self.connections = StationConnectionRegistry()
        self._server: Server | None = None
        self._command_stop = asyncio.Event()
        self._command_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Open the WebSocket listener for OCPP 2.0.1 and OCPP 1.6J.

        Raises:
            RuntimeError: If the listener was already started.

        Side Effects:
            Binds host/port and registers callbacks handling the handshake,
            subprotocol, and connection. The server instance is stored in
            ``self._server``. Incoming messages larger than
            ``CHARGING_OCPP_MAX_MESSAGE_BYTES`` are refused by the WebSocket
            library (close code 1009) rather than stored truncated.
        """
        if self._server is not None:
            raise RuntimeError("OCPP gateway is already running")
        # ``serve`` owns the TCP accept and WebSocket handshake; the gateway
        # only supplies the validation policy and the callback that handles
        # an already accepted connection.
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            subprotocols=[Subprotocol(name) for name in SUPPORTED_SUBPROTOCOLS],
            select_subprotocol=select_ocpp_subprotocol,
            process_request=self._process_request,
            max_size=settings.CHARGING_OCPP_MAX_MESSAGE_BYTES,
            logger=logger,
        )
        self._command_stop.clear()
        self._command_task = asyncio.create_task(
            run_command_loop(self.connections, self.session_factory, self._command_stop)
        )
        logger.info(
            "OCPP gateway started",
            extra={"host": self.host, "port": self.port},
        )

    async def stop(self) -> None:
        """Stop the listener and release the socket.

        Side Effects:
            Stops the command loop (cancelling commands still in flight),
            closes the current listener and waits for the socket to be
            released. Safe to call when the listener has not been started.
        """
        self._command_stop.set()
        if self._command_task is not None:
            await self._command_task
            self._command_task = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("OCPP gateway stopped")

    async def _process_request(
        self, _connection: ServerConnection, request: Request
    ) -> Response | None:
        """Validate the path, subprotocol, and identity before the WebSocket upgrade.

        Args:
            _connection: Temporary connection passed into the callback by
                websockets.
            request: HTTP handshake request to validate.

        Returns:
            An HTTP rejection if the handshake is invalid; ``None`` to
            continue the upgrade to WebSocket.

        Side Effects:
            Opens a short read-only transaction to resolve the station
            identity and rejects an identity that has not been
            pre-provisioned (404) or whose charger is not ``ACTIVE`` (403,
            STN-02: a registered charger connects only while in service).
        """
        identity = parse_ocpp_identity(request.path)
        if identity is None:
            return _http_rejection(404, "Not Found", "Invalid OCPP station path")
        if not set(SUPPORTED_SUBPROTOCOLS) & _requested_subprotocols(request):
            return _http_rejection(
                426,
                "Upgrade Required",
                "Required WebSocket subprotocol: "
                + " or ".join(SUPPORTED_SUBPROTOCOLS),
            )
        async with self.session_factory.begin() as db:
            station = await charging_stations_repository.get_station_by_identity(
                db, identity, include_deleted=False
            )
        if station is None:
            logger.warning(
                "Rejected OCPP connection for unknown station",
                extra={"ocpp_identity": identity},
            )
            return _http_rejection(404, "Not Found", "Unknown OCPP station identity")
        if station.status != ChargingResourceStatus.ACTIVE.value:
            # STN-02: a registered charger connects only while it is ACTIVE.
            logger.warning(
                "Rejected OCPP connection for station out of service",
                extra={"ocpp_identity": identity},
            )
            return _http_rejection(403, "Forbidden", "OCPP station is out of service")
        return None

    async def _handle_connection(self, connection: ServerConnection) -> None:
        """Run the lifecycle of one WebSocket after a successful handshake.

        Args:
            connection: WebSocket connection accepted by websockets.

        Side Effects:
            Creates the adapter for the negotiated subprotocol
            (``create_charge_point``) over a ``RecordingConnection`` and
            waits for ``python-ocpp`` to read messages until the station
            disconnects or the handler is cancelled. Every frame is stored verbatim in its own transaction.
            Handler errors (including a failure to store a frame) are
            logged; ``CancelledError`` is re-raised so shutdown keeps working.
            If the station was soft-deleted or set ``INACTIVE`` between the
            handshake and this point, the connection is closed with code 1008.
        """
        request = connection.request
        # ``process_request`` already validated the request and path before
        # the upgrade; the assertions only record that invariant for the
        # type checker on the happy path.
        assert request is not None, "WebSocket request must exist after the handshake"
        identity = parse_ocpp_identity(request.path)
        assert identity is not None, "OCPP identity must be valid after the handshake"
        # ConnectionRegistry, reconnect replacement, offline detector,
        # timeout, and retry recovery belong to the production path and are
        # deferred; the MVP keeps state on this exact connection and lets
        # the process boundary own the socket lifecycle.
        async with self.session_factory.begin() as db:
            station = await charging_stations_repository.get_station_by_identity(
                db, identity, include_deleted=False
            )
        if station is None or station.status != ChargingResourceStatus.ACTIVE.value:
            # Only reachable if the station was deleted or taken out of service
            # right after the handshake validation; nothing can be logged
            # without a station.
            logger.warning(
                "Closing OCPP connection for station deleted after handshake",
                extra={"ocpp_identity": identity},
            )
            await connection.close(code=1008, reason="Unknown station identity")
            return
        recording_connection = RecordingConnection(
            connection,
            station_id=station.station_id,
            ocpp_subprotocol=connection.subprotocol or OCPP_SUBPROTOCOL,
            session_factory=self.session_factory,
        )
        charge_point = create_charge_point(
            connection.subprotocol,
            identity,
            recording_connection,
            self.session_factory,
        )
        logger.info(
            "OCPP station connected",
            extra={"ocpp_identity": identity, "subprotocol": connection.subprotocol},
        )
        self.connections.register(station.station_id, charge_point)
        try:
            await charge_point.start()
        except ConnectionClosed:
            # The station closed the connection normally after the happy
            # path received the ACK.
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "OCPP station handler failed",
                extra={"ocpp_identity": identity},
            )
        finally:
            self.connections.unregister(station.station_id, charge_point)
            # A 1.6J adapter may have a post-boot request in flight; it must not
            # outlive its connection.
            if isinstance(charge_point, OCPP16ChargePoint):
                await charge_point.cancel_background_tasks()


async def run_server(
    stop_event: asyncio.Event,
    *,
    server_factory: Callable[[], OCPPServer] = OCPPServer,
) -> None:
    """Run the gateway until it receives a stop signal.

    Args:
        stop_event: Event set by the entrypoint on SIGINT/SIGTERM.
        server_factory: Factory that creates the server; injectable to test
            the lifecycle.

    Side Effects:
        Starts the listener, waits for the stop event, and always stops the
        listener in a ``finally`` block. Does not close the database itself,
        since the database lifecycle belongs to the entrypoint process.
    """
    server = server_factory()
    await server.start()
    try:
        await stop_event.wait()
    finally:
        await server.stop()
