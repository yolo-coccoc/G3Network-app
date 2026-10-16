"""Public service of the charging sessions: scan, start, stop and measurements.

A session is created ``PENDING`` at the QR scan (``create_pending_session``,
CE-10) and turned ``ACTIVE`` by the charger's start message when its token
matches a PENDING session of the same charger (``activate_pending_session``,
CE-11); the stop message completes it (``complete_session``). The other ends
are handled here too: a charger that resends its start because it never got
the answer gets the transaction it already began back
(``find_started_session_by_token``, RV-CS3); internal staff close a session
the charger will never stop (``force_close_session``, CE-29); a scan the
charger never starts is abandoned by the stop from the app
(``prepare_session_stop``), by a failed remote start
(``abandon_pending_session``) or by the gateway's sweep once its window ends
(``abandon_expired_pending_sessions``, CE-10); and every end runs the
session-ended hooks (see below). General replay, DLQ, out-of-order recovery
and sequence-number dedup are still deferred (``docs/decisions/deferred.md``
item 27). Two correctness invariants hold (F-B2): a session's status can only
move forward (data arriving after ``COMPLETED`` is refused, not silently
applied), and the session keeps only the readings the charger declares
(``meter_start_wh``, ``meter_stop_wh``, CE-12). The caller at the entry
boundary owns commit/rollback of the transaction.

The QR flow (``app/api/charging_session_flow.py``) asks this module the
session-side rules of a scan (``ensure_session_may_start``: the gun is free,
the person has no other charge open), of a stop (``prepare_session_stop``)
and of a receipt (``get_completed_charging_session``).

Access (ACC-15): a session belongs to the organization that paid for it
(DM-24 C) and was started by the user who scanned. An HTTP caller sees the
sessions of their organization (internal staff all); a caller who is only a
DRIVER sees just the sessions they started. The owner of the charger does not
see other organizations' sessions at their chargers through this module
(`charging_sessions` cannot ask `charging_stations` who owns a charger).

The read-only monitoring endpoints live in area modules (CV-20, CV-23):
``read_service`` (session detail with its read-time summary, the filtered
session list, energy samples, measurements) and ``report_service`` (the
station energy summary and time series); the reach checks and response
mappers they share with this module are in ``access`` and ``mappers``. This
module keeps thin wrappers for the reads other code outside the domain calls
(``get_charging_session``, ``get_completed_charging_session``,
``resolve_station_energy_total``). The ingestion functions, ``allocate_ocpp16_transaction_id``,
``has_active_session_on_connector`` and ``resolve_session_by_transaction`` are
the public entry points the ``charging_stations`` OCPP adapters call;
``resolve_station_energy_total`` is the one its all-stations energy report
calls.

Billing reacts to the end of a session through hooks (``register_session_ended_hook``):
``COMPLETED`` by ``complete_session``, ``ABANDONED`` by the two abandon functions.
This domain imports no billing code; ``app/api/billing_hooks.py`` wires the two
(BL-19).
"""

import dataclasses
import logging
import secrets
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.access as session_access
import app.domains.charging_sessions.mappers as session_mappers
import app.domains.charging_sessions.read_service as session_read_service
import app.domains.charging_sessions.report_service as session_report_service
import app.domains.charging_sessions.repository as charging_session_repository
from app.domains.charging_sessions.exceptions import (
    ChargingConnectorBusyError,
    ChargingSessionAlreadyOpenError,
    ChargingSessionInputError,
    ChargingSessionNotFoundError,
    ChargingSessionStateError,
    ChargingSessionStopDeniedError,
    ChargingSessionTokenError,
)
from app.domains.charging_sessions.models import (
    ChargingSessionModel,
)
from app.domains.charging_sessions.schemas import (
    ChargingSessionDetailResponse,
    ChargingSessionResponse,
)
from app.domains.charging_sessions.types import (
    ENERGY_ACTIVE_IMPORT_REGISTER,
    ENERGY_UNIT_WH,
    ID_TOKEN_MAX_LENGTH,
    MEASUREMENT_LOCATION_OUTLET,
    OCPP_TRANSACTION_ID_MAX_LENGTH,
    QR_TOKEN_ALPHABET,
    QR_TOKEN_LENGTH,
    STAFF_CLOSED_STOP_REASON,
    STOP_REASON_MAX_LENGTH,
    MeasurementInput,
    MeterIngestResult,
    MeterSampleInput,
    PendingSessionReference,
    SessionCommandReference,
    SessionEndedEvent,
    SessionEndedHook,
    SessionStatus,
    SessionStopTarget,
    StartedSessionReference,
    StationEnergyTotal,
    TransactionIngestResult,
    TransactionSessionReference,
)
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings

logger = logging.getLogger(__name__)


# Callbacks run, in the caller's transaction, after a session ended (BL-19).
# Registered once per process at start-up through `register_session_ended_hook`
# (every process does, see `app/api/startup.py`); this domain imports no other
# domain.
_session_ended_hooks: list[SessionEndedHook] = []


def register_session_ended_hook(hook: SessionEndedHook) -> None:
    """Register a callback for the end of a session (completed or abandoned).

    Args:
        hook: Async callable ``hook(db, event)`` taking the database session
            and a `SessionEndedEvent`. It runs in the same transaction, inside
            a savepoint: if it raises, its own changes are rolled back and the
            error is logged, but the session still ends (a billing fault must
            never make the charger's stop message fail).

    Side Effects:
        Appends to a module-level list; registering the same hook twice is
        ignored.
    """
    if hook not in _session_ended_hooks:
        _session_ended_hooks.append(hook)


