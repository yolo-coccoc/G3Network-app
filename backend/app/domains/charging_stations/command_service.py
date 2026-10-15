"""The command channel to chargers (CS-20, PR-16, STN-10).

Queues a command for the OCPP gateway to send (remote start / stop, reset,
unlock, configuration changes ...): the API path checks the parameters per
type, the operator's reason, the charger being online and no active session
where it matters; commands can be read, listed and cancelled while queued.
Internal module: the domain's router calls it; ``service.py`` wraps
``queue_station_command`` for the QR charge. Runs in the caller's transaction
and never commits.
"""

from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.access as station_access
import app.domains.charging_stations.mappers as station_mappers
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_sessions.types import SessionStatus
from app.domains.charging_stations.exceptions import (
    ChargingStationCommandConflictError,
    ChargingStationCommandInputError,
    ChargingStationNotFoundError,
    ChargingStationOfflineError,
)
from app.domains.charging_stations.models import (
    ChargingStationCommandModel,
)
from app.domains.charging_stations.schemas import (
    COMMAND_PARAMETER_MODELS,
    ChargingStationCommandCreateRequest,
    ChargingStationCommandListResponse,
    ChargingStationCommandResponse,
)
from app.domains.charging_stations.types import (
    StationCommandOutcome,
    StationCommandReference,
    StationCommandType,
)
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window

# --- Commands ----------------------------------------------------------------


# Commands that change the charger or a session need the operator's reason
# (scenario 8: "operator reason required"); reading the settings or asking the
# charger to resend a message changes nothing.
_COMMANDS_NEEDING_REASON = frozenset(
    {
        StationCommandType.REMOTE_START,
        StationCommandType.REMOTE_STOP,
        StationCommandType.UNLOCK_CONNECTOR,
        StationCommandType.RESET,
        StationCommandType.CHANGE_AVAILABILITY,
        StationCommandType.CHANGE_CONFIGURATION,
    }
)


def _validate_command_parameters(
    command_type: StationCommandType,
    *,
    evse_id: UUID | None,
    session_id: UUID | None,
    parameters: dict[str, object] | None,
) -> dict[str, object] | None:
    """Check that a command carries what its type needs and fill the defaults.

    The ``parameters`` are checked against the schema of the type
    (``COMMAND_PARAMETER_MODELS``: allowed keys, value ranges, word lists), then
    the links the type needs (a session, an EVSE) are checked.

    Args:
        command_type: What to ask.
        evse_id: The gun targeted, if any.
        session_id: The session the command is about, if any.
        parameters: The caller's parameters, if any.

    Returns:
        The parameters to store (defaults added), ``None`` if there are none.

    Raises:
        ChargingStationCommandInputError: If a parameter is unknown or out of
            range, or a required link or parameter is missing.
    """
    parameter_model = COMMAND_PARAMETER_MODELS[command_type]
    try:
        checked = parameter_model.model_validate(parameters or {})
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
            for problem in error.errors()
        )
        raise ChargingStationCommandInputError(
            f"Invalid parameters for {command_type.value}: {problems}"
        ) from error
    values: dict[str, object] = dict(checked.model_dump(exclude_none=True))
    if command_type is StationCommandType.REMOTE_START:
        if session_id is None and "id_token" not in values:
            raise ChargingStationCommandInputError(
                "REMOTE_START needs a session (its token) or an id_token parameter"
            )
        # The 2.0.1 remoteStartId is kept on the row (CS-20); a 1.6J charger
        # ignores it.
        values.setdefault("remote_start_id", uuid4().int % 2**31)
    elif command_type is StationCommandType.REMOTE_STOP:
        if session_id is None:
            raise ChargingStationCommandInputError("REMOTE_STOP needs a session")
    elif command_type is StationCommandType.UNLOCK_CONNECTOR:
        if evse_id is None:
            raise ChargingStationCommandInputError("UNLOCK_CONNECTOR needs an EVSE")
    return values or None


