"""Shared fixtures of the telematics smoke tests."""

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.repository as telematics_repository
from app.domains.telematics.models import TelematicStatusReportModel


@pytest.fixture(autouse=True)
def no_status_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every device look like it never sent a status report.

    ``build_telematic_response`` reads the newest status report (TX-11); the
    smoke tests use a placeholder session, so the lookup returns "none"
    instead of touching it.
    """

    async def find_no_report(
        db_session: AsyncSession, telematic_id: UUID
    ) -> TelematicStatusReportModel | None:
        return None

    monkeypatch.setattr(
        telematics_repository, "find_latest_status_report", find_no_report
    )