async def _run_session_ended_hooks(db: AsyncSession, event: SessionEndedEvent) -> None:
    """Run every registered session-ended hook, in registration order.

    Each hook runs inside its own savepoint so a failing hook leaves no
    half-written rows behind; the failure is logged with the traceback and the
    session's own end is kept.

    Args:
        db: The async session owned by the entry boundary.
        event: What ended.

    Side Effects:
        Whatever the hooks write, in the caller's transaction.
    """
    for hook in _session_ended_hooks:
        try:
            async with db.begin_nested():
                await hook(db, event)
        # A hook is a task boundary (backend-runtime-conventions.md): its
        # failure is isolated by the savepoint and must not undo the stop.
        except Exception:
            logger.exception(
                "Session-ended hook failed",
                extra={"session_id": str(event.session_id)},
            )


# Input length limits, mirroring the widths of the columns the values are
# stored in (``charging_session_measurements``): validating here turns an
# over-long value into a ChargingSessionInputError instead of a database error
# at flush time. The session columns' widths live in ``types.py``.
_MEASURAND_MAX_LENGTH: Final[int] = 60


_MEASUREMENT_UNIT_MAX_LENGTH: Final[int] = 20


_MEASUREMENT_CONTEXT_MAX_LENGTH: Final[int] = 30


_MEASUREMENT_PHASE_MAX_LENGTH: Final[int] = 10


_MEASUREMENT_LOCATION_MAX_LENGTH: Final[int] = 20


def _validate_energy(value: Decimal | None, field_name: str) -> Decimal | None:
    """Check that the energy value is a non-negative Decimal.

    Args:
        value: The energy value, which may be nullable.
        field_name: The field name used in the error message.

    Returns:
        A finite, non-negative Decimal, or ``None``.

    Raises:
        ChargingSessionInputError: If the value has the wrong type, is not
            finite, or is negative.
    """
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ChargingSessionInputError(f"{field_name} must be a finite Decimal")
    if value < 0:
        raise ChargingSessionInputError(f"{field_name} must not be negative")
    return value


def _validate_measurement(sample: MeasurementInput) -> MeasurementInput:
    """Validate one measurement and normalize its time to UTC.

    Pure: performs no I/O. Values may be negative (a temperature, an
    exported power) and vendor-specific measurand names are accepted. The
    gateway has already converted known measurands to their fixed unit.

    Args:
        sample: The measurement as normalized by the OCPP adapter.

    Returns:
        A copy of the sample with ``sampled_at`` in UTC.

    Raises:
        ChargingSessionInputError: On an empty or over-long measurand, an empty
            context or location, a field longer than its column, a
            non-finite value or a naive timestamp.
    """
    if not sample.measurand or len(sample.measurand) > _MEASURAND_MAX_LENGTH:
        raise ChargingSessionInputError(
            f"measurand must be 1-{_MEASURAND_MAX_LENGTH} characters"
        )
    for field_name, field_value, max_length in (
        ("unit", sample.unit, _MEASUREMENT_UNIT_MAX_LENGTH),
        ("context", sample.context, _MEASUREMENT_CONTEXT_MAX_LENGTH),
        ("phase", sample.phase, _MEASUREMENT_PHASE_MAX_LENGTH),
        (
            "measurement_location",
            sample.measurement_location,
            _MEASUREMENT_LOCATION_MAX_LENGTH,
        ),
    ):
        if field_value is not None and len(field_value) > max_length:
            raise ChargingSessionInputError(
                f"{field_name} exceeds {max_length} characters"
            )
    if not sample.context or not sample.measurement_location:
        raise ChargingSessionInputError(
            "context and measurement_location are required (CE-14)"
        )
    if not isinstance(sample.value, Decimal) or not sample.value.is_finite():
        raise ChargingSessionInputError("value must be a finite Decimal")
    return dataclasses.replace(
        sample, sampled_at=session_access.normalize_utc(sample.sampled_at, "sampled_at")
    )


def _pending_window_start() -> datetime:
    """Tell the oldest scan time a PENDING session may still have (CE-10, CE-11).

    Returns:
        Now minus ``CHARGING_PENDING_SESSION_TIMEOUT_SECONDS``: a scan older
        than this is expired even before the sweep marks it ABANDONED, so its
        token no longer starts a charge.
    """
    return utc_now() - timedelta(
        seconds=settings.CHARGING_PENDING_SESSION_TIMEOUT_SECONDS
    )


def _touch_session(session_record: ChargingSessionModel) -> None:
    """Stamp the aggregate's ``updated_at`` with the current time.

    The column's ``onupdate`` is not enough on its own: it only fires when
    the flush emits an ``UPDATE``, i.e. when another column changed. An
    ``Updated`` event without a reading, a stale sample or a non-energy
    measurement changes nothing else, yet still counts as activity on the
    session.

    Args:
        session_record: The ORM aggregate being processed in the transaction.

    Side Effects:
        Sets ``updated_at``; written at the caller's next flush/commit.
    """
    session_record.updated_at = utc_now()