async def queue_station_command(
    db: AsyncSession,
    *,
    station_id: UUID,
    command_type: StationCommandType,
    evse_id: UUID | None = None,
    session_id: UUID | None = None,
    parameters: dict[str, object] | None = None,
    requested_by: UUID | None = None,
    reason: str | None = None,
) -> StationCommandReference:
    """Queue a command for a charger: the OCPP gateway sends it (PR-16).

    Public entry point for other domains (a remote start for a QR charge is
    queued by ``charging_sessions``, WP8). The command is written ``PENDING``
    with no message ID; the gateway process holding the charger's connection
    claims it, sends the OCPP call and writes the answer back. A charger that
    is not connected in time ends as ``NOT_SENT``.

    Args:
        db: Async session owned by the caller's entry boundary.
        station_id: The charger the command goes to.
        command_type: What to ask.
        evse_id: The gun it targets, ``None`` for the whole charger.
        session_id: The session a remote start begins or a remote stop ends.
        parameters: What to send besides the links (see
            ``ChargingStationCommandCreateRequest``); validated per type.
        requested_by: User who asked, ``None`` when the system sends it.
        reason: Why, typed by the operator for a manual command.

    Returns:
        A reference to the queued command.

    Raises:
        ChargingStationNotFoundError: If the charger is not active.
        ChargingStationCommandInputError: If a link or parameter the type needs
            is missing, the EVSE is not the charger's, or a referenced row does
            not exist.
    """
    if await charging_stations_repository.get_station_by_id(db, station_id) is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    if evse_id is not None:
        evse = await charging_stations_repository.get_evse_by_id(db, evse_id)
        if evse is None or evse.station_id != station_id:
            raise ChargingStationCommandInputError(
                f"EVSE '{evse_id}' does not belong to the station"
            )
    stored_parameters = _validate_command_parameters(
        command_type,
        evse_id=evse_id,
        session_id=session_id,
        parameters=parameters,
    )
    try:
        command = await ocpp_state_repository.insert_station_command(
            db,
            station_id=station_id,
            command_type=command_type,
            evse_id=evse_id,
            session_id=session_id,
            parameters=stored_parameters,
            requested_by=requested_by,
            reason=reason,
        )
    except IntegrityError as error:
        raise ChargingStationCommandInputError(
            "The session or the requesting user does not exist"
        ) from error
    return StationCommandReference(
        command_id=command.command_id,
        station_id=command.station_id,
        command_type=StationCommandType(command.command_type),
        outcome=StationCommandOutcome(command.outcome),
    )


async def create_charging_station_command(
    db: AsyncSession,
    station_id: UUID,
    command_create_request: ChargingStationCommandCreateRequest,
    *,
    principal: Principal,
) -> ChargingStationCommandResponse:
    """Queue a command for a charger from the API and return it.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: The charger the command goes to.
        command_create_request: The command, already Pydantic-validated.
        principal: The caller, recorded as ``requested_by``; only the owner
            organization and internal staff may command a charger.

    Returns:
        The queued command (``PENDING``, no message ID yet).

    Raises:
        ChargingStationNotFoundError: If the charger is not active or is out
            of the caller's reach.
        ChargingStationCommandInputError: The parameters do not fit the type, a
            link is missing, the session belongs to another charger or has the
            wrong status, or the type needs the operator's reason.
        ChargingStationOfflineError: The charger is not connected now (the
            gateway could only answer ``NOT_SENT``).
        ChargingStationCommandConflictError: An unlock was asked while a charge
            runs on that gun.
    """
    await station_access.get_station_in_reach(db, station_id, principal)
    command_type = command_create_request.command_type
    if command_type in _COMMANDS_NEEDING_REASON and not command_create_request.reason:
        raise ChargingStationCommandInputError(
            f"{command_type.value} needs a reason typed by the operator"
        )
    if command_create_request.session_id is not None:
        # A command may only name a session of this very charger, in the status
        # the command needs: otherwise one tenant's token could be sent to
        # another tenant's charger, or a stranger's session abandoned (RV-CS8).
        session_reference = (
            await charging_sessions_service.resolve_session_command_reference(
                db, command_create_request.session_id
            )
        )
        needed_status = {
            StationCommandType.REMOTE_START: SessionStatus.PENDING,
            StationCommandType.REMOTE_STOP: SessionStatus.ACTIVE,
        }.get(command_type)
        if session_reference.station_id != station_id:
            raise ChargingStationCommandInputError(
                "The session does not belong to this charger"
            )
        if needed_status is not None and session_reference.status != needed_status:
            raise ChargingStationCommandInputError(
                f"{command_type.value} needs a {needed_status.value} session"
            )
    state = await charging_stations_repository.get_station_state(db, station_id)
    if not station_mappers.is_station_online(state, utc_now()):
        raise ChargingStationOfflineError(
            "The charger is not connected, so a command cannot reach it now"
        )
    if (
        command_type is StationCommandType.UNLOCK_CONNECTOR
        and command_create_request.evse_id is not None
    ):
        evse = await charging_stations_repository.get_evse_by_id(
            db, command_create_request.evse_id
        )
        if evse is not None and evse.station_id == station_id:
            for connector_id in await station_access.list_evse_connector_ids(
                db, evse.evse_id
            ):
                if await charging_sessions_service.has_active_session_on_connector(
                    db, connector_id
                ):
                    raise ChargingStationCommandConflictError(
                        "A charge is running on this gun; stop it before unlocking"
                    )
    reference = await queue_station_command(
        db,
        station_id=station_id,
        command_type=command_type,
        evse_id=command_create_request.evse_id,
        session_id=command_create_request.session_id,
        parameters=command_create_request.parameters,
        requested_by=principal.user_id,
        reason=command_create_request.reason,
    )
    command = await ocpp_state_repository.get_station_command_by_id(
        db, reference.command_id
    )
    assert command is not None, "the command was just inserted"
    return to_charging_station_command_response(command)


