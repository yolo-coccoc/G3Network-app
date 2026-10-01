"""Smoke tests for the unified charging_session_measurements storage."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

import app.domains.charging_sessions.repository as charging_repository
import app.domains.charging_sessions.service as charging_service
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionNotFoundError,
    ChargingSessionStateError,
)
from app.domains.charging_sessions.models import (
    ChargingSessionMeasurementModel,
    ChargingSessionModel,
)
from app.domains.charging_sessions.schemas import ChargingSessionMeterValueResponse
from app.domains.charging_sessions.types import (
    ENERGY_ACTIVE_IMPORT_REGISTER,
    ENERGY_UNIT_WH,
    MeasurementInput,
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


# --- ingest_measurements (non-energy samples) ----------------------------------------------


def _patch_for_measurements(
    monkeypatch: pytest.MonkeyPatch, session: ChargingSessionModel | None
) -> list[dict[str, Any]]:
    inserted: list[dict[str, Any]] = []

    async def get_by_id(db: object, session_id: Any) -> Any:
        return session

    async def insert_measurement(db: object, **kwargs: Any) -> None:
        inserted.append(kwargs)

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "insert_measurement", insert_measurement)
    monkeypatch.setattr(charging_repository, "utc_now", lambda: NOW)
    return inserted


@pytest.mark.asyncio
async def test_ingest_measurements_stores_every_sample_without_touching_the_energy_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SoC/power/voltage… are stored one by one; only the energy register drives the total."""
    session = _active_session()
    inserted = _patch_for_measurements(monkeypatch, session)
    samples = [
        MeasurementInput(
            sampled_at=NOW, measurand="SoC", value=Decimal(0), unit="Percent"
        ),
        MeasurementInput(
            sampled_at=NOW,
            measurand="Temperature",
            value=Decimal("-4.5"),  # negative values are legitimate
            unit="Celsius",
            context="Sample.Periodic",
            phase="L1-N",
            location="Cable",
        ),
        MeasurementInput(
            sampled_at=NOW, measurand="Voltage.Demand", value=Decimal(600)
        ),
    ]

    stored = await charging_service.ingest_measurements(
        object(),  # type: ignore[arg-type]
        session_id=session.session_id,
        samples=samples,
    )

    assert stored == 3
    assert [row["measurand"] for row in inserted] == [
        "SoC",
        "Temperature",
        "Voltage.Demand",
    ]
    assert inserted[1]["value"] == Decimal("-4.5")
    assert (inserted[1]["phase"], inserted[1]["location"]) == ("L1-N", "Cable")
    assert session.meter_end_wh is None  # the energy total is untouched
    assert session.energy_delivered_wh is None


@pytest.mark.asyncio
async def test_ingest_measurements_refuses_a_completed_or_unknown_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMPLETED is terminal, and a missing session is an error."""
    completed = _active_session()
    completed.status = SessionStatus.COMPLETED
    inserted = _patch_for_measurements(monkeypatch, completed)
    sample = MeasurementInput(sampled_at=NOW, measurand="SoC", value=Decimal(1))

    with pytest.raises(ChargingSessionStateError):
        await charging_service.ingest_measurements(
            object(), session_id=completed.session_id, samples=[sample]  # type: ignore[arg-type]
        )
    _patch_for_measurements(monkeypatch, None)
    with pytest.raises(ChargingSessionNotFoundError):
        await charging_service.ingest_measurements(
            object(), session_id=uuid4(), samples=[sample]  # type: ignore[arg-type]
        )

    assert inserted == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sample",
    [
        MeasurementInput(sampled_at=NOW, measurand="", value=Decimal(1)),
        MeasurementInput(sampled_at=NOW, measurand="X" * 61, value=Decimal(1)),
        MeasurementInput(
            sampled_at=NOW, measurand="SoC", value=Decimal(1), unit="U" * 21
        ),
        MeasurementInput(
            sampled_at=NOW, measurand="SoC", value=Decimal(1), context="c" * 31
        ),
        MeasurementInput(
            sampled_at=NOW, measurand="SoC", value=Decimal(1), phase="p" * 11
        ),
        MeasurementInput(
            sampled_at=NOW, measurand="SoC", value=Decimal(1), location="l" * 21
        ),
        MeasurementInput(sampled_at=NOW, measurand="SoC", value=Decimal("NaN")),
        MeasurementInput(
            sampled_at=NOW.replace(tzinfo=None), measurand="SoC", value=Decimal(1)
        ),
    ],
)
async def test_ingest_measurements_rejects_out_of_contract_samples(
    monkeypatch: pytest.MonkeyPatch, sample: MeasurementInput
) -> None:
    """Over-long fields, a non-finite value or a naive timestamp are refused."""
    session = _active_session()
    _patch_for_measurements(monkeypatch, session)

    with pytest.raises(ChargingSessionInputError):
        await charging_service.ingest_measurements(
            object(), session_id=session.session_id, samples=[sample]  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_list_measurements_passes_the_measurand_filter_and_paginates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read API filters by measurand and reports the total for that filter."""
    session = _active_session()
    seen: dict[str, Any] = {}
    rows = [
        ChargingSessionMeasurementModel(
            measurement_id=uuid4(),
            sampled_at=NOW,
            session_id=session.session_id,
            measurand="SoC",
            value=Decimal(80),
            unit="Percent",
            context=None,
            phase=None,
            location=None,
        )
    ]

    async def get_by_id(db: object, session_id: Any) -> Any:
        return session

    async def list_rows(db: object, session_id: Any, **kwargs: Any) -> Any:
        seen["list"] = kwargs
        return rows

    async def count_rows(db: object, session_id: Any, **kwargs: Any) -> int:
        seen["count"] = kwargs
        return 1

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)
    monkeypatch.setattr(charging_repository, "list_session_measurements", list_rows)
    monkeypatch.setattr(charging_repository, "count_session_measurements", count_rows)

    response = await charging_service.list_charging_session_measurements(
        object(),  # type: ignore[arg-type]
        session.session_id,
        measurand="SoC",
        page=1,
        page_size=10,
    )

    assert seen["list"]["measurand"] == seen["count"]["measurand"] == "SoC"
    assert (response.total, response.page, response.page_size) == (1, 1, 10)
    assert (
        response.items[0].measurand,
        response.items[0].value,
        response.items[0].unit,
    ) == (
        "SoC",
        Decimal(80),
        "Percent",
    )
    assert response.items[0].session_id == session.session_id


@pytest.mark.asyncio
async def test_list_measurements_for_an_unknown_session_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint's 404 comes from this domain error."""

    async def get_by_id(db: object, session_id: Any) -> None:
        return None

    monkeypatch.setattr(charging_repository, "get_session_by_id", get_by_id)

    with pytest.raises(ChargingSessionNotFoundError):
        await charging_service.list_charging_session_measurements(
            object(),  # type: ignore[arg-type]
            uuid4(),
            measurand=None,
            page=1,
            page_size=10,
        )
