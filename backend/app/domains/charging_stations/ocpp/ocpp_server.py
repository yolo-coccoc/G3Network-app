"""Minimal WebSocket gateway for OCPP 2.0.1 and OCPP 1.6J.

The gateway only validates the path, subprotocol, and identity of a
pre-provisioned station, then keeps one stable connection within the
process. The negotiated subprotocol (``ocpp2.0.1`` or ``ocpp1.6``) selects
the adapter class: ``OCPP201ChargePoint`` here, ``OCPP16ChargePoint`` in
``ocpp16_charge_point.py``. Production reliability, reconnect, and technical status history
are outside the active path. Every frame exchanged on a connection is
stored verbatim by ``RecordingConnection`` (see ``raw_log.py``) before any
parsing.

For the 2.0.1 adapter: ``python-ocpp``'s ``ChargePoint._handle_call`` only snake_cases inbound
JSON keys and splats the result as handler kwargs - it never constructs
the ``ocpp.v201.datatypes`` dataclasses. So nested OCPP objects (``evse``,
``transactionInfo``, ``meterValue`` and its ``sampledValue`` entries)
always arrive as plain ``dict``s at runtime, never as those dataclasses,
regardless of a handler's type annotation. Handlers here are written
against that reality (aliased ``OcppPayload``); don't re-introduce
dataclass type hints for nested OCPP objects.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Final
from urllib.parse import unquote, urlsplit
from uuid import UUID

from ocpp.routing import on
from ocpp.v201 import ChargePoint, call_result
from ocpp.v201.enums import Action, ConnectorStatusEnumType, TransactionEventEnumType
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
from app.domains.charging_stations.ocpp.ocpp16_charge_point import OCPP16ChargePoint
from app.domains.charging_stations.ocpp.parsing import (
    OcppPayload,
    parse_ocpp_timestamp,
)
from app.domains.charging_stations.ocpp.raw_log import RecordingConnection
from app.domains.charging_stations.types import ChargingConnectorStatus
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

# OCPP 2.0.1: a SampledValue with no measurand is a cumulative active-import
# energy register, which is the only measurand meter_start_wh/meter_end_wh
# can be computed from (F-B2). Any other measurand (power, SoC,
# temperature, an *interval* energy delta) is data this MVP doesn't model.
_ENERGY_REGISTER_MEASURAND: Final[str] = "Energy.Active.Import.Register"
# Wh is OCPP's default unit; kWh is the only other energy unit in the 2.0.1
# standardized list. Keys are lowercased for a case-insensitive match,
# since unitOfMeasure.unit is a free string, not an enum, in the spec.
_ENERGY_UNIT_FACTORS_WH: Final[dict[str, Decimal]] = {
    "wh": Decimal(1),
    "kwh": Decimal(1000),
}


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


def normalize_sampled_value_to_wh(sampled_value: OcppPayload) -> Decimal | None:
    """Convert one OCPP sampled value into a canonical Wh energy reading (F-B2).

    Rule:
        Only ``Energy.Active.Import.Register`` is a cumulative import
        register, and therefore the only measurand the session aggregate
        can use ``meter_start_wh``/``meter_end_wh`` from; anything else
        (power, SoC, temperature, an *interval* energy delta) is extra
        telemetry this MVP doesn't model and is skipped, not an error. An
        absent ``measurand`` means that register, per OCPP 2.0.1's own
        default. The reading is ``value * 10 ** multiplier`` in ``unit``,
        with ``unit`` defaulting to Wh and ``multiplier`` to 0 - the OCPP
        JSON Schema declares those defaults but validation doesn't inject
        them, so both are defaulted here.

    Args:
        sampled_value: One ``sampledValue`` object, snake_cased by
            ``python-ocpp`` into a plain dict - see the module docstring.

    Returns:
        The reading in Wh, or ``None`` if the measurand is not the import
        energy register (skip this sample, not an error).

    Raises:
        ValueError: If the measurand is the import energy register but its
            unit is neither Wh nor kWh - an uninterpretable register
            reading must fail loudly rather than be dropped, which would
            leave the aggregate stale while the station believes it
            reported successfully.
    """
    measurand = sampled_value.get("measurand") or _ENERGY_REGISTER_MEASURAND
    if measurand != _ENERGY_REGISTER_MEASURAND:
        return None

    unit_of_measure = sampled_value.get("unit_of_measure") or {}
    unit = str(unit_of_measure.get("unit") or "Wh").lower()
    multiplier = unit_of_measure.get("multiplier") or 0
    factor = _ENERGY_UNIT_FACTORS_WH.get(unit)
    if factor is None:
        raise ValueError(f"Unrecognized energy unit '{unit}' for {measurand}")

    raw_value = Decimal(str(sampled_value["value"]))
    return raw_value.scaleb(multiplier) * factor


def extract_meter_samples(
    meter_values: list[OcppPayload],
) -> list[MeterSampleInput]:
    """Convert the energy samples in an OCPP message into persistence input.

    Args:
        meter_values: Groups of samples, snake_cased by ``python-ocpp``
            into plain dicts - see the module docstring.

    Returns:
        Canonical Wh samples in payload order. Shorter than the payload,
        or empty, when the message carries non-energy measurands
        (F-B2) - see ``normalize_sampled_value_to_wh``.

    Raises:
        ValueError: If a timestamp lacks a timezone, or an energy-register
            sample uses an uninterpretable unit.
    """
    samples: list[MeterSampleInput] = []
    for meter_value in meter_values:
        sampled_at = parse_ocpp_timestamp(meter_value["timestamp"])
        for sampled_value in meter_value["sampled_value"]:
            value_wh = normalize_sampled_value_to_wh(sampled_value)
            if value_wh is None:
                continue
            samples.append(MeterSampleInput(sampled_at=sampled_at, value_wh=value_wh))
    return samples


def parse_ocpp_transaction_id(transaction_info: OcppPayload) -> str:
    """Read the transaction identity out of a raw TransactionEvent payload.

    Args:
        transaction_info: The ``transactionInfo`` object, snake_cased by
            ``python-ocpp`` into a plain dict - see the module docstring.

    Returns:
        The OCPP transaction identity.

    Raises:
        ValueError: If the payload has no ``transaction_id``.
    """
    transaction_id = transaction_info.get("transaction_id")
    if not transaction_id:
        raise ValueError("transactionInfo must have a transactionId")
    return str(transaction_id)


def parse_ocpp_evse_reference(evse: OcppPayload | None) -> tuple[int, int]:
    """Read the OCPP EVSE and connector IDs out of a raw ``evse`` payload.

    Args:
        evse: The ``evse`` object, snake_cased by ``python-ocpp`` into a
            plain dict - see the module docstring.

    Returns:
        A ``(ocpp_evse_id, ocpp_connector_id)`` pair.

    Raises:
        ValueError: If the payload is missing the EVSE or the connector.
    """
    if evse is None or evse.get("id") is None or evse.get("connector_id") is None:
        raise ValueError("TransactionEvent must have an EVSE and connector")
    return int(evse["id"]), int(evse["connector_id"])


async def resolve_ocpp_topology(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    evse: OcppPayload | None,
) -> tuple[UUID, UUID, UUID]:
    """Resolve the OCPP EVSE/connector identity into UUID primitives.

    Args:
        db: Async session of the action transaction.
        ocpp_identity: Identity of the OCPP station.
        evse: EVSE object from the OCPP payload, as a plain dict.

    Returns:
        Tuple of internal IDs for the station, EVSE, and connector.

    Raises:
        ValueError: If the payload is missing the EVSE or connector.
    """
    ocpp_evse_id, ocpp_connector_id = parse_ocpp_evse_reference(evse)
    return await charging_stations_service.resolve_ocpp_topology(
        db,
        ocpp_identity=ocpp_identity,
        ocpp_evse_id=ocpp_evse_id,
        ocpp_connector_id=ocpp_connector_id,
    )


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


class OCPP201ChargePoint(ChargePoint):  # type: ignore[misc]
    """``python-ocpp`` OCPP 2.0.1 adapter attaching an accepted WebSocket to a station identity.

    Attributes:
        id: Station identity used by ``ChargePoint`` when dispatching OCPP.
        connection: Recording wrapper around the WebSocket connection created
            by ``websockets`` after the handshake; ``ChargePoint`` only calls
            its ``recv()``/``send()``.
        session_factory: Shared factory used for each persistence operation.
        _session_by_evse: Mapping from OCPP EVSE ID to session UUID, for
            MeterValues.

    Note:
        The class receives OCPP actions after the handshake, converts the
        payload into primitive values, and does not create the WebSocket
        connection itself.
    """

    def __init__(
        self,
        identity: str,
        connection: RecordingConnection,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Initialize the OCPP 2.0.1 adapter for an already validated connection.

        Args:
            identity: OCPP identity already resolved in the database.
            connection: Recording wrapper around the WebSocket connection that
                completed the handshake.
            session_factory: Shared factory owning the transaction for the
                action handler.

        Side Effects:
            Initializes the ``python-ocpp`` base class state and attaches
            the gateway logger.
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
        transaction_info: OcppPayload,
        meter_value: list[OcppPayload] | None = None,
        evse: OcppPayload | None = None,
        **_: object,
    ) -> call_result.TransactionEvent:
        """Persist a TransactionEvent using primitive values, then ACK the OCPP call.

        Args:
            event_type: ``Started``, ``Updated``, or ``Ended``.
            timestamp: Time of the event per OCPP.
            trigger_reason: OCPP trigger, currently only parsed to preserve
                the contract.
            seq_no: OCPP sequence number, persisted on the event history
                row (F-B2) but not yet used for dedup/ordering.
            transaction_info: The ``transactionInfo`` object, as a plain
                dict - see the module docstring.
            meter_value: Start/end meter values depending on the event, as
                plain dicts - see the module docstring.
            evse: OCPP EVSE and connector to resolve, as a plain dict.
            **_: Optional OCPP fields not part of the MVP.

        Returns:
            A valid empty response for TransactionEvent.

        Side Effects:
            Calls the public ``charging_sessions`` service within an atomic
            transaction; the EVSE -> session mapping is only updated after
            the transaction commits.
        """
        del trigger_reason
        event_map = {
            TransactionEventEnumType.started: SessionEventType.STARTED,
            TransactionEventEnumType.updated: SessionEventType.UPDATED,
            TransactionEventEnumType.ended: SessionEventType.ENDED,
        }
        session_event = event_map[event_type]
        samples = extract_meter_samples(meter_value) if meter_value else []
        meter_start_wh = samples[0].value_wh if samples else None
        meter_end_wh = samples[-1].value_wh if samples else None
        meter_end_sampled_at = samples[-1].sampled_at if samples else None
        transaction_id = parse_ocpp_transaction_id(transaction_info)
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
                seq_no=seq_no,
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
                meter_end_sampled_at=(
                    meter_end_sampled_at
                    if session_event is not SessionEventType.STARTED
                    else None
                ),
            )
        if session_event is SessionEventType.ENDED:
            if evse is not None:
                self._session_by_evse.pop(evse["id"], None)
        else:
            if evse is not None:
                self._session_by_evse[evse["id"]] = result.session_id
        return call_result.TransactionEvent()

    @on(Action.meter_values)  # type: ignore[untyped-decorator]
    async def on_meter_values(
        self,
        evse_id: int,
        meter_value: list[OcppPayload],
        **_: object,
    ) -> call_result.MeterValues:
        """Persist each MeterValues energy sample within one transaction.

        Args:
            evse_id: OCPP EVSE ID used to look up the session on this
                connection.
            meter_value: Groups of samples, as plain dicts - see the
                module docstring - to convert to Wh.
            **_: Optional OCPP fields not part of the MVP.

        Returns:
            A valid empty response for MeterValues.

        Side Effects:
            Calls ``ingest_meter_values`` for each sample on the same
            AsyncSession; an exception rolls back the entire message's
            entry transaction.
        """
        # Populated by on_transaction_event on this same connection and
        # popped on Ended. A missing key means MeterValues outside a
        # transaction or after a reconnect dropped the mapping; the
        # resulting KeyError -> CALLERROR is the intended MVP failure, and
        # surviving a reconnect is the reliability path (future.md item 27).
        session_id = self._session_by_evse[evse_id]
        samples = extract_meter_samples(meter_value)
        async with self.session_factory.begin() as db:
            for sample in samples:
                await charging_sessions_service.ingest_meter_values(
                    db, session_id=session_id, sample=sample
                )
        return call_result.MeterValues()

    @on(Action.status_notification)  # type: ignore[untyped-decorator]
    async def on_status_notification(
        self,
        timestamp: str,
        connector_status: ConnectorStatusEnumType,
        evse_id: int,
        connector_id: int,
        **_: object,
    ) -> call_result.StatusNotification:
        """Record a connector's live status, then ACK the OCPP call (F-C2).

        Args:
            timestamp: Time of the status change per OCPP.
            connector_status: OCPP status label. Arrives as a plain ``str``
                at runtime despite the type annotation - ``python-ocpp``
                passes the parsed JSON straight through with no coercion.
            evse_id: OCPP EVSE ID owning the connector.
            connector_id: OCPP connector ID within the EVSE.
            **_: Optional OCPP fields not part of the MVP.

        Returns:
            A valid empty response for StatusNotification.

        Raises:
            ChargingStationNotFoundError: If ``self.id`` is not
                pre-provisioned.
            ChargingEvseNotFoundError: If ``evse_id`` is not pre-provisioned
                under this station.
            ChargingConnectorNotFoundError: If ``connector_id`` is not
                pre-provisioned under that EVSE.
            ValueError: If ``connector_status`` isn't one of OCPP 2.0.1's
                five status labels.

        Side Effects:
            Calls the public ``charging_stations`` service within an atomic
            transaction; rolls back entirely on any of the above. No
            try/except here - ``python-ocpp`` already wraps every handler
            invocation, logs the traceback, and replies with a
            ``CALLERROR(InternalError)`` without closing the connection,
            the same de-facto contract ``on_transaction_event`` and
            ``on_meter_values`` already rely on. Standardizing this instead
            of relying on the framework default is deferred (see
            ``docs/01-requirements/future.md`` item 31).
        """
        async with self.session_factory.begin() as db:
            _station_id, _evse_id, connector_uuid = (
                await charging_stations_service.resolve_ocpp_topology(
                    db,
                    ocpp_identity=self.id,
                    ocpp_evse_id=evse_id,
                    ocpp_connector_id=connector_id,
                )
            )
            await charging_stations_service.update_connector_status(
                db,
                connector_id=connector_uuid,
                status=ChargingConnectorStatus(connector_status),
                status_updated_at=parse_ocpp_timestamp(timestamp),
            )
        return call_result.StatusNotification()


class OCPPServer:
    """Lifecycle and handshake policy of the OCPP WebSocket gateway.

    Attributes:
        host: Bind address taken from charging settings.
        port: Bind port taken from charging settings.
        session_factory: Shared async session factory used to resolve
            stations.
        _server: WebSocket server after it has started successfully.
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
        self._server: Server | None = None

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
        logger.info(
            "OCPP gateway started",
            extra={"host": self.host, "port": self.port},
        )

    async def stop(self) -> None:
        """Stop the listener and release the socket.

        Side Effects:
            Closes the current listener and waits for the socket to be
            released. Safe to call when the listener has not been started.
        """
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
            pre-provisioned.
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
        """Run the lifecycle of one WebSocket after a successful handshake.

        Args:
            connection: WebSocket connection accepted by websockets.

        Side Effects:
            Creates the adapter for the negotiated subprotocol
            (``create_charge_point``) over a ``RecordingConnection`` and waits for ``python-ocpp`` to read
            messages until the station disconnects or the handler is
            cancelled. Every frame is stored verbatim in its own transaction.
            Handler errors (including a failure to store a frame) are
            logged; ``CancelledError`` is re-raised so shutdown keeps working.
            If the station was soft-deleted between the handshake and this
            point, the connection is closed with code 1008.
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
            station = await repository.get_station_by_identity(
                db, identity, include_deleted=False
            )
        if station is None:
            # Only reachable if the station was deleted right after the
            # handshake validation; nothing can be logged without a station.
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
