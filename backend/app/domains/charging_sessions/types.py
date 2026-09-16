"""Minimal data types for the ideal charging_sessions MVP.

The MVP assumes messages arrive in order, without duplicates and without
interruption. Because of this the module only keeps the active/completed
status, three TransactionEvent types and one canonical Wh meter sample.
"""

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


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
    """

    sampled_at: datetime
    value_wh: Decimal


@dataclass(frozen=True, slots=True)
class TransactionIngestResult:
    """The result of processing one TransactionEvent in the happy path.

    Attributes:
        session_id: The UUID of the aggregate created or updated.
        status: The status after processing the event.
        event_count: The number of events appended in this call.
    """

    session_id: UUID
    status: SessionStatus
    event_count: int


@dataclass(frozen=True, slots=True)
class MeterIngestResult:
    """The result of processing one MeterValues message in the happy path.

    Attributes:
        session_id: The UUID of the aggregate that was updated.
        status: The aggregate's status after the batch.
        accepted_count: The number of samples persisted; always one on a
            successful call.
    """

    session_id: UUID
    status: SessionStatus
    accepted_count: int
