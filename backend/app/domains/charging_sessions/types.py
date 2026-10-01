"""Minimal data types for the ideal charging_sessions MVP.

The MVP assumes messages arrive in order, without duplicates and without
interruption - retry, DLQ, out-of-order recovery, and dedup remain
deferred (``future.md`` item 27). Because of this the module only keeps
the active/completed status, three TransactionEvent types and one
canonical Wh meter sample. F-B2 layers two correctness invariants on top
of that assumption without reopening it: a session's status can only move
forward (an event for an already-``COMPLETED`` session is refused, not
applied), and a meter reading can only advance the aggregate's
``meter_end_wh`` forward in *time* (a sample stamped earlier than one
already applied is discarded, not overwritten).
"""

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

# The measurand whose canonical form (Wh) drives a session's energy total. Every
# other measurand is stored beside it in the same measurements table.
ENERGY_ACTIVE_IMPORT_REGISTER: Final[str] = "Energy.Active.Import.Register"
# Unit of every stored energy-register value: the OCPP adapter normalizes
# Wh/kWh into Wh before the value reaches this domain.
ENERGY_UNIT_WH: Final[str] = "Wh"
# Width of ``charging_sessions.stop_reason``. Public so an adapter that
# receives a longer vendor reason can truncate it to fit before ingesting.
STOP_REASON_MAX_LENGTH: Final[int] = 30


class SessionStatus(str, enum.Enum):
    """The single lifecycle status of a session in the happy path.

    Attributes:
        ACTIVE: The session has started but has not yet received ``Ended``.
        COMPLETED: The session has received ``Ended`` and has ``ended_at``.
    """

    ACTIVE = "active"
    COMPLETED = "completed"


class SessionEventType(str, enum.Enum):
    """The three TransactionEvent types stored in the ideal MVP.

    Attributes:
        STARTED: Starts the transaction and creates the aggregate.
        UPDATED: Updates a transaction that is currently active.
        ENDED: Ends the transaction and moves the aggregate to completed.
    """

    STARTED = "Started"
    UPDATED = "Updated"
    ENDED = "Ended"


@dataclass(frozen=True, slots=True)
class MeterSampleInput:
    """An energy sample already canonicalized to Wh.

    Attributes:
        sampled_at: The time the sample occurred, timezone-aware.
        value_wh: The energy value in Wh.
        context: The reading context (OCPP ``ReadingContext``, for example
            ``Sample.Periodic`` or ``Transaction.End``), if the protocol
            carries one.
    """

    sampled_at: datetime
    value_wh: Decimal
    context: str | None = None


@dataclass(frozen=True, slots=True)
class MeasurementInput:
    """One measurement of a session, already normalized by the OCPP adapter.

    Attributes:
        sampled_at: The time the sample occurred, timezone-aware.
        measurand: The OCPP measurand name, as sent (vendor-specific names are
            allowed).
        value: The reading, a finite number; for the energy register the
            adapter has already converted it to Wh.
        unit: The unit of ``value``, if known.
        context: The reading context (``Sample.Periodic``…), if any.
        phase: The electrical phase, if any.
        location: Where it was measured, if any.
    """

    sampled_at: datetime
    measurand: str
    value: Decimal
    unit: str | None = None
    context: str | None = None
    phase: str | None = None
    location: str | None = None


@dataclass(frozen=True, slots=True)
class TransactionIngestResult:
    """The result of processing one TransactionEvent in the happy path.

    Each call appends exactly one event, so no count is carried.

    Attributes:
        session_id: The UUID of the aggregate created or updated.
        status: The status after processing the event.
    """

    session_id: UUID
    status: SessionStatus


@dataclass(frozen=True, slots=True)
class TransactionSessionReference:
    """Minimal reference to a session found by its OCPP transaction identity.

    Lets a caller that only knows ``(station, transactionId)`` (for example an
    OCPP 1.6J ``StopTransaction``, which carries no connector) learn the
    session's topology and status without receiving an ORM model.

    Attributes:
        session_id: The UUID of the session aggregate.
        station_id: The UUID of the station that owns the transaction.
        evse_id: The UUID of the EVSE that owns the transaction.
        connector_id: The UUID of the connector delivering power.
        status: The session's current status.
    """

    session_id: UUID
    station_id: UUID
    evse_id: UUID
    connector_id: UUID
    status: SessionStatus


@dataclass(frozen=True, slots=True)
class MeterIngestResult:
    """The result of storing one energy-register sample in the happy path.

    Each call stores exactly one sample, so no count is carried.

    Attributes:
        session_id: The UUID of the aggregate that was updated.
        status: The aggregate's status after the sample.
    """

    session_id: UUID
    status: SessionStatus