async def _get_open_charging_session_record(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionModel:
    """Load a session that must exist and still accept readings (F-B2).

    Args:
        db: The current async session.
        session_id: UUID of the session to load.

    Returns:
        The existing ``ACTIVE`` session.

    Raises:
        ChargingSessionNotFoundError: If the session is not found.
        ChargingSessionStateError: If the session is not ``ACTIVE``: data
            landing after the stop must not silently rewrite a finished
            session, and a session that never started has no readings.
    """
    session_record = await session_access.get_charging_session_record(db, session_id)
    if session_record.status is not SessionStatus.ACTIVE:
        raise ChargingSessionStateError(
            f"Session '{session_id}' is {session_record.status.value}, not active"
        )
    return session_record


async def _get_open_session_by_transaction(
    db: AsyncSession,
    *,
    station_id: UUID,
    transaction_id: str,
    evse_id: UUID | None,
    connector_id: UUID | None,
) -> ChargingSessionModel:
    """Load the session a stop message belongs to.

    Args:
        db: The current async session.
        station_id: UUID of the station that raised the transaction.
        transaction_id: The validated OCPP transaction identity.
        evse_id: UUID of the EVSE the message names, if it names one.
        connector_id: UUID of the connector the message names, if it names one.

    Returns:
        The existing ``ACTIVE`` session.

    Raises:
        ChargingSessionNotFoundError: If no start created it.
        ChargingSessionInputError: If the message's EVSE/connector differ from
            the session's.
        ChargingSessionStateError: If the session is not ``ACTIVE`` (already
            ``COMPLETED``, or never started).

    Side Effects:
        Locks the row until the transaction ends: a stop that waits on a staff
        force-close of the same session (CE-29) then sees ``COMPLETED``.
    """
    session_record = await charging_session_repository.get_session_by_transaction(
        db, station_id, transaction_id, for_update=True
    )
    if session_record is None:
        raise ChargingSessionNotFoundError(
            f"Transaction '{transaction_id}' has no start yet"
        )
    if (evse_id is not None and session_record.evse_id != evse_id) or (
        connector_id is not None and session_record.connector_id != connector_id
    ):
        raise ChargingSessionInputError("Transaction topology does not match")
    if session_record.status is not SessionStatus.ACTIVE:
        # COMPLETED is terminal: re-stamping ended_at or re-applying a reading
        # would silently rewrite a finished session and corrupt F-C5's energy
        # totals. Distinguishing a harmless replay from a genuinely different
        # late message needs sequence-number dedup (deferred.md item 27), so
        # every post-stop message is refused the same way.
        raise ChargingSessionStateError(
            f"Transaction '{transaction_id}' is already completed"
        )
    return session_record


async def create_pending_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    organization_id: UUID,
    started_by: UUID,
    vehicle_id: UUID | None = None,
) -> PendingSessionReference:
    """Create a PENDING session for a QR scan and issue its single-use token.

    The token is sent to the charger in the remote start; the charger echoes it
    in its start message, which is how that message finds this row (CE-11).
    It is ``QR_TOKEN_LENGTH`` random upper-case letters and digits (CE-28), so a
    charger that upper-cases the idTag still sends back the same token.
    The start flow in ``app/api/charging_session_flow.py`` runs the checks
    first (the charger, ``ensure_session_may_start``, the wallet, the price,
    the truck from the driving session), calls this function, then queues the
    remote start.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the charger the scan named.
        organization_id: UUID of the organization that pays.
        started_by: UUID of the user who scanned the code.
        vehicle_id: UUID of the truck being charged, if known (CE-13).

    Returns:
        A reference holding the new session's ID, its token and when the scan
        expires (the scan time plus ``CHARGING_PENDING_SESSION_TIMEOUT_SECONDS``,
        the same window the sweep and the start message use, CE-10).

    Side Effects:
        Inserts one PENDING row in the caller's transaction; an unknown
        station, organization, user or vehicle fails at flush time with an
        ``IntegrityError``.
    """
    id_token = "".join(
        secrets.choice(QR_TOKEN_ALPHABET) for _ in range(QR_TOKEN_LENGTH)
    )
    session_record = await charging_session_repository.create_pending_session(
        db,
        station_id=station_id,
        organization_id=organization_id,
        started_by=started_by,
        vehicle_id=vehicle_id,
        id_token=id_token,
    )
    return PendingSessionReference(
        session_id=session_record.session_id,
        station_id=station_id,
        id_token=id_token,
        expires_at=session_record.created_at
        + timedelta(seconds=settings.CHARGING_PENDING_SESSION_TIMEOUT_SECONDS),
    )


async def activate_pending_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    id_token: str,
    transaction_id: str,
    started_at: datetime,
    meter_start_wh: Decimal | None,
) -> TransactionIngestResult:
    """Turn the PENDING session holding a start message's token ACTIVE (CE-11).

    Rule:
        1. Every input field is validated and normalized before anything is
           read or written.
        2. The token must belong to a PENDING session of the same charger;
           otherwise the start is refused and no row is created.
        3. The charger's gun, transaction ID, start time and start reading are
           filled in and the status becomes ``ACTIVE``.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the charger that sent the message.
        evse_id: UUID of the EVSE the transaction runs on.
        connector_id: UUID of the connector the transaction runs on.
        id_token: The token the message carries.
        transaction_id: The charger's transaction identity (a 1.6J number as
            text, or a 2.0.1 string of at most 36 characters).
        started_at: The charger's start time; must have a timezone.
        meter_start_wh: The start reading in Wh; required, because an ACTIVE
            session always has one (CE-10).

    Returns:
        The session ID and its status after the start.

    Raises:
        ChargingSessionInputError: If a field is empty, over-long, naive or
            negative, or the start reading is missing.
        ChargingSessionTokenError: If no PENDING session of this charger holds
            the token (CE-11).

    Side Effects:
        Updates the session in the caller's transaction; a duplicate
        ``(station, transaction ID)`` is refused by the unique index at flush
        time.
    """
    normalized_transaction_id = transaction_id.strip()
    if (
        not normalized_transaction_id
        or len(normalized_transaction_id) > OCPP_TRANSACTION_ID_MAX_LENGTH
    ):
        raise ChargingSessionInputError(
            f"transaction_id is empty or exceeds {OCPP_TRANSACTION_ID_MAX_LENGTH} "
            "characters"
        )
    if not id_token or len(id_token) > ID_TOKEN_MAX_LENGTH:
        raise ChargingSessionInputError(
            f"id_token is empty or exceeds {ID_TOKEN_MAX_LENGTH} characters"
        )
    started_at_utc = session_access.normalize_utc(started_at, "started_at")
    validated_meter_start_wh = _validate_energy(meter_start_wh, "meter_start_wh")
    if validated_meter_start_wh is None:
        raise ChargingSessionInputError("meter_start_wh is required to start a session")
    session_record = await charging_session_repository.find_pending_session_by_token(
        db, station_id, id_token, created_after=_pending_window_start()
    )
    if session_record is None:
        # The token is deliberately left out of the message and the log (IS-07).
        raise ChargingSessionTokenError(
            "The start message carries no token issued for this charger"
        )
    session_record.evse_id = evse_id
    session_record.connector_id = connector_id
    session_record.ocpp_transaction_id = normalized_transaction_id
    session_record.started_at = started_at_utc
    session_record.meter_start_wh = validated_meter_start_wh
    session_record.status = SessionStatus.ACTIVE
    _touch_session(session_record)
    return TransactionIngestResult(
        session_id=session_record.session_id, status=session_record.status
    )