def to_charging_station_command_response(
    command: ChargingStationCommandModel,
) -> ChargingStationCommandResponse:
    """Build a command response from the ORM model.

    Args:
        command: Command ORM object queried or created by the repository.

    Returns:
        Response schema corresponding to the command.
    """
    return ChargingStationCommandResponse.model_validate(command)


async def get_charging_station_command(
    db: AsyncSession, station_id: UUID, command_id: UUID, *, principal: Principal
) -> ChargingStationCommandResponse:
    """Get one command of a charger (the answer is polled here, PR-16).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: The charger.
        command_id: The command.
        principal: The caller (manage access to the charger).

    Returns:
        The command with its current outcome.

    Raises:
        ChargingStationNotFoundError: If the command does not exist, belongs
            to another charger or the charger is out of the caller's reach.
    """
    await station_access.get_station_in_reach(db, station_id, principal)
    command = await ocpp_state_repository.get_station_command_by_id(db, command_id)
    if command is None or command.station_id != station_id:
        raise ChargingStationNotFoundError(
            f"Command '{command_id}' not found for the station"
        )
    return to_charging_station_command_response(command)


async def cancel_charging_station_command(
    db: AsyncSession, station_id: UUID, command_id: UUID, *, principal: Principal
) -> ChargingStationCommandResponse:
    """Cancel a command that no gateway has picked up yet (STN-10).

    Rule:
        Only a queued command (``PENDING`` with no message ID) can be cancelled;
        it is closed as ``NOT_SENT`` with ``response_status`` "Cancelled" and is
        never sent. One conditional ``UPDATE`` decides, so a gateway that claims
        the command at the same moment wins and the cancel is refused.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: The charger.
        command_id: The command.
        principal: The caller (manage access to the charger).

    Returns:
        The command as it is now.

    Raises:
        ChargingStationNotFoundError: The command is not the charger's, or the
            charger is out of the caller's reach.
        ChargingStationCommandConflictError: The command was already sent or
            closed.
    """
    command = await get_charging_station_command(
        db, station_id, command_id, principal=principal
    )
    if not await ocpp_state_repository.cancel_queued_command(
        db, command_id, response_status="Cancelled"
    ):
        raise ChargingStationCommandConflictError(
            f"Command '{command.command_id}' is already sent or closed"
        )
    cancelled = await ocpp_state_repository.get_station_command_by_id(db, command_id)
    assert cancelled is not None, "the command was just cancelled"
    return to_charging_station_command_response(cancelled)


async def list_charging_station_commands(
    db: AsyncSession,
    station_id: UUID,
    *,
    principal: Principal,
    outcome: StationCommandOutcome | None = None,
    command_type: StationCommandType | None = None,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingStationCommandListResponse:
    """List a charger's commands, newest first (STN-10).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: The charger.
        principal: The caller (manage access to the charger).
        outcome: Only commands with this observed outcome.
        command_type: Only commands of this type.
        page: Page number starting at one.
        page_size: Page size, bounded by settings.

    Returns:
        The commands and pagination metadata.

    Raises:
        ChargingStationNotFoundError: If the charger is not active.
    """
    await station_access.get_station_in_reach(db, station_id, principal)
    page_window = normalize_page_window(page, page_size)
    commands = await ocpp_state_repository.list_station_commands(
        db,
        station_id=station_id,
        offset=page_window.offset,
        limit=page_window.page_size,
        outcome=outcome,
        command_type=command_type,
    )
    total = await ocpp_state_repository.count_station_commands(
        db, station_id, outcome=outcome, command_type=command_type
    )
    return ChargingStationCommandListResponse(
        items=[to_charging_station_command_response(command) for command in commands],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )
