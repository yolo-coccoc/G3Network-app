"""Connection wrapper that stores every OCPP frame verbatim (both directions).

The gateway hands a ``RecordingConnection`` to ``python-ocpp`` instead of the
raw WebSocket connection. ``ChargePoint`` only ever calls ``recv()`` and
``send()`` on its connection, so the wrapper sees every frame, including ones
the library cannot parse and actions the gateway has no handler for. That is
the point: the raw log must contain exactly what the charger sent, before any
parsing, so a real charger's deviations from the OCPP standard stay visible.

Each frame is written in the wrapper's **own** transaction, separate from the
handler's, so a handler rolling back never erases the record of what arrived.
A failure to persist a frame is not swallowed: it propagates out of
``recv()``/``send()`` and ends the connection handler, because evidence that
may silently go missing is not evidence (operational handling of a database
outage is ``docs/01-requirements/future.md`` item 31).

Scope: this module only records frames. It never inspects, filters, or
rewrites them, and it exposes no read API (the table is queried with SQL).
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.server import ServerConnection
from websockets.typing import Data

import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
from app.domains.charging_stations.types import OcppMessageDirection


class RecordingConnection:
    """Wrap an accepted WebSocket so every frame is logged verbatim.

    Attributes:
        _connection: The underlying WebSocket connection, used unchanged for
            the actual I/O.
        _station_id: UUID of the station this connection belongs to.
        _ocpp_subprotocol: Subprotocol negotiated for this connection.
        _session_factory: Shared factory; each logged frame uses its own
            short transaction.

    Note:
        Only ``recv()`` and ``send()`` are provided because those are the only
        connection methods ``python-ocpp`` calls; do not add pass-through
        methods speculatively.
    """

    def __init__(
        self,
        connection: ServerConnection,
        *,
        station_id: UUID,
        ocpp_subprotocol: str,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Initialize the wrapper for one already-accepted connection.

        Args:
            connection: WebSocket connection that completed the handshake.
            station_id: UUID of the pre-provisioned station.
            ocpp_subprotocol: Negotiated subprotocol label stored with each
                frame.
            session_factory: Shared async session factory.
        """
        self._connection = connection
        self._station_id = station_id
        self._ocpp_subprotocol = ocpp_subprotocol
        self._session_factory = session_factory

    async def recv(self) -> Data:
        """Receive one frame, store it, and only then hand it to the library.

        Returns:
            The frame exactly as the WebSocket delivered it.

        Raises:
            websockets.exceptions.ConnectionClosed: If the connection ended;
                nothing is recorded in that case.
            Exception: Any failure to persist the frame; the frame is then
                not passed on for processing.

        Side Effects:
            Commits one log row in a separate transaction before returning,
            so the frame is recorded even if processing it later fails.
        """
        frame = await self._connection.recv()
        # Stamp the time before the database round trip so ``occurred_at`` is
        # the receive time, not the time the write finished.
        received_at = datetime.now(timezone.utc)
        await self._record(OcppMessageDirection.CP_TO_CSMS, frame, received_at)
        return frame

    async def send(self, message: Data) -> None:
        """Send one frame, then record it.

        Args:
            message: The frame the library wants to send.

        Raises:
            websockets.exceptions.ConnectionClosed: If the send failed; a
                frame that was not sent is not recorded.
            Exception: Any failure to persist the frame.

        Side Effects:
            Commits one log row in a separate transaction after the frame was
            handed to the WebSocket.
        """
        await self._connection.send(message)
        await self._record(
            OcppMessageDirection.CSMS_TO_CP, message, datetime.now(timezone.utc)
        )

    async def _record(
        self, direction: OcppMessageDirection, frame: Data, occurred_at: datetime
    ) -> None:
        """Persist one frame in its own transaction.

        Args:
            direction: Whether the frame was inbound or outbound.
            frame: The frame as delivered/sent. OCPP-J frames are text; a
                binary frame is not valid OCPP-J, so its bytes are decoded
                (with replacement characters) only for the stored copy while
                the original object is still what the caller receives.
            occurred_at: Timezone-aware receive/send time.
        """
        raw_frame = (
            frame if isinstance(frame, str) else frame.decode("utf-8", errors="replace")
        )
        async with self._session_factory.begin() as db:
            await ocpp_state_service.record_ocpp_message(
                db,
                station_id=self._station_id,
                occurred_at=occurred_at,
                ocpp_subprotocol=self._ocpp_subprotocol,
                direction=direction,
                raw_frame=raw_frame,
            )