async def find_started_session_by_token(
    db: AsyncSession, *, station_id: UUID, id_token: str
) -> StartedSessionReference | None:
    """Find the session a token already started on a charger (RV-CS3).

    A charger that never got the answer to its ``StartTransaction`` sends it
    again. The token then matches no PENDING session, but the session it
    started is ACTIVE: the retry must get the same transaction identity back,
    or the charger adopts ``0``, its stop finds nothing and the session never
    completes.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the charger that sent the message.
        id_token: The token the message carries (never logged, IS-07).

    Returns:
        The ACTIVE session's ID and transaction identity, or ``None`` when no
        such session exists.
    """
    session_record = await charging_session_repository.find_active_session_by_token(
        db, station_id=station_id, id_token=id_token
    )
    if session_record is None or session_record.ocpp_transaction_id is None:
        return None
    return StartedSessionReference(
        session_id=session_record.session_id,
        ocpp_transaction_id=session_record.ocpp_transaction_id,
    )


async def complete_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    transaction_id: str,
    ended_at: datetime,
    stop_reason: str | None = None,
    meter_stop_wh: Decimal | None = None,
    evse_id: UUID | None = None,
    connector_id: UUID | None = None,
) -> TransactionIngestResult:
    """Complete the ACTIVE session a stop message belongs to.

    Rule:
        1. Every input field is validated and normalized before anything is
           read or written.
        2. The session must exist, match the message's topology (when it
           names one) and be ``ACTIVE`` (F-B2): a duplicate stop or a late
           message must not silently re-mutate a finished record.
        3. The stop time, reason and the charger's closing reading (the
           billing figure, CE-12, stored as sent) are written and the status
           becomes ``COMPLETED``.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the station that raised the transaction.
        transaction_id: The charger's transaction identity.
        ended_at: The charger's stop time; must have a timezone.
        stop_reason: Why the session stopped, as sent (at most 30 characters).
        meter_stop_wh: The closing reading in Wh, if the message carries one.
        evse_id: UUID of the EVSE the message names, if it names one.
        connector_id: UUID of the connector the message names, if it names one.

    Returns:
        The session ID and its status after the stop.

    Raises:
        ChargingSessionInputError: If a field is invalid or the topology does
            not match.
        ChargingSessionNotFoundError: If the station has no such transaction.
        ChargingSessionStateError: If the session is not ``ACTIVE``.

    Side Effects:
        Updates the session in the caller's transaction, then runs the
        session-ended hooks (billing computes the bill, BL-10).
    """
    normalized_transaction_id = transaction_id.strip()
    if not normalized_transaction_id:
        raise ChargingSessionInputError("transaction_id is empty")
    ended_at_utc = session_access.normalize_utc(ended_at, "ended_at")
    validated_meter_stop_wh = _validate_energy(meter_stop_wh, "meter_stop_wh")
    if stop_reason is not None and len(stop_reason) > STOP_REASON_MAX_LENGTH:
        raise ChargingSessionInputError(
            f"stop_reason exceeds {STOP_REASON_MAX_LENGTH} characters"
        )
    session_record = await _get_open_session_by_transaction(
        db,
        station_id=station_id,
        transaction_id=normalized_transaction_id,
        evse_id=evse_id,
        connector_id=connector_id,
    )
    session_record.ended_at = ended_at_utc
    session_record.status = SessionStatus.COMPLETED
    session_record.stop_reason = stop_reason
    session_record.meter_stop_wh = validated_meter_stop_wh
    _touch_session(session_record)
    await _run_completed_session_hooks(db, session_record)
    return TransactionIngestResult(
        session_id=session_record.session_id, status=session_record.status
    )


async def _run_completed_session_hooks(
    db: AsyncSession,
    session_record: ChargingSessionModel,
    *,
    ended_by: UUID | None = None,
) -> None:
    """Tell the session-ended hooks (billing) that a session completed.

    Args:
        db: The caller's async session.
        session_record: The session just set to ``COMPLETED``.
        ended_by: The staff member who closed it, ``None`` for the charger.

    Side Effects:
        Reads the newest outlet energy measurement, then runs every registered
        hook in the caller's transaction (billing settles the bill, BL-10).
    """
    # The newest measurement is read only when somebody listens: it is the
    # fallback and the cross-check of the billing figure (CE-12).
    if not _session_ended_hooks:
        return
    await _run_session_ended_hooks(
        db,
        await _build_completed_event(db, session_record, ended_by=ended_by),
    )


async def _build_completed_event(
    db: AsyncSession,
    session_record: ChargingSessionModel,
    *,
    ended_by: UUID | None,
) -> SessionEndedEvent:
    """Build the session-ended event of a completed session.

    Args:
        db: The caller's async session.
        session_record: A ``COMPLETED`` session.
        ended_by: The staff member who acted, ``None`` for the charger.

    Returns:
        The event with the readings billing needs.

    Side Effects:
        Reads the session's newest outlet energy measurement.
    """
    last_measured_wh = await charging_session_repository.find_last_measurement_value(
        db,
        session_record.session_id,
        measurand=ENERGY_ACTIVE_IMPORT_REGISTER,
        measurement_location=MEASUREMENT_LOCATION_OUTLET,
    )
    return SessionEndedEvent(
        session_id=session_record.session_id,
        status=SessionStatus.COMPLETED,
        started_by=session_record.started_by,
        meter_start_wh=session_record.meter_start_wh,
        meter_stop_wh=session_record.meter_stop_wh,
        last_measured_wh=last_measured_wh,
        ended_by=ended_by,
    )


