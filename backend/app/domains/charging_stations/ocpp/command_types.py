"""Data passed between the gateway's command loop and the protocol adapters (CS-20).

The loop (``command_loop.py``) claims queued ``charging_station_commands`` rows,
turns each into an ``OutboundCommand`` carrying only primitives (the OCPP
numbers of the gun, the session's token) and hands it to the adapter of the
charger's protocol; the adapter turns it into that version's OCPP call and
returns a ``CommandResult``. Keeping these two small types apart from both
adapters lets the loop stay protocol-neutral (CO-15).
"""

from dataclasses import dataclass, field
from typing import Any, Final, Protocol
from uuid import UUID

from app.domains.charging_stations.types import (
    StationCommandOutcome,
    StationCommandType,
)

# Answer statuses (1.6J and 2.0.1) that mean the charger will do what we asked,
# possibly later (``Scheduled``) or after a restart (``RebootRequired``).
_ACCEPTED_STATUSES: Final[frozenset[str]] = frozenset(
    {"Accepted", "Scheduled", "RebootRequired", "Unlocked"}
)
# Width of ``charging_station_commands.response_status``.
RESPONSE_STATUS_MAX_LENGTH: Final[int] = 30


@dataclass(frozen=True, slots=True)
class OutboundCommand:
    """One claimed command, ready to be sent by an adapter.

    Attributes:
        command_id: The command row.
        command_type: What to ask.
        ocpp_message_id: Message ID the frame must carry (set when the command
            was claimed), so the frame and the answer pair with the row.
        ocpp_evse_id: OCPP number of the targeted EVSE, ``None`` for the whole
            charger (for a 1.6J charger it is the gun number, CS-03).
        ocpp_connector_id: OCPP number of the targeted connector within the
            EVSE, ``None`` when the command has no EVSE.
        session_id: The session a remote start begins or a remote stop ends.
        id_token: Token the session carries, for a remote start; never log it.
        ocpp_transaction_id: The charger's transaction ID of that session, for
            a remote stop.
        parameters: The command's stored parameters.
    """

    command_id: UUID
    command_type: StationCommandType
    ocpp_message_id: str
    ocpp_evse_id: int | None = None
    ocpp_connector_id: int | None = None
    session_id: UUID | None = None
    id_token: str | None = None
    ocpp_transaction_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The charger's answer to a command, in the table's one shape.

    Attributes:
        outcome: ``ACCEPTED`` or ``REJECTED`` (the loop adds ``ERROR`` and
            ``TIMEOUT`` itself when no usable answer arrives).
        response_status: The answer as the charger sent it, e.g. ``Accepted``,
            ``UnlockFailed``, ``RebootRequired``.
        handled: ``True`` when the adapter already wrote the answer to the
            command row (``GET_CONFIGURATION`` completes it together with its
            snapshot), so the loop must not write it again.
    """

    outcome: StationCommandOutcome
    response_status: str | None
    handled: bool = False


class CommandSender(Protocol):
    """What the command loop needs from a connected charger's adapter."""

    protocol_version: str

    async def send_command(self, command: OutboundCommand) -> CommandResult:
        """Send one command as that protocol's OCPP call and wait for the answer.

        Args:
            command: The command to send.

        Returns:
            The charger's verdict.

        Raises:
            TimeoutError: If the charger does not answer in time.
            ocpp.exceptions.OCPPError: If the charger answers with an error.
        """
        ...


def to_command_result(response_status: str | None) -> CommandResult:
    """Map a charger's answer status to the observed outcome.

    Args:
        response_status: The status string of the answer, if any.

    Returns:
        ``ACCEPTED`` for ``Accepted``/``Scheduled``/``RebootRequired``/
        ``Unlocked``, otherwise ``REJECTED``; the status is kept as sent
        (cut to the column width).
    """
    status = None if response_status is None else str(response_status)
    outcome = (
        StationCommandOutcome.ACCEPTED
        if status in _ACCEPTED_STATUSES
        else StationCommandOutcome.REJECTED
    )
    return CommandResult(
        outcome=outcome,
        response_status=None if status is None else status[:RESPONSE_STATUS_MAX_LENGTH],
    )
