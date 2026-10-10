"""Gateway side of the command channel: send queued commands to connected chargers.

The API (or another domain) queues a row in ``charging_station_commands``
(``PENDING``, no ``ocpp_message_id``; PR-16, CS-20). This module runs inside the
OCPP gateway process: a loop polls the table for queued commands of the chargers
connected to *this* process, claims them, sends each as that charger's OCPP call
and writes the answer back. There is no broker and no shared socket.

Rules:

* A claimed command gets its frame's message ID and the claim is committed
  **before** anything is sent, so a command is never sent twice, even if the
  process dies afterwards; a sent command nobody answered ends as ``TIMEOUT``
  (swept after a while), and a queued one no connected gateway picked up ends as
  ``NOT_SENT`` (the charger was not connected).
* Every command runs in its own task; the loop never awaits a charger's answer
  and no OCPP handler ever awaits a call (the library's receive loop is
  sequential, so awaiting there would deadlock until the timeout; see
  ``deferred.md`` 74). The adapter's call lock keeps one request in flight per
  charger.
* Each step uses its own short transaction; the answer to a command is written
  after the call returned, never inside a transaction held across it.
* A ``REMOTE_START`` that ends ``REJECTED``, ``ERROR``, ``TIMEOUT`` or
  ``NOT_SENT`` abandons its PENDING session in the same step (CE-10), and the
  loop also sweeps the PENDING sessions whose scan outlived
  ``CHARGING_PENDING_SESSION_TIMEOUT_SECONDS`` (no separate process).
* One gateway process is assumed: ``NOT_SENT`` is decided by the age of a queued
  command, so a second gateway holding the charger would race it (Known issues
  in the refactor plan).
"""

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any, Final
from uuid import UUID

from ocpp.exceptions import OCPPError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.exceptions import ConnectionClosed

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.ocpp_state_service as ocpp_state_service
import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_sessions.exceptions import ChargingSessionNotFoundError
from app.domains.charging_stations.exceptions import ChargingEvseNotFoundError
from app.domains.charging_stations.models import ChargingStationCommandModel
from app.domains.charging_stations.ocpp.command_types import (
    RESPONSE_STATUS_MAX_LENGTH,
    CommandSender,
    OutboundCommand,
)
from app.domains.charging_stations.types import (
    StationCommandOutcome,
    StationCommandType,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

# How many queued commands one poll claims at most.
_CLAIM_BATCH_SIZE: Final[int] = 20
# A sent command nobody answered is closed as TIMEOUT after this many request
# timeouts (the gateway restarted between the send and the answer).
_STALE_SENT_FACTOR: Final[int] = 3


# Outcomes after which a remote start can no longer begin its session (CE-10).
_FAILED_START_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        StationCommandOutcome.REJECTED.value,
        StationCommandOutcome.ERROR.value,
        StationCommandOutcome.TIMEOUT.value,
        StationCommandOutcome.NOT_SENT.value,
    }
)


async def abandon_session_of_failed_start(
    db: AsyncSession, command_id: UUID, outcome: StationCommandOutcome
) -> None:
    """Abandon the PENDING session of a remote start that did not go through.

    For a ``REMOTE_START`` with a session and a failed outcome, turns that
    session ``ABANDONED``. Any other command, a session-less start or a start
    that was accepted changes nothing; a session that is no longer PENDING is
    left alone. Why the session was abandoned stays on the command row (CS-20).

    Args:
        db: Async session owned by the caller's transaction.
        command_id: The command that just ended.
        outcome: The outcome just written for it. Passed in rather than read
            back, because the caller wrote it with an UPDATE statement that a
            row already loaded in this session would not reflect.

    Side Effects:
        May update one session row in the caller's transaction.
    """
    if outcome.value not in _FAILED_START_OUTCOMES:
        return
    command = await ocpp_state_repository.get_station_command_by_id(db, command_id)
    if (
        command is not None
        and command.command_type == StationCommandType.REMOTE_START.value
        and command.session_id is not None
    ):
        try:
            reference = (
                await charging_sessions_service.resolve_session_command_reference(
                    db, command.session_id
                )
            )
        except ChargingSessionNotFoundError:
            return
        if reference.station_id != command.station_id:
            # A start that named another charger's session must not end it
            # (RV-CS8).
            logger.warning(
                "Failed start names a session of another charger, not abandoned",
                extra={"command_id": str(command_id)},
            )
            return
        await charging_sessions_service.abandon_pending_session(db, command.session_id)


