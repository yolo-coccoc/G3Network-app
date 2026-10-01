"""Smoke tests for the support case request schemas."""

import pytest
from pydantic import ValidationError

from app.domains.support.schemas import (
    SupportSosCreateRequest,
    SupportTicketCreateRequest,
)
from app.domains.support.types import SupportCaseCategory


def test_support_ticket_create_request_requires_coordinates_together() -> None:
    """F-I1's ticket request rejects a lone latitude/longitude value."""
    SupportTicketCreateRequest(
        vehicle_vin=None,
        driver_id=None,
        category=SupportCaseCategory.TECHNICAL,
        subject="App crashes on login",
        description=None,
        error_code=None,
        latitude=None,
        longitude=None,
    )
    with pytest.raises(ValidationError):
        SupportTicketCreateRequest(
            vehicle_vin=None,
            driver_id=None,
            category=SupportCaseCategory.TECHNICAL,
            subject="App crashes on login",
            description=None,
            error_code=None,
            latitude=10.8,
            longitude=None,
        )


def test_support_sos_create_request_requires_coordinates() -> None:
    """F-I2's SOS request requires a location, unlike the optional one on a ticket."""
    SupportSosCreateRequest(
        vehicle_vin=None,
        driver_id=None,
        description=None,
        error_code=None,
        latitude=10.8,
        longitude=106.7,
    )
    with pytest.raises(ValidationError):
        SupportSosCreateRequest()  # type: ignore[call-arg]
