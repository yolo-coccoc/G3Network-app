"""Smoke tests for the support case request schemas."""

import pytest
from pydantic import ValidationError

from app.domains.support.schemas import (
    SupportSosCreateRequest,
    SupportTicketCreateRequest,
)
from app.domains.support.types import SupportCaseCategory, SupportCaseChannel


def test_support_ticket_create_request_requires_coordinates_together() -> None:
    """F-I1's ticket request rejects a lone latitude/longitude value."""
    SupportTicketCreateRequest(
        organization_id=None,
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
            organization_id=None,
            vehicle_vin=None,
            driver_id=None,
            category=SupportCaseCategory.TECHNICAL,
            subject="App crashes on login",
            description=None,
            error_code=None,
            latitude=10.8,
            longitude=None,
        )


def test_support_sos_create_request_requires_coordinates_for_in_app() -> None:
    """An in-app SOS (the default channel) requires a location (F-I2, D12)."""
    sos_request = SupportSosCreateRequest(
        organization_id=None,
        vehicle_vin=None,
        driver_id=None,
        description=None,
        error_code=None,
        latitude=10.8,
        longitude=106.7,
    )
    assert sos_request.channel is SupportCaseChannel.IN_APP
    with pytest.raises(ValidationError):
        SupportSosCreateRequest.model_validate({})


@pytest.mark.parametrize("channel", ["HOTLINE", "ZALO"])
def test_support_sos_create_request_allows_no_location_off_app(channel: str) -> None:
    """An SOS logged from a hotline call or Zalo may omit the location (D12)."""
    sos_request = SupportSosCreateRequest.model_validate({"channel": channel})

    assert sos_request.latitude is None
    assert sos_request.longitude is None


def test_support_sos_create_request_still_requires_coordinates_together() -> None:
    """Off-app, a given location still needs both coordinates (D12)."""
    with pytest.raises(ValidationError):
        SupportSosCreateRequest.model_validate({"channel": "HOTLINE", "latitude": 10.8})
