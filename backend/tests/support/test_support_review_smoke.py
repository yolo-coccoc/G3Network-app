"""Review smoke tests for who a support case is attributed to (RV-OP13).

The `xfail(strict=True)` test asserts the correct behaviour that a reviewed
defect breaks today; once fixed it passes, strict mode reports it and the
marker must be removed. The guard checks a rule that already holds.

Nothing touches a database: repositories and other domains' services are
monkeypatched.
"""

from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
import app.domains.notifications.service as notifications_service
import app.domains.support.repository as support_repository
import app.domains.support.service as support_service
from app.domains.drivers.types import DriverReference
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import UserRole
from app.domains.notifications.types import NotificationReference
from app.domains.support.exceptions import SupportDriverNotFoundError
from app.domains.support.models import SupportCaseModel
from app.domains.support.schemas import SupportSosCreateRequest
from app.domains.support.types import (
    SupportCaseCategory,
    SupportCaseChannel,
    SupportCaseType,
)
from app.libs.common.config import settings
from tests.builders import build_support_case_record, fake_db_session
from tests.principals import build_principal

pytestmark = pytest.mark.usefixtures("own_organization_for_new_records")

OWN_DRIVER = DriverReference(driver_id=uuid4(), full_name="Own Driver")
COLLEAGUE = DriverReference(driver_id=uuid4(), full_name="Colleague")


def _patch_case_writes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Resolve both drivers, record the inserted case and swallow the SOS alert.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The values of every inserted case, in order.
    """
    inserted: list[dict[str, object]] = []

    async def own_driver(*_args: object, **_kwargs: object) -> DriverReference:
        return OWN_DRIVER

    async def any_driver(
        db: AsyncSession, driver_id: UUID, **_scope: object
    ) -> DriverReference | None:
        return {
            OWN_DRIVER.driver_id: OWN_DRIVER,
            COLLEAGUE.driver_id: COLLEAGUE,
        }.get(driver_id)

    async def insert(db: AsyncSession, values: dict[str, object]) -> SupportCaseModel:
        inserted.append(values)
        return build_support_case_record(
            case_type=SupportCaseType.SOS,
            sla_response_minutes=settings.SUPPORT_SOS_RESPONSE_SLA_MINUTES,
            driver_id=cast(UUID | None, values["driver_id"]),
            organization_id=cast(UUID, values["organization_id"]),
        )

    async def create_notification(
        *_args: object, **_kwargs: object
    ) -> NotificationReference:
        return NotificationReference(notification_id=1)

    monkeypatch.setattr(driver_service, "resolve_own_driver_reference", own_driver)
    monkeypatch.setattr(driver_service, "resolve_driver_reference_by_id", any_driver)
    monkeypatch.setattr(support_repository, "insert", insert)
    monkeypatch.setattr(
        notifications_service, "create_notification", create_notification
    )
    return inserted


def _sos_request(driver_id: UUID | None) -> SupportSosCreateRequest:
    """Build an in-app SOS with a location and, optionally, a named driver.

    Args:
        driver_id: The driver the request names, or ``None``.

    Returns:
        The validated request.
    """
    return SupportSosCreateRequest(
        organization_id=None,
        vehicle_vin=None,
        driver_id=driver_id,
        category=SupportCaseCategory.ACCIDENT,
        channel=SupportCaseChannel.IN_APP,
        description=None,
        error_code=None,
        latitude=10.8,
        longitude=106.7,
    )


@pytest.mark.asyncio
async def test_driver_only_caller_cannot_file_an_sos_in_a_colleagues_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DRIVER-only caller's case is never attributed to another driver.

    Either the request is refused, or the case records the caller's own
    profile; it must not carry the colleague's ID.
    """
    inserted = _patch_case_writes(monkeypatch)
    driver = build_principal(roles=frozenset({UserRole.DRIVER}))

    try:
        await support_service.create_support_sos(
            fake_db_session(), _sos_request(COLLEAGUE.driver_id), principal=driver
        )
    except (AccessDeniedError, SupportDriverNotFoundError):
        return
    assert inserted[0]["driver_id"] != COLLEAGUE.driver_id


@pytest.mark.asyncio
async def test_driver_only_caller_sos_without_driver_records_their_own_profile_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: an SOS that names no driver records the caller's own profile."""
    inserted = _patch_case_writes(monkeypatch)
    driver = build_principal(roles=frozenset({UserRole.DRIVER}))

    await support_service.create_support_sos(
        fake_db_session(), _sos_request(None), principal=driver
    )

    assert inserted[0]["driver_id"] == OWN_DRIVER.driver_id