async def resolve_session_ended_event(
    db: AsyncSession,
    session_id: UUID,
    *,
    principal: Principal,
) -> SessionEndedEvent:
    """Rebuild the session-ended event of a finished session (hardening D2).

    A session-ended hook that failed (it runs in a savepoint and only logs)
    leaves a finished session whose bill is still QUOTED. Staff re-run the
    billing with this event; the billing functions are idempotent, so a bill
    that was settled meanwhile is left as it is.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session.
        principal: The staff member retrying; their data reach applies and
            they are recorded as the actor (``ended_by``).

    Returns:
        A ``COMPLETED`` event with the readings, or an ``ABANDONED`` one.

    Raises:
        ChargingSessionNotFoundError: The session does not exist or is out of
            the caller's reach (404).
        ChargingSessionStateError: The session has not ended (409).

    Side Effects:
        Locks the session row (a stop arriving at the same moment runs before
        or after) and reads its newest outlet energy measurement.
    """
    session_record = await session_access.get_charging_session_record(
        db, session_id, principal, for_update=True
    )
    if session_record.status is SessionStatus.COMPLETED:
        return await _build_completed_event(
            db, session_record, ended_by=principal.user_id
        )
    if session_record.status is SessionStatus.ABANDONED:
        return dataclasses.replace(
            _build_abandoned_event(session_id), ended_by=principal.user_id
        )
    raise ChargingSessionStateError(
        f"Session '{session_id}' is {session_record.status.value}; "
        "only a finished session can be billed again"
    )


async def force_close_session(
    db: AsyncSession,
    session_id: UUID,
    *,
    principal: Principal,
    reason: str,
) -> ChargingSessionDetailResponse:
    """Complete an ACTIVE session the charger will never stop (CE-29).

    A charger that reboots or loses power mid-session may never send its stop,
    which leaves the gun and the driver blocked. Internal staff close it here.

    Rule:
        1. The session is read under a row lock, so a ``StopTransaction``
           arriving at the same moment runs before or after this, never
           interleaved (the later one sees ``COMPLETED``; the charger's stop is
           then confirmed and ignored, CO-16).
        2. Only an ``ACTIVE`` session can be closed: a PENDING scan has its own
           stop (it is abandoned), a finished one is final.
        3. It completes now with ``stop_reason`` ``StaffClosed`` and **no
           closing reading**, then the session-ended hooks run as for a normal
           stop: billing falls back to the newest outlet measurement, or puts
           the bill ON_HOLD when the charger sent none (CE-12).

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session to close.
        principal: The staff member closing it; their data reach applies.
        reason: Why it is closed, as typed by the staff member; logged only
            (no column holds it, deferred.md 101).

    Returns:
        The closed session with its read-time summary.

    Raises:
        ChargingSessionNotFoundError: The session does not exist or is out of
            the caller's reach (404).
        ChargingSessionStateError: The session is not ``ACTIVE`` (409).

    Side Effects:
        Updates the session and runs the session-ended hooks in the caller's
        transaction; logs a warning with the staff member and the reason.
    """
    session_record = await session_access.get_charging_session_record(
        db, session_id, principal, for_update=True
    )
    if session_record.status is not SessionStatus.ACTIVE:
        raise ChargingSessionStateError(
            f"Session '{session_id}' is {session_record.status.value}; "
            "only an ACTIVE session can be force-closed"
        )
    session_record.ended_at = utc_now()
    session_record.status = SessionStatus.COMPLETED
    session_record.stop_reason = STAFF_CLOSED_STOP_REASON
    session_record.meter_stop_wh = None
    _touch_session(session_record)
    await _run_completed_session_hooks(db, session_record, ended_by=principal.user_id)
    logger.warning(
        "Charging session force-closed by staff",
        extra={
            "session_id": str(session_id),
            "station_id": str(session_record.station_id),
            "ocpp_transaction_id": session_record.ocpp_transaction_id,
            "closed_by": str(principal.user_id),
            "close_reason": reason,
        },
    )
    return await session_mappers.build_charging_session_detail_response(
        db, session_record
    )


async def ingest_meter_values(
    db: AsyncSession,
    *,
    session_id: UUID,
    sample: MeterSampleInput,
) -> MeterIngestResult:
    """Store one energy-register sample (Wh) of an ACTIVE session.

    Rule:
        1. The session must exist and be ``ACTIVE`` (F-B2), for the same
           reason as ``complete_session``'s guard.
        2. The sample's time and Wh value are validated.
        3. The sample is appended to the measurements (append-only); the
           session row keeps only the readings the charger declares, so
           nothing else changes except ``updated_at`` (CE-12).

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the session to update.
        sample: The sample, already converted to Wh by the gateway.

    Returns:
        A result containing the session UUID and its status.

    Raises:
        ChargingSessionInputError: If the sample lacks a timezone or the
            energy value is invalid.
        ChargingSessionNotFoundError: If the session does not exist.
        ChargingSessionStateError: If the session is not ``ACTIVE``.

    Side Effects:
        Appends a measurement row and stamps the session in the same
        transaction; the caller must commit or roll back at the entry
        boundary.
    """
    session_record = await _get_open_charging_session_record(db, session_id)
    sampled_at = session_access.normalize_utc(sample.sampled_at, "sampled_at")
    value_wh = _validate_energy(sample.value_wh, "value_wh")
    if value_wh is None:
        raise ChargingSessionInputError("value_wh is required")
    if not sample.context or not sample.measurement_location:
        raise ChargingSessionInputError(
            "context and measurement_location are required (CE-14)"
        )
    await charging_session_repository.insert_measurement(
        db,
        session_id=session_record.session_id,
        sampled_at=sampled_at,
        measurand=ENERGY_ACTIVE_IMPORT_REGISTER,
        value=value_wh,
        unit=ENERGY_UNIT_WH,
        context=sample.context,
        measurement_location=sample.measurement_location,
    )
    _touch_session(session_record)
    return MeterIngestResult(
        session_id=session_record.session_id,
        status=session_record.status,
    )


