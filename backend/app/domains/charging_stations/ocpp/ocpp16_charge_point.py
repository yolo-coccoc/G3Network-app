"""OCPP 1.6J adapter for one connected charge point.

This module holds the adapter class for the ``ocpp1.6`` subprotocol. It is
deliberately separate from the OCPP 2.0.1 adapter: the two protocols have
incompatible payload shapes (1.6J has no EVSE level, no ``TransactionEvent``,
and reports units in a flat ``unit`` field), and mixing them in one class would
hide which contract each handler assumes.

Scope: at this point the adapter has **no handlers**, so every action a 1.6J
charger sends is answered with ``CALLERROR NotImplemented`` by ``python-ocpp``
(the frame is still stored by the raw message log). Handlers are added one
message group at a time by the OCPP 1.6J planner
(``docs/02-planners/backend-ocpp16-charger-integration.md``).
"""

import logging

from ocpp.v16 import ChargePoint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.charging_stations.ocpp.raw_log import RecordingConnection

logger = logging.getLogger(__name__)


class OCPP16ChargePoint(ChargePoint):  # type: ignore[misc]
    """``python-ocpp`` adapter attaching an accepted 1.6J WebSocket to a station.

    Attributes:
        id: Station identity used by ``ChargePoint`` when dispatching OCPP.
        connection: Recording wrapper around the WebSocket connection created
            by ``websockets`` after the handshake; ``ChargePoint`` only calls
            its ``recv()``/``send()``.
        session_factory: Shared factory used for each persistence operation
            (the entry boundary owns every transaction).

    Note:
        The class receives OCPP actions after the handshake and does not
        create the WebSocket connection itself.
    """

    def __init__(
        self,
        identity: str,
        connection: RecordingConnection,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Initialize the OCPP 1.6J adapter for an already validated connection.

        Args:
            identity: OCPP identity already resolved in the database.
            connection: Recording wrapper around the WebSocket connection that
                completed the handshake.
            session_factory: Shared factory owning the transaction for each
                action handler.

        Side Effects:
            Initializes the ``python-ocpp`` base class state and attaches the
            module logger.
        """
        super().__init__(identity, connection, logger=logger)
        self.session_factory = session_factory
