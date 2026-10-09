"""Smoke tests for the driver request schemas and the drivers tables' models."""

from datetime import date
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, Index, Table

from app.domains.drivers.models import DriverModel, DrivingSessionModel, TripModel
from app.domains.drivers.schemas import (
    DriverCreateRequest,
    DrivingSessionCheckInRequest,
)
from app.domains.drivers.types import CheckInMethod, LicenseClass


def test_driver_create_request_validates_core_contract() -> None:
    """A driver request needs a membership and a known licence class (DR-09)."""
    driver = DriverCreateRequest(
        membership_id=uuid4(),
        license_number="LICENSE-001",
        license_class=LicenseClass.CE,
        license_expires_on=date(2030, 1, 1),
    )

    assert driver.license_number == "LICENSE-001"
    with pytest.raises(ValidationError):
        DriverCreateRequest(
            membership_id=uuid4(),
            license_number="LICENSE-001",
            license_class="Z9",  # type: ignore[arg-type]
            license_expires_on=date(2030, 1, 1),
        )


def test_check_in_request_rejects_malformed_vin() -> None:
    """Check-in enforces the same 17-character VIN length as vehicles (DR-07)."""
    DrivingSessionCheckInRequest(
        driver_id=uuid4(),
        vehicle_vin="1HGBH41JXMN109186",
        check_in_method=CheckInMethod.QR,
    )
    with pytest.raises(ValidationError):
        DrivingSessionCheckInRequest(
            driver_id=uuid4(),
            vehicle_vin="TOO-SHORT",
            check_in_method=CheckInMethod.QR,
        )


def test_drivers_tables_declare_their_partial_indexes_and_checks() -> None:
    """Open-session unique indexes and the DM-25 / DR-12 checks are in the models."""
    session_indexes = {
        index.name
        for index in cast(Table, DrivingSessionModel.__table__).indexes
        if isinstance(index, Index)
    }
    assert {
        "uq_driving_sessions_open_vehicle",
        "uq_driving_sessions_open_driver",
    } <= session_indexes
    driver_checks = {
        constraint.name
        for constraint in cast(Table, DriverModel.__table__).constraints
        if isinstance(constraint, CheckConstraint)
    }
    trip_checks = {
        constraint.name
        for constraint in cast(Table, TripModel.__table__).constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert driver_checks == {"ck_drivers_deleted_is_inactive"}
    assert trip_checks == {
        "ck_trips_planned_has_no_actuals",
        "ck_trips_started_has_session",
        "ck_trips_completed_has_end",
    }