async def ingest_measurements(
    db: AsyncSession,
    *,
    session_id: UUID,
    samples: Sequence[MeasurementInput],
) -> int:
    """Store non-energy measurements of an ACTIVE session (SoC, power, voltage…).

    Rule:
        1. The session must exist and be ``ACTIVE``, for the same reason as in
           ``ingest_meter_values``.
        2. Every sample is validated, in payload order, before the first
           insert (``_validate_measurement``). Vendor-specific measurand
           names are accepted as sent; values may be negative (a
           temperature, an exported power).
        3. Each sample is inserted individually (append-only history, no
           batching).
        4. ``updated_at`` is stamped (``_touch_session``).

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the session that owns the samples.
        samples: The measurements to store, in payload order.

    Returns:
        The number of measurements stored.

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.
        ChargingSessionStateError: If the session is not ``ACTIVE``.
        ChargingSessionInputError: If a sample lacks a timezone, has a
            non-finite value, an empty measurand, or a field longer than its
            column (measurand 60, unit 20, context 30, phase 10, location 20).

    Side Effects:
        Appends rows and updates the session's ``updated_at`` in the caller's
        transaction; does not commit or roll back.
    """
    session_record = await _get_open_charging_session_record(db, session_id)
    validated_samples = [_validate_measurement(sample) for sample in samples]
    for validated_sample in validated_samples:
        await charging_session_repository.insert_measurement(
            db,
            session_id=session_record.session_id,
            sampled_at=validated_sample.sampled_at,
            measurand=validated_sample.measurand,
            value=validated_sample.value,
            unit=validated_sample.unit,
            context=validated_sample.context,
            phase=validated_sample.phase,
            measurement_location=validated_sample.measurement_location,
        )
    _touch_session(session_record)
    return len(validated_samples)


async def has_open_session_by_user(db: AsyncSession, user_id: UUID) -> bool:
    """Tell whether a person already has a charge open (CHG-01).

    A person pays from one wallet and may run one charge at a time: an
    ``ACTIVE`` session, or a ``PENDING`` scan still inside its window, counts.

    Args:
        db: The async session owned by the entry boundary.
        user_id: The scanning user.

    Returns:
        ``True`` when such a session exists.
    """
    open_count = await charging_session_repository.count_open_sessions_by_user(
        db, user_id, pending_created_after=_pending_window_start()
    )
    return open_count > 0


async def ensure_session_may_start(
    db: AsyncSession,
    *,
    user_id: UUID,
    connector_id: UUID | None,
    connector_ids: Sequence[UUID],
) -> None:
    """Check the session-side rules of a QR scan, in order (CHG-01, CO-14).

    1. The gun is free: with a gun named, it has no ``ACTIVE`` session; with
       none named, the driver may still pick a free gun on the charger's
       screen, so only a charger whose every gun is charging is refused (a
       charger with no gun at all is not refused here).
    2. The person has no other charge open (`has_open_session_by_user`). This
       read is not locked: the caller serializes one person's scans first
       (billing's wallet lock, RV-BL3).

    Args:
        db: The async session owned by the entry boundary.
        user_id: The scanning user.
        connector_id: The gun the QR code names, ``None`` when it names none.
        connector_ids: Every gun of the charger, used when no gun is named.

    Raises:
        ChargingConnectorBusyError: The gun, or every gun, is charging (409).
        ChargingSessionAlreadyOpenError: The person has a charge open (409).

    Side Effects:
        Read queries only (one per gun checked, then one for the person).
    """
    if connector_id is not None:
        is_busy = await has_active_session_on_connector(db, connector_id)
    else:
        is_busy = bool(connector_ids)
        for candidate_connector_id in connector_ids:
            if not await has_active_session_on_connector(db, candidate_connector_id):
                is_busy = False
                break
    if is_busy:
        raise ChargingConnectorBusyError("A charge is already running on this charger")
    if await has_open_session_by_user(db, user_id):
        raise ChargingSessionAlreadyOpenError(
            "You already have a charge open; finish or stop it first"
        )


async def is_start_token_valid(
    db: AsyncSession, *, station_id: UUID, id_token: str
) -> bool:
    """Tell whether a charger's ``Authorize`` presents a token we issued (CE-11).

    Used by both OCPP adapters: a token is valid when a scan issued it for this
    charger and the session is still waiting (inside its window) or running
    (the same token may be shown again to stop at the screen).

    Args:
        db: The async session owned by the entry boundary.
        station_id: The charger that sent the message.
        id_token: The presented token; never log it (IS-07).

    Returns:
        ``True`` for a token of a PENDING or ACTIVE session of that charger.
    """
    if not id_token or len(id_token) > ID_TOKEN_MAX_LENGTH:
        return False
    token_count = await charging_session_repository.count_sessions_with_token(
        db, station_id, id_token, pending_created_after=_pending_window_start()
    )
    return token_count > 0


def _build_abandoned_event(session_id: UUID) -> SessionEndedEvent:
    """Build the session-ended event of a scan that never started.

    Args:
        session_id: The abandoned session.

    Returns:
        An ``ABANDONED`` event with no readings.
    """
    return SessionEndedEvent(
        session_id=session_id,
        status=SessionStatus.ABANDONED,
        started_by=None,
        meter_start_wh=None,
        meter_stop_wh=None,
        last_measured_wh=None,
    )