class StationConnectionRegistry:
    """The chargers connected to this gateway process, by station ID.

    Attributes:
        _senders: Adapter of each connected charger; one per station, a newer
            connection of the same charger replaces the older one.
    """

    def __init__(self) -> None:
        """Create an empty registry."""
        self._senders: dict[UUID, CommandSender] = {}

    def register(self, station_id: UUID, sender: CommandSender) -> None:
        """Record a charger's connection (replacing an older one).

        Args:
            station_id: The charger.
            sender: Its adapter.
        """
        self._senders[station_id] = sender

    def unregister(self, station_id: UUID, sender: CommandSender) -> None:
        """Forget a charger's connection, unless a newer one replaced it.

        Args:
            station_id: The charger.
            sender: The adapter of the connection that closed.
        """
        if self._senders.get(station_id) is sender:
            del self._senders[station_id]

    def get(self, station_id: UUID) -> CommandSender | None:
        """Find the adapter of a connected charger.

        Args:
            station_id: The charger.

        Returns:
            Its adapter, or ``None`` if it is not connected here.
        """
        return self._senders.get(station_id)

    def connected_station_ids(self) -> list[UUID]:
        """List the chargers connected to this process.

        Returns:
            Their station IDs.
        """
        return list(self._senders)


async def _build_outbound_command(
    db: AsyncSession, command: ChargingStationCommandModel
) -> OutboundCommand:
    """Turn a claimed command row into the primitives an adapter needs.

    Args:
        db: Async session owned by the caller's transaction.
        command: The claimed command (it has its message ID).

    Returns:
        The outbound command: the OCPP numbers of the targeted gun, and for a
        remote start / stop the session's token / transaction ID.

    Raises:
        ChargingSessionNotFoundError: If the command's session is missing.
        ChargingEvseNotFoundError: If the targeted EVSE no longer exists:
            the command is failed, never widened to the whole charger
            (RV-CS10).
    """
    assert command.ocpp_message_id is not None, "a claimed command has a message ID"
    parameters: dict[str, Any] = dict(command.parameters or {})
    ocpp_evse_id: int | None = None
    ocpp_connector_id: int | None = None
    if command.evse_id is not None:
        evse = await charging_stations_repository.get_evse_by_id(db, command.evse_id)
        if evse is None:
            raise ChargingEvseNotFoundError(
                "The EVSE the command targets no longer exists"
            )
        ocpp_evse_id = evse.ocpp_evse_id
        connectors = await charging_stations_repository.list_charging_connectors(
            db, evse_id=evse.evse_id, offset=0, limit=1
        )
        if connectors:
            ocpp_connector_id = connectors[0].ocpp_connector_id
    id_token: str | None = (
        str(parameters["id_token"]) if "id_token" in parameters else None
    )
    ocpp_transaction_id: str | None = None
    if command.session_id is not None:
        reference = await charging_sessions_service.resolve_session_command_reference(
            db, command.session_id
        )
        id_token = reference.id_token or id_token
        ocpp_transaction_id = reference.ocpp_transaction_id
    return OutboundCommand(
        command_id=command.command_id,
        command_type=StationCommandType(command.command_type),
        ocpp_message_id=command.ocpp_message_id,
        ocpp_evse_id=ocpp_evse_id,
        ocpp_connector_id=ocpp_connector_id,
        session_id=command.session_id,
        id_token=id_token,
        ocpp_transaction_id=ocpp_transaction_id,
        parameters=parameters,
    )


