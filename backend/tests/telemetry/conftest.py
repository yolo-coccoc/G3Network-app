"""Fixtures shared by the telemetry smoke tests."""

import pytest


@pytest.fixture(autouse=True)
def _no_installed_battery(no_installed_battery: None) -> None:
    """Apply the shared "no battery fitted" fixture to every telemetry test."""
