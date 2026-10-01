"""Smoke tests for the telemetry HTTP contract (F-A1, F-A3, F-A6).

Kept beside the domain instead of in ``tests/test_api_smoke.py`` so the
telemetry routes, parameters and the CSV response are asserted in one place.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telemetry.router as telemetry_router
import app.domains.telemetry.service as telemetry_service
from app.api.main import app
from app.domains.telemetry.schemas import VehicleOperatingReportResponse
from app.domains.telemetry.types import ReportFormat, ReportGranularity
from tests.builders import fake_db_session

_PREFIX = "/api/v1/telemetry/vehicles/{vehicle_id}"


def _query_parameters(path: str) -> dict[str, dict[str, Any]]:
    """Return a GET route's query parameters from the OpenAPI document.

    Args:
        path: Full OpenAPI path.

    Returns:
        Parameter objects keyed by name.
    """
    operation = app.openapi()["paths"][path]["get"]
    return {
        parameter["name"]: parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "query"
    }


def test_battery_health_route_takes_a_time_window() -> None:
    """F-A3: the battery-health trend is registered with start/end_time."""
    parameters = _query_parameters(f"{_PREFIX}/battery-health")

    assert parameters["start_time"]["required"] is True
    assert parameters["end_time"]["required"] is True


def test_operating_report_accepts_optional_granularity_and_format() -> None:
    """F-A6: granularity and format are optional, so the old call still works."""
    parameters = _query_parameters(f"{_PREFIX}/operating-report")

    assert parameters["granularity"].get("required", False) is False
    assert parameters["format"].get("required", False) is False
    schemas = app.openapi()["components"]["schemas"]
    assert schemas["ReportGranularity"]["enum"] == ["day", "week", "month"]
    assert schemas["ReportFormat"]["enum"] == ["json", "csv"]


def test_latest_response_documents_received_at_and_is_online() -> None:
    """F-A1: the latest-telemetry response adds received_at and is_online."""
    properties = app.openapi()["components"]["schemas"][
        "VehicleTelemetryLatestResponse"
    ]["properties"]

    assert "received_at" in properties
    assert "is_online" in properties


def _operating_report() -> VehicleOperatingReportResponse:
    """Build a minimal whole-window operating report.

    Returns:
        A report with zero sums and no breakdown.
    """
    return VehicleOperatingReportResponse(
        vehicle_id=uuid4(),
        start_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 2, tzinfo=timezone.utc),
        sample_count=0,
        odometer_sample_count=0,
        distance_km=0.0,
        energy_consumed_kwh=0.0,
        energy_per_100km_kwh=None,
        distance_per_day_km=None,
        energy_cost_vnd=0.0,
        cost_per_km_vnd=None,
        battery_capacity_kwh=75.0,
        is_default_battery_capacity=True,
        cost_per_kwh_vnd=3000.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("report_format", [ReportFormat.JSON, ReportFormat.CSV])
async def test_operating_report_endpoint_returns_json_or_csv(
    monkeypatch: pytest.MonkeyPatch, report_format: ReportFormat
) -> None:
    """format=csv returns a text/csv attachment; json returns the model."""
    operating_report = _operating_report()
    captured_kwargs: dict[str, object] = {}

    async def get_report(
        db: AsyncSession, **kwargs: object
    ) -> VehicleOperatingReportResponse:
        captured_kwargs.update(kwargs)
        return operating_report

    monkeypatch.setattr(telemetry_service, "get_vehicle_operating_report", get_report)

    endpoint_result = await telemetry_router.get_vehicle_operating_report_endpoint(
        operating_report.vehicle_id,
        operating_report.start_time,
        operating_report.end_time,
        granularity=ReportGranularity.WEEK,
        report_format=report_format,
        db=fake_db_session(),
    )

    assert captured_kwargs["granularity"] is ReportGranularity.WEEK
    if report_format is ReportFormat.JSON:
        assert endpoint_result is operating_report
        return
    assert isinstance(endpoint_result, Response)
    assert endpoint_result.media_type == "text/csv"
    assert "attachment" in endpoint_result.headers["content-disposition"]
    assert bytes(endpoint_result.body).decode().startswith("vehicle_id,period_start")