async def _record_failure(
    session_factory: async_sessionmaker[AsyncSession],
    command_id: UUID,
    outcome: StationCommandOutcome,
    response_status: str | None,
) -> None:
    """Write a command that got no usable answer.

    Args:
        session_factory: Shared factory; one short transaction is used.
        command_id: The command.
        outcome: ``ERROR`` or ``TIMEOUT``.
        response_status: What to keep as the answer, ``None`` for none.
    """
    async with session_factory.begin() as db:
        await ocpp_state_service.fail_command(
            db,
            command_id=command_id,
            outcome=outcome,
            response_status=(
                None
                if response_status is None
                else response_status[:RESPONSE_STATUS_MAX_LENGTH]
            ),
            answered_at=utc_now(),
        )
        await abandon_session_of_failed_start(db, command_id, outcome)


async def run_command(
    session_factory: async_sessionmaker[AsyncSession],
    sender: CommandSender,
    command_id: UUID,
) -> None:
    """Send one claimed command and write the answer back.

    This is a task boundary (nothing awaits it): every failure is turned into
    an outcome on the row, and an unexpected one is logged with its traceback
    (``logger.exception``). ``CancelledError`` is never swallowed.

    Args:
        session_factory: Shared factory; each step uses its own transaction.
        sender: The adapter of the charger the command goes to.
        command_id: The claimed command.

    Side Effects:
        Sends one OCPP call; writes ``outcome`` / ``response_status`` /
        ``answered_at`` on the command row (``ERROR`` for an OCPP error or a
        closed connection, ``TIMEOUT`` for no answer).
    """
    try:
        async with session_factory.begin() as db:
            command = await ocpp_state_repository.get_station_command_by_id(
                db, command_id
            )
            if command is None:
                return
            if command.outcome != StationCommandOutcome.PENDING.value:
                # Something closed the command while it waited its turn (the
                # sweep timed it out, or it was cancelled): it is not sent
                # now, and nothing is written over its result (RV-CS6).
                return
            try:
                outbound = await _build_outbound_command(db, command)
            except ChargingEvseNotFoundError:
                is_evse_missing = True
            else:
                is_evse_missing = False
        if is_evse_missing:
            await _record_failure(
                session_factory,
                command_id,
                StationCommandOutcome.ERROR,
                "EvseNotFound",
            )
            return
        try:
            result = await sender.send_command(outbound)
        except TimeoutError:
            await _record_failure(
                session_factory, command_id, StationCommandOutcome.TIMEOUT, None
            )
            return
        except OCPPError as error:
            await _record_failure(
                session_factory,
                command_id,
                StationCommandOutcome.ERROR,
                getattr(error, "code", None) or type(error).__name__,
            )
            return
        except ConnectionClosed:
            await _record_failure(
                session_factory,
                command_id,
                StationCommandOutcome.ERROR,
                "ConnectionClosed",
            )
            return
        if result.handled:
            return
        async with session_factory.begin() as db:
            await ocpp_state_repository.set_command_answer(
                db,
                command_id,
                outcome=result.outcome,
                response_status=result.response_status,
                answered_at=utc_now(),
            )
            await abandon_session_of_failed_start(db, command_id, result.outcome)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "Station command failed", extra={"command_id": str(command_id)}
        )
        try:
            await _record_failure(
                session_factory,
                command_id,
                StationCommandOutcome.ERROR,
                "InternalError",
            )
        except Exception:
            logger.exception(
                "Could not record the failure of a station command",
                extra={"command_id": str(command_id)},
            )