async def abandon_pending_session(db: AsyncSession, session_id: UUID) -> bool:
    """End a scan that will never start: the session becomes ``ABANDONED``.

    Called by the OCPP gateway when the remote start of the session ended
    ``REJECTED``, ``ERROR``, ``TIMEOUT`` or ``NOT_SENT`` (CE-10). Idempotent: a
    session that is no longer ``PENDING`` (the charger started anyway) is left
    alone. Why it was abandoned stays on the command's record (CS-20).

    Args:
        db: The async session owned by the entry boundary.
        session_id: The session whose start failed.

    Returns:
        ``True`` when a PENDING session was abandoned.

    Side Effects:
        One UPDATE in the caller's transaction, then the session-ended hooks
        when a row changed (billing voids the quoted bill).
    """
    is_abandoned = await charging_session_repository.abandon_pending_session(
        db, session_id
    )
    if is_abandoned:
        await _run_session_ended_hooks(db, _build_abandoned_event(session_id))
    return is_abandoned


async def abandon_expired_pending_sessions(db: AsyncSession) -> int:
    """Abandon every PENDING session whose scan is older than the window (CE-10).

    The window is ``CHARGING_PENDING_SESSION_TIMEOUT_SECONDS`` counted from the
    scan (``created_at``). The OCPP gateway's loop calls this every
    ``CHARGING_SESSION_SWEEP_INTERVAL_SECONDS``.

    Args:
        db: The async session owned by the entry boundary.

    Returns:
        The number of sessions abandoned.

    Side Effects:
        One UPDATE in the caller's transaction, then the session-ended hooks
        once per abandoned session.
    """
    abandoned_ids = (
        await charging_session_repository.abandon_pending_sessions_created_before(
            db, _pending_window_start()
        )
    )
    for session_id in abandoned_ids:
        await _run_session_ended_hooks(db, _build_abandoned_event(session_id))
    return len(abandoned_ids)


async def authorize_session_stop(
    db: AsyncSession, session_id: UUID, *, principal: Principal
) -> ChargingSessionResponse:
    """Check that the caller may stop a session now and return it (CHG-01).

    Only the person who started the charge (the payer, BL-13) or internal staff
    may stop it, and only while it is ``ACTIVE`` or still ``PENDING``: a scan
    the charger never started is cancelled by the same call, so the person is
    not blocked until the pending window ends (RV-BL9).

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session to stop.
        principal: The caller.

    Returns:
        The session, with the gun and transaction the stop command needs.

    Raises:
        ChargingSessionNotFoundError: The session does not exist or is out of
            the caller's reach.
        ChargingSessionStopDeniedError: The caller neither started the session
            nor is internal staff.
        ChargingSessionStateError: The session is neither ``ACTIVE`` nor
            ``PENDING``.
    """
    session_record = await session_access.get_charging_session_record(
        db, session_id, principal
    )
    if session_record.started_by != principal.user_id and not principal.is_internal:
        raise ChargingSessionStopDeniedError(
            "Only the person who started this charge, or our staff, may stop it"
        )
    if session_record.status not in (SessionStatus.ACTIVE, SessionStatus.PENDING):
        raise ChargingSessionStateError(
            f"Session '{session_id}' is {session_record.status.value}, not active"
        )
    return session_mappers.to_charging_session_response(session_record)


async def prepare_session_stop(
    db: AsyncSession, session_id: UUID, *, principal: Principal
) -> SessionStopTarget:
    """Handle the session side of a stop asked for in the app (CHG-01, RV-BL9).

    Rule:
        1. The caller must be allowed to stop the session now
           (`authorize_session_stop`).
        2. A scan the charger has not started yet (``PENDING``) is abandoned at
           once: no command is needed, and the session-ended hooks void the
           quoted bill. The abandon is a conditional UPDATE, which decides
           against a start message arriving at the same moment: if the charger
           won, the session is ACTIVE now and the caller must stop it again,
           as a running charge.
        3. An ``ACTIVE`` session stays ACTIVE until the charger confirms with
           its own stop message; the caller queues the remote stop.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session to stop.
        principal: The caller.

    Returns:
        The session's charger and gun, its status after the call
        (``ABANDONED`` or ``ACTIVE``) and whether a remote stop must be queued.

    Raises:
        ChargingSessionNotFoundError: The session does not exist or is out of
            the caller's reach.
        ChargingSessionStopDeniedError: The caller neither started the session
            nor is internal staff.
        ChargingSessionStateError: The session is neither ``ACTIVE`` nor
            ``PENDING``, or the charger started a pending one while it was
            being cancelled.

    Side Effects:
        For a PENDING scan, one UPDATE and the session-ended hooks, in the
        caller's transaction.
    """
    session = await authorize_session_stop(db, session_id, principal=principal)
    if session.status is SessionStatus.PENDING:
        if not await abandon_pending_session(db, session.session_id):
            raise ChargingSessionStateError(
                f"Session '{session.session_id}' was started by the charger "
                "while it was being cancelled; stop it again"
            )
        return SessionStopTarget(
            session_id=session.session_id,
            station_id=session.station_id,
            evse_id=session.evse_id,
            status=SessionStatus.ABANDONED,
            should_queue_remote_stop=False,
        )
    return SessionStopTarget(
        session_id=session.session_id,
        station_id=session.station_id,
        evse_id=session.evse_id,
        status=session.status,
        should_queue_remote_stop=True,
    )


async def allocate_ocpp16_transaction_id(db: AsyncSession) -> int:
    """Allocate the next OCPP 1.6J transaction ID.

    OCPP 1.6J requires the CSMS (this backend) to assign the integer
    ``transactionId`` in the ``StartTransaction`` response. IDs come from a
    database sequence so they survive restarts and never repeat.

    Args:
        db: The async session owned by the entry boundary.

    Returns:
        The next integer transaction ID (32-bit range).

    Side Effects:
        Advances the sequence; a rolled-back transaction leaves a harmless
        gap in the numbers.
    """
    return await charging_session_repository.next_ocpp16_transaction_id(db)


