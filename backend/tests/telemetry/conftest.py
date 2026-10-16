"""Fixtures shared by the telemetry smoke tests."""

import pytest

import app.domains.identity.service as identity_service


@pytest.fixture(autouse=True)
def _no_installed_battery(no_installed_battery: None) -> None:
    """Apply the shared "no battery fitted" fixture to every telemetry test."""


@pytest.fixture(autouse=True)
def _skip_data_access_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the personal-data audit row the live and history reads write.

    The smoke tests run on a placeholder session that cannot insert rows; the
    audit row itself belongs to identity (ACC-18) and is not under test here.

    Args:
        monkeypatch: Pytest's patching fixture.
    """

    async def skip_data_access_row(*_args: object, **_kwargs: object) -> None:
        """Accept the audit call and write nothing."""

    monkeypatch.setattr(identity_service, "record_data_access", skip_data_access_row)