async def process_queued_commands(
    registry: StationConnectionRegistry,
    session_factory: async_sessionmaker[AsyncSession],
    tasks: set[asyncio.Task[None]],
) -> int:
    """Run one poll of the command loop.

    Claims the queued commands of the connected chargers (committing the claim
    first), schedules a task per command, and closes the commands that nobody
    picked up in time (``NOT_SENT``) or that were sent and never answered
    (``TIMEOUT``).

    Args:
        registry: The chargers connected to this process.
        session_factory: Shared factory; each step uses its own transaction.
        tasks: Running command tasks; new ones are added and remove themselves
            when done, so the caller can cancel them at shutdown.

    Returns:
        The number of commands claimed in this poll.
    """
    async with session_factory.begin() as db:
        claimed = await ocpp_state_repository.claim_queued_commands(
            db, registry.connected_station_ids(), limit=_CLAIM_BATCH_SIZE
        )
        claimed_by_station = [
            (command.command_id, command.station_id) for command in claimed
        ]
        now = utc_now()
        not_sent_ids = await ocpp_state_repository.mark_unsent_commands_not_sent(
            db,
            requested_before=now
            - timedelta(seconds=settings.CHARGING_OCPP_COMMAND_PICKUP_TIMEOUT_SECONDS),
        )
        timed_out_ids = await ocpp_state_repository.mark_stale_commands_timed_out(
            db,
            claimed_before=now
            - timedelta(
                seconds=settings.CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS
                * _STALE_SENT_FACTOR
            ),
        )
        for closed_command_id in not_sent_ids:
            await abandon_session_of_failed_start(
                db, closed_command_id, StationCommandOutcome.NOT_SENT
            )
        for closed_command_id in timed_out_ids:
            await abandon_session_of_failed_start(
                db, closed_command_id, StationCommandOutcome.TIMEOUT
            )
    for command_id, station_id in claimed_by_station:
        sender = registry.get(station_id)
        if sender is None:
            await _record_failure(
                session_factory,
                command_id,
                StationCommandOutcome.ERROR,
                "NotConnected",
            )
            continue
        task = asyncio.create_task(run_command(session_factory, sender, command_id))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    return len(claimed_by_station)


async def sweep_expired_pending_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Abandon the PENDING sessions whose scan outlived the pending window (CE-10).

    The simplest home for the sweep is the gateway's own loop: it already runs
    one short transaction per step and is the process that knows whether a
    charger answered, so QR charging needs no extra process.

    Args:
        session_factory: Shared factory; one short transaction is used.

    Returns:
        The number of sessions abandoned.
    """
    async with session_factory.begin() as db:
        abandoned_count = (
            await charging_sessions_service.abandon_expired_pending_sessions(db)
        )
    if abandoned_count:
        logger.info(
            "Abandoned expired pending sessions",
            extra={"abandoned_count": abandoned_count},
        )
    return abandoned_count


async def run_command_loop(
    registry: StationConnectionRegistry,
    session_factory: async_sessionmaker[AsyncSession],
    stop_event: asyncio.Event,
) -> None:
    """Poll the command table until the stop event is set.

    This is a process boundary: a failing poll (for example a database blip) is
    logged with its traceback and the loop keeps running; ``CancelledError`` is
    never swallowed. On stop it cancels the commands still in flight.

    Args:
        registry: The chargers connected to this process.
        session_factory: Shared factory.
        stop_event: Set by the gateway when it stops.

    Side Effects:
        Reads and writes ``charging_station_commands``; sends OCPP calls;
        every ``CHARGING_SESSION_SWEEP_INTERVAL_SECONDS`` abandons the expired
        PENDING sessions.
    """
    tasks: set[asyncio.Task[None]] = set()
    last_sweep = float("-inf")
    try:
        while not stop_event.is_set():
            try:
                await process_queued_commands(registry, session_factory, tasks)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Station command poll failed")
            if (
                time.monotonic() - last_sweep
                >= settings.CHARGING_SESSION_SWEEP_INTERVAL_SECONDS
            ):
                last_sweep = time.monotonic()
                try:
                    await sweep_expired_pending_sessions(session_factory)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Pending session sweep failed")
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.CHARGING_OCPP_COMMAND_POLL_SECONDS,
                )
            except TimeoutError:
                continue
    finally:
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
