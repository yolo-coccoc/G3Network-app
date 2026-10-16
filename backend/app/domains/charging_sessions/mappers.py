"""Response mappers and read-time figures of the charging_sessions domain.

Turns session rows into the HTTP responses (the list item and the detail with
its read-time summary: duration, delivered energy, SoC and peak power) and
holds the pure calculations behind them (CE-12, CE-14). The ``build_``
function reads the session's measurements (documented on it). Internal
module: other domains use ``service.py``.
"""

from datetime import datetime
from decimal import Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_session_repository
from app.domains.charging_sessions.models import (
    ChargingSessionMeasurementModel,
    ChargingSessionModel,
)
from app.domains.charging_sessions.schemas import (
    ChargingSessionDetailResponse,
    ChargingSessionMeterValueResponse,
    ChargingSessionResponse,
)
from app.domains.charging_sessions.types import (
    ENERGY_ACTIVE_IMPORT_REGISTER,
    MEASUREMENT_LOCATION_OUTLET,
)
from app.libs.common.clock import utc_now

# Measurands read by the session summary (F-B2). Names as OCPP defines them.
# Values are stored in one fixed unit per measurand (CE-14): power in W, SoC in
# percent, so the summary converts nothing but W to kW for display.
_SOC_MEASURAND: Final[str] = "SoC"


_POWER_ACTIVE_IMPORT_MEASURAND: Final[str] = "Power.Active.Import"


_W_PER_KW: Final[Decimal] = Decimal(1000)


WH_PER_KWH: Final[Decimal] = Decimal(1000)


def calculate_session_duration_seconds(
    session_record: ChargingSessionModel, *, now: datetime
) -> int:
    """Compute how long a session lasted, or has lasted so far (F-B2).

    Args:
        session_record: The session.
        now: Reference time used while the session has no ``ended_at``.

    Returns:
        Whole seconds from ``started_at`` to ``ended_at`` (or ``now``),
        floored at zero so a charger clock ahead of the server never yields
        a negative duration; ``0`` for a session that never started.
    """
    if session_record.started_at is None:
        return 0
    ended_at = session_record.ended_at if session_record.ended_at is not None else now
    return max(int((ended_at - session_record.started_at).total_seconds()), 0)


def calculate_energy_delivered_wh(
    meter_start_wh: Decimal | None, meter_stop_wh: Decimal | None
) -> Decimal | None:
    """Compute the energy between two meter readings of a session (CE-12).

    Derived on every read, never stored.

    Args:
        meter_start_wh: The charger's start reading.
        meter_stop_wh: The charger's closing reading, or the figure standing in
            for it.

    Returns:
        The closing reading minus the start reading; ``None`` when either is
        missing.
    """
    if meter_start_wh is None or meter_stop_wh is None:
        return None
    return meter_stop_wh - meter_start_wh


def to_charging_session_response(
    session_record: ChargingSessionModel,
) -> ChargingSessionResponse:
    """Convert a session row into the monitoring response (pure mapping).

    The delivered energy is the stop reading minus the start reading, so a
    running session or one stopped without a reading shows ``None``; the
    detail read replaces it with its measurement-based estimate.

    Args:
        session_record: The session queried by the repository.

    Returns:
        The response.
    """
    return ChargingSessionResponse(
        session_id=session_record.session_id,
        station_id=session_record.station_id,
        evse_id=session_record.evse_id,
        connector_id=session_record.connector_id,
        organization_id=session_record.organization_id,
        started_by=session_record.started_by,
        vehicle_id=session_record.vehicle_id,
        ocpp_transaction_id=session_record.ocpp_transaction_id,
        status=session_record.status,
        started_at=session_record.started_at,
        ended_at=session_record.ended_at,
        meter_start_wh=session_record.meter_start_wh,
        stop_reason=session_record.stop_reason,
        meter_stop_wh=session_record.meter_stop_wh,
        energy_delivered_wh=calculate_energy_delivered_wh(
            session_record.meter_start_wh, session_record.meter_stop_wh
        ),
        created_at=session_record.created_at,
        updated_at=session_record.updated_at,
    )


async def _resolve_energy_delivered_wh(
    db: AsyncSession, session_record: ChargingSessionModel
) -> Decimal | None:
    """Compute the energy a session delivered, at read time (CE-12).

    Args:
        db: The current async session.
        session_record: The session.

    Returns:
        The charger's stop reading minus its start reading; while the session
        runs (or when the stop message carried no reading) the newest outlet
        energy measurement stands in for the stop reading. ``None`` without a
        start reading or any closing figure.

    Side Effects:
        Runs one measurement query when the stop reading is missing.
    """
    if session_record.meter_start_wh is None:
        return None
    closing_wh = session_record.meter_stop_wh
    if closing_wh is None:
        closing_wh = await charging_session_repository.find_last_measurement_value(
            db,
            session_record.session_id,
            measurand=ENERGY_ACTIVE_IMPORT_REGISTER,
            measurement_location=MEASUREMENT_LOCATION_OUTLET,
        )
    return calculate_energy_delivered_wh(session_record.meter_start_wh, closing_wh)


async def build_charging_session_detail_response(
    db: AsyncSession, session_record: ChargingSessionModel
) -> ChargingSessionDetailResponse:
    """Build the session detail, computing its summary from the measurements.

    Args:
        db: The async session owned by the HTTP boundary.
        session_record: The session already loaded.

    Returns:
        The session response plus the delivered energy, ``duration_seconds``,
        the first/last SoC and the maximum and newest outlet import power in kW
        (``None`` where the session has no such sample).

    Side Effects:
        Runs up to five measurement queries; does not commit or roll back.
    """
    soc_start = await charging_session_repository.find_first_measurement_value(
        db, session_record.session_id, measurand=_SOC_MEASURAND
    )
    soc_end = await charging_session_repository.find_last_measurement_value(
        db, session_record.session_id, measurand=_SOC_MEASURAND
    )
    max_power_w = await charging_session_repository.find_max_measurement_value(
        db,
        session_record.session_id,
        measurand=_POWER_ACTIVE_IMPORT_MEASURAND,
        measurement_location=MEASUREMENT_LOCATION_OUTLET,
    )
    current_power_w = await charging_session_repository.find_last_measurement_value(
        db,
        session_record.session_id,
        measurand=_POWER_ACTIVE_IMPORT_MEASURAND,
        measurement_location=MEASUREMENT_LOCATION_OUTLET,
    )
    session_response = to_charging_session_response(session_record)
    return ChargingSessionDetailResponse(
        **session_response.model_dump(exclude={"energy_delivered_wh"}),
        energy_delivered_wh=await _resolve_energy_delivered_wh(db, session_record),
        duration_seconds=calculate_session_duration_seconds(
            session_record, now=utc_now()
        ),
        soc_start_percent=float(soc_start) if soc_start is not None else None,
        soc_end_percent=float(soc_end) if soc_end is not None else None,
        max_power_kw=(
            float(max_power_w / _W_PER_KW) if max_power_w is not None else None
        ),
        current_power_kw=(
            float(current_power_w / _W_PER_KW) if current_power_w is not None else None
        ),
    )


def to_charging_session_meter_value_response(
    measurement_record: ChargingSessionMeasurementModel,
) -> ChargingSessionMeterValueResponse:
    """Convert an energy measurement into the ``/meter-values`` response schema.

    The response contract predates the unified measurements table and is kept
    unchanged: ``value_wh`` is the measurement's canonical Wh ``value``, shown
    with three decimals like the ``Numeric(24, 3)`` column it used to come
    from (the new column keeps six for non-energy measurands).

    Args:
        measurement_record: An energy-register measurement queried by the
            repository.

    Returns:
        A canonical Wh meter response.
    """
    return ChargingSessionMeterValueResponse(
        meter_value_id=measurement_record.measurement_id,
        sampled_at=measurement_record.sampled_at,
        session_id=measurement_record.session_id,
        value_wh=measurement_record.value.quantize(Decimal("0.001")),
    )
