"""Data types of the charging_sessions domain.

A session is created ``PENDING`` at the QR scan and turned ``ACTIVE`` by the
charger's start message that carries the single-use token (CE-10, CE-11); the
stop message completes it. The messages are assumed to arrive in order, without
duplicates and without interruption - retry, DLQ, out-of-order recovery and
dedup remain deferred (``deferred.md`` item 27). Two correctness invariants
hold on the happy path (F-B2): a session's status can only move forward (data
for an already-``COMPLETED`` session is refused, not applied) and the session
keeps only the meter readings the charger declares (CE-12).

The read side adds ``EnergySeriesGranularity`` (F-C5 time series),
``ChargingSessionListFilter`` (F-B2 list filters) and ``StationEnergyTotal``,
the DTO ``charging_stations`` receives for its all-stations energy report.
"""

import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

# The measurand whose reading (in Wh) is the session's cumulative energy. Every
# other measurand is stored beside it in the same measurements table.
ENERGY_ACTIVE_IMPORT_REGISTER: Final[str] = "Energy.Active.Import.Register"
# Fixed unit of every stored energy-register value: the OCPP gateway converts
# Wh/kWh into Wh before the value reaches this domain (CE-14).
ENERGY_UNIT_WH: Final[str] = "Wh"
# OCPP defaults stored when a charger omits the field (CE-14): the columns are
# required, so readers never meet a NULL.
DEFAULT_MEASUREMENT_CONTEXT: Final[str] = "Sample.Periodic"
DEFAULT_MEASUREMENT_LOCATION: Final[str] = "Outlet"
# Measurement locations features read: the charger's output for energy and
# power, the truck's side for the state of charge (CE-14).
MEASUREMENT_LOCATION_OUTLET: Final[str] = "Outlet"
# Widths of the session columns. Public so an adapter that receives a longer
# vendor value can truncate or refuse it before it reaches the database.
STOP_REASON_MAX_LENGTH: Final[int] = 30
OCPP_TRANSACTION_ID_MAX_LENGTH: Final[int] = 36
ID_TOKEN_MAX_LENGTH: Final[int] = 255
# Length of the single-use token generated at a scan: OCPP 1.6J's idTag is at
# most 20 characters, so the token must fit it (CE-10).
QR_TOKEN_LENGTH: Final[int] = 12


class SessionStatus(str, enum.Enum):
    """The lifecycle status of a session (CE-10); observed, so no reason.

    Attributes:
        PENDING: The scan was accepted, waiting for the charger.
        ACTIVE: The transaction is running.
        COMPLETED: The charger stopped the transaction.
        ABANDONED: The remote start was refused or timed out, or no
            transaction followed within the configured window.
    """

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True, slots=True)
class MeterSampleInput:
    """An energy-register sample already converted to Wh by the gateway.

    Attributes:
        sampled_at: The time the sample occurred, timezone-aware.
        value_wh: The energy value in Wh.
        context: The reading context (OCPP ``ReadingContext``), the OCPP
            default when the charger sent none.
        measurement_location: Where it was measured (``Outlet``...), the OCPP
            default when the charger sent none.
    """

    sampled_at: datetime
    value_wh: Decimal
    context: str = DEFAULT_MEASUREMENT_CONTEXT
    measurement_location: str = DEFAULT_MEASUREMENT_LOCATION


@dataclass(frozen=True, slots=True)
class MeasurementInput:
    """One measurement of a session, already normalized by the OCPP gateway.

    Attributes:
        sampled_at: The time the sample occurred, timezone-aware.
        measurand: The OCPP measurand name, as sent (vendor-specific names are
            allowed).
        value: The reading, a finite number, already in the fixed unit of a
            known measurand (CE-14).
        unit: The unit of ``value``: the fixed unit of a known measurand; as
            sent, or ``None``, for a vendor one.
        context: The reading context, the OCPP default when none was sent.
        phase: The electrical phase, if any.
        measurement_location: Where it was measured, the OCPP default when
            none was sent.
    """

    sampled_at: datetime
    measurand: str
    value: Decimal
    unit: str | None = None
    context: str = DEFAULT_MEASUREMENT_CONTEXT
    phase: str | None = None
    measurement_location: str = DEFAULT_MEASUREMENT_LOCATION


@dataclass(frozen=True, slots=True)
class TransactionIngestResult:
    """The result of processing one start or stop message.

    Attributes:
        session_id: The UUID of the session started or completed.
        status: The status after processing the message.
    """

    session_id: UUID
    status: SessionStatus


