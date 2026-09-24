"""Smoke tests for the unified charging_session_measurements storage."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
from app.domains.charging_sessions.models import (
    ChargingSessionMeasurementModel,
    ChargingSessionModel,
)
from app.domains.charging_sessions.schemas import ChargingSessionMeterValueResponse
from app.domains.charging_sessions.types import (
    ENERGY_ACTIVE_IMPORT_REGISTER,
    ENERGY_UNIT_WH,
    MeterSampleInput,
    SessionStatus,
)

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def _active_session() -> ChargingSessionModel:
    return ChargingSessionModel(
        session_id=uuid4(),
        station_id=uuid4(),
        evse_id=uuid4(),
        connector_id=uuid4(),
        ocpp_transaction_id="TX-M",
        status=SessionStatus.ACTIVE,
        started_at=NOW,
        ended_at=None,
        meter_start_wh=Decimal(1000),
        meter_end_wh=None,
        meter_end_sampled_at=None,
        energy_delivered_wh=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch, session: ChargingSessionModel
) -> list[dict[str, Any]]:
    inserted: list[dict[str, Any]] = []

    async def get_by_id(db: object, session_id: Any) -> ChargingSessionModel:
        return session

    async def insert_measurement(db: object, **kwargs: Any) -> None:
        inserted.append(kwargs)

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_measurement", insert_measurement)
    monkeypatch.setattr(charging_repository, "utc_now", lambda: NOW)
    return inserted


@pytest.mark.asyncio
async def test_ingest_meter_values_stores_the_energy_register_in_wh_with_its_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An energy sample becomes a measurement of the energy register, in Wh."""
    session = _active_session()
    inserted = _patch(monkeypatch, session)

    await charging_service.ingest_meter_values(
        object(),  # type: ignore[arg-type]
        session_id=session.session_id,
        sample=MeterSampleInput(
            sampled_at=NOW, value_wh=Decimal("1500"), context="Sample.Periodic"
        ),
    )

    assert inserted == [
        {
            "session_id": session.session_id,
            "sampled_at": NOW,
            "measurand": ENERGY_ACTIVE_IMPORT_REGISTER,
            "value": Decimal("1500"),
            "unit": ENERGY_UNIT_WH,
            "context": "Sample.Periodic",
        }
    ]
    assert session.meter_end_wh == Decimal("1500")  # the aggregate is still updated
    assert session.energy_delivered_wh == Decimal("500")


@pytest.mark.asyncio
async def test_ingest_meter_values_without_context_stores_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2.0.1 path passes no context; it defaults to None."""
    session = _active_session()
    inserted = _patch(monkeypatch, session)

    await charging_service.ingest_meter_values(
        object(),  # type: ignore[arg-type]
        session_id=session.session_id,
        sample=MeterSampleInput(sampled_at=NOW, value_wh=Decimal("1200")),
    )

    assert inserted[0]["context"] is None


def test_meter_value_response_contract_is_unchanged_by_the_unified_table() -> None:
    """``/meter-values`` still returns meter_value_id, sampled_at, session_id and value_wh."""
    measurement = ChargingSessionMeasurementModel(
        measurement_id=uuid4(),
        sampled_at=NOW,
        session_id=uuid4(),
        measurand=ENERGY_ACTIVE_IMPORT_REGISTER,
        value=Decimal("1250.500000"),
        unit="Wh",
        context=None,
        phase=None,
        location=None,
    )

    response = charging_service.to_charging_session_meter_value_response(measurement)

    assert isinstance(response, ChargingSessionMeterValueResponse)
    assert response.meter_value_id == measurement.measurement_id
    assert response.session_id == measurement.session_id
    assert response.sampled_at == NOW
    assert response.value_wh == Decimal("1250.500")
    assert str(response.value_wh) == "1250.500"  # same rendering as before the merge
    assert set(response.model_dump()) == {
        "meter_value_id",
        "sampled_at",
        "session_id",
        "value_wh",
    }
