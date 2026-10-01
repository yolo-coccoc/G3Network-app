"""Smoke tests for the telematic config-push request schema (F-J2)."""

import pytest
from pydantic import ValidationError

from app.domains.telematics.schemas import (
    TelematicConfigPushRequest,
)
from app.libs.common.config import settings


def test_telematic_config_push_request_rejects_interval_outside_bounds() -> None:
    """F-J2's requested telemetry interval must stay within the configured bounds."""
    TelematicConfigPushRequest(
        telemetry_interval_seconds=settings.TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS
    )
    TelematicConfigPushRequest(
        telemetry_interval_seconds=settings.TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS
    )
    with pytest.raises(ValidationError):
        TelematicConfigPushRequest(
            telemetry_interval_seconds=(
                settings.TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS - 1
            )
        )
    with pytest.raises(ValidationError):
        TelematicConfigPushRequest(
            telemetry_interval_seconds=(
                settings.TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS + 1
            )
        )