async def has_active_session_on_connector(db: AsyncSession, connector_id: UUID) -> bool:
    """Tell whether a connector already has an open (``ACTIVE``) session.

    Args:
        db: The async session owned by the entry boundary.
        connector_id: UUID of the connector.

    Returns:
        ``True`` if at least one session on the connector is still active.
    """
    active_session_count = (
        await charging_session_repository.count_active_sessions_by_connector_id(
            db, connector_id
        )
    )
    return active_session_count > 0


async def resolve_session_by_transaction(
    db: AsyncSession,
    *,
    station_id: UUID,
    transaction_id: str,
) -> TransactionSessionReference:
    """Look up a session by the OCPP transaction identity a charger sent.

    Used by messages that carry a ``transactionId`` but no topology (OCPP 1.6J
    ``StopTransaction`` and ``MeterValues``), so nothing has to be remembered
    per connection: the database is the source of truth.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the station that sent the message.
        transaction_id: The transaction identity, as stored.

    Returns:
        A frozen reference with the session's ID, topology and status.

    Raises:
        ChargingSessionNotFoundError: If the station has no such transaction.
    """
    session_record = await charging_session_repository.get_session_by_transaction(
        db, station_id, transaction_id
    )
    if session_record is None:
        raise ChargingSessionNotFoundError(
            f"Transaction '{transaction_id}' not found for this station"
        )
    if session_record.evse_id is None or session_record.connector_id is None:
        raise ChargingSessionStateError(
            f"Transaction '{transaction_id}' has no gun recorded"
        )
    return TransactionSessionReference(
        session_id=session_record.session_id,
        station_id=session_record.station_id,
        evse_id=session_record.evse_id,
        connector_id=session_record.connector_id,
        status=session_record.status,
    )


async def resolve_session_command_reference(
    db: AsyncSession, session_id: UUID
) -> SessionCommandReference:
    """Read the token and transaction ID the gateway sends in a remote command.

    The remote start's token is kept on the session, not copied into the
    command (CS-20); the gateway reads it here when it sends the command.

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the session.

    Returns:
        A frozen reference with the session's token, transaction ID, charger
        and status.

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.
    """
    session_record = await charging_session_repository.get_session_by_id(db, session_id)
    if session_record is None:
        raise ChargingSessionNotFoundError(f"Session '{session_id}' not found")
    return SessionCommandReference(
        session_id=session_record.session_id,
        id_token=session_record.id_token,
        ocpp_transaction_id=session_record.ocpp_transaction_id,
        station_id=session_record.station_id,
        status=session_record.status,
    )


async def get_charging_session(
    db: AsyncSession,
    session_id: UUID,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> ChargingSessionDetailResponse:
    """Get the session aggregate and its read-time summary (F-B2).

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the aggregate to view.
        principal: The caller; a session out of their reach is not found.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a session on another truck or with no truck is not found unless
            the caller started it (hardening D5). `None` means no limit.

    Returns:
        The session response plus ``duration_seconds``,
        ``soc_start_percent``, ``soc_end_percent`` and ``max_power_kw``, all
        computed now from the stored measurements (never stored).

    Raises:
        ChargingSessionNotFoundError: If the session does not exist.

    Side Effects:
        Performs one aggregate query and four measurement queries; does not
        commit or roll back.
    """
    return await session_read_service.get_charging_session(
        db, session_id, principal=principal, visible_vehicle_ids=visible_vehicle_ids
    )


async def get_completed_charging_session(
    db: AsyncSession,
    session_id: UUID,
    *,
    principal: Principal,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> ChargingSessionDetailResponse:
    """Get a session a receipt may be shown for (CHG-03).

    A receipt exists only once the charge completed: a PENDING, ACTIVE or
    ABANDONED session has none.

    Args:
        db: The async session owned by the HTTP boundary.
        session_id: UUID of the session.
        principal: The caller; a session out of their reach is not found.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10),
            as for `get_charging_session`; `None` means no limit.

    Returns:
        The session detail, as `get_charging_session` returns it.

    Raises:
        ChargingSessionNotFoundError: The session does not exist or is out of
            the caller's reach.
        ChargingSessionStateError: The session has not completed.

    Side Effects:
        The read queries of `get_charging_session`; nothing is written.
    """
    return await session_read_service.get_completed_charging_session(
        db, session_id, principal=principal, visible_vehicle_ids=visible_vehicle_ids
    )


async def resolve_station_energy_total(
    db: AsyncSession,
    *,
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> StationEnergyTotal:
    """Total energy sold at a station within a window, as a DTO (F-C5).

    Public entry point for ``charging_stations``' all-stations report, and
    the core of ``get_station_energy_summary``: completed sessions of the
    station whose ``ended_at`` falls within the inclusive window, summing
    ``energy_delivered_wh``.

    Args:
        db: The async session owned by the caller's entry boundary.
        station_id: UUID of the station to aggregate over.
        start_time: Inclusive lower bound on ``ended_at``; must carry a
            timezone.
        end_time: Inclusive upper bound on ``ended_at``; must carry a
            timezone and be after ``start_time``.

    Returns:
        The total in Wh and the session count; zero for a station with no
        matching session (existence is not checked here).

    Raises:
        ChargingSessionInputError: If either timestamp lacks a timezone, or
            ``end_time`` isn't after ``start_time``.

    Side Effects:
        Performs one aggregate query; does not commit or roll back.
    """
    return await session_report_service.resolve_station_energy_total(
        db, station_id=station_id, start_time=start_time, end_time=end_time
    )