@dataclass(frozen=True, slots=True)
class SessionEndedEvent:
    """What a session-ended hook is told when a session reaches its last status.

    Sent after the stop message completed a session (``COMPLETED``) or after a
    scan that never started was given up (``ABANDONED``), inside the same
    transaction (BL-10, PAY-10).

    Attributes:
        session_id: The session that ended.
        status: ``COMPLETED`` or ``ABANDONED``.
        started_by: The scanning user; ``None`` for an abandoned session (the
            hook does not need it).
        meter_start_wh: The charger's start reading, ``None`` if it declared none.
        meter_stop_wh: The charger's closing reading, ``None`` if it declared
            none (CE-12).
        last_measured_wh: The newest outlet energy-register measurement, the
            fallback when the closing reading is missing and the cross-check
            when it is present; ``None`` if the session has none.
    """

    session_id: UUID
    status: SessionStatus
    started_by: UUID | None
    meter_start_wh: Decimal | None
    meter_stop_wh: Decimal | None
    last_measured_wh: Decimal | None


# A callback another part of the application registers to react, inside the
# same transaction, when a session ends. Called as ``hook(db, event)`` with a
# ``SessionEndedEvent``. charging_sessions imports no billing code (the edge
# between the two domains stays one-way): billing is wired to it in
# ``app/api/billing_hooks.py`` (decision log BL-19).
SessionEndedHook = Callable[..., Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PendingSessionReference:
    """What the caller of a scan needs from the PENDING session just created.

    Attributes:
        session_id: The UUID of the new session.
        station_id: The charger the scan named.
        id_token: The single-use token to send in the remote start and that
            the charger echoes in its start message; never log it (IS-07).
    """

    session_id: UUID
    station_id: UUID
    id_token: str


@dataclass(frozen=True, slots=True)
class TransactionSessionReference:
    """Minimal reference to a started session found by its OCPP transaction.

    Lets a caller that only knows ``(station, transactionId)`` (for example an
    OCPP 1.6J ``StopTransaction``, which carries no connector) learn the
    session's topology and status without receiving an ORM model.

    Attributes:
        session_id: The UUID of the session.
        station_id: The UUID of the station that owns the transaction.
        evse_id: The UUID of the EVSE that owns the transaction.
        connector_id: The UUID of the connector delivering power.
        status: The session's current status (``ACTIVE`` or ``COMPLETED``).
    """

    session_id: UUID
    station_id: UUID
    evse_id: UUID
    connector_id: UUID
    status: SessionStatus


@dataclass(frozen=True, slots=True)
class SessionCommandReference:
    """What the OCPP gateway needs from a session to send a remote start or stop.

    Attributes:
        session_id: The UUID of the session.
        id_token: The token the session carries (the single-use token of a
            QR start, CE-11); never copy it into application logs (IS-07).
        ocpp_transaction_id: The charger's transaction ID as stored, ``None``
            while the session is still waiting for the charger.
    """

    session_id: UUID
    id_token: str
    ocpp_transaction_id: str | None


class EnergySeriesGranularity(str, enum.Enum):
    """Bucket size of the station energy time series (F-C5).

    Buckets are cut in ``APP_REPORT_TIMEZONE`` (decision D4 of the
    happy-path completion planner) and returned as UTC instants.

    Attributes:
        HOUR: One bucket per local clock hour.
        DAY: One bucket per local calendar day.
    """

    HOUR = "hour"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class ChargingSessionListFilter:
    """Optional filters of the session list (F-B2); ``None`` means "any".

    Attributes:
        station_id: Only sessions of this station.
        connector_id: Only sessions on this connector.
        organization_id: Only sessions paid by this organization.
        started_by: Only sessions started (scanned) by this user.
        vehicle_id: Only sessions attributed to this truck (CHG-07).
        status: Only sessions in this lifecycle status.
        started_from: Only sessions with ``started_at >= started_from``
            (UTC, inclusive).
        started_to: Only sessions with ``started_at < started_to`` (UTC,
            exclusive).
    """

    station_id: UUID | None = None
    connector_id: UUID | None = None
    organization_id: UUID | None = None
    started_by: UUID | None = None
    vehicle_id: UUID | None = None
    status: SessionStatus | None = None
    started_from: datetime | None = None
    started_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class StationEnergyTotal:
    """Energy sold at one station within a window, for another domain (F-C5).

    Returned by ``resolve_station_energy_total`` to ``charging_stations``,
    which ranks every station for the all-stations report.

    Attributes:
        station_id: The station the total belongs to.
        total_energy_wh: Sum of ``meter_stop_wh - meter_start_wh`` of the
            station's completed sessions that ended within the window (CE-12);
            ``0`` if none.
        session_count: Number of those sessions.
    """

    station_id: UUID
    total_energy_wh: Decimal
    session_count: int


@dataclass(frozen=True, slots=True)
class MeterIngestResult:
    """The result of storing one energy-register sample.

    Each call stores exactly one sample, so no count is carried.

    Attributes:
        session_id: The UUID of the session the sample belongs to.
        status: The session's status after the sample.
    """

    session_id: UUID
    status: SessionStatus
