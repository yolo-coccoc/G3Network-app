"""Smoke tests for the support service: tickets, SOS and SLA (F-I1, F-I2)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
import app.domains.support.repository as support_repository
import app.domains.support.service as support_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.support.exceptions import (
    SupportCaseNotFoundError,
    SupportCaseStateError,
    SupportDriverNotFoundError,
    SupportVehicleNotFoundError,
)
from app.domains.support.models import SupportCaseModel
from app.domains.support.schemas import (
    SupportCaseUpdateRequest,
    SupportSosCreateRequest,
    SupportTicketCreateRequest,
)
from app.domains.support.types import (
    SupportCaseCategory,
    SupportCaseChannel,
    SupportCaseStatus,
    SupportCaseType,
)
from app.libs.common.config import settings
from tests.builders import build_support_case_record, fake_db_session


@pytest.mark.asyncio
async def test_create_support_ticket_creates_open_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_support_ticket() opens a case with the ticket SLA (F-I1)."""
    inserted = build_support_case_record(
        case_type=SupportCaseType.TICKET,
        sla_response_minutes=settings.SUPPORT_TICKET_RESPONSE_SLA_MINUTES,
    )

    async def insert(db: AsyncSession, values: dict[str, object]) -> SupportCaseModel:
        assert values["case_type"] == SupportCaseType.TICKET
        assert (
            values["sla_response_minutes"]
            == settings.SUPPORT_TICKET_RESPONSE_SLA_MINUTES
        )
        return inserted

    monkeypatch.setattr(support_repository, "insert", insert)

    response = await support_service.create_support_ticket(
        fake_db_session(),
        SupportTicketCreateRequest(
            vehicle_vin=None,
            driver_id=None,
            category=SupportCaseCategory.TECHNICAL,
            subject="App crashes on login",
            description=None,
            error_code=None,
            latitude=None,
            longitude=None,
        ),
    )

    assert response.case_id == inserted.case_id
    assert response.case_type == SupportCaseType.TICKET
    assert response.is_sla_breached is False


@pytest.mark.asyncio
async def test_create_support_ticket_rejects_unknown_vin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_support_ticket() raises when the VIN doesn't resolve to a vehicle (F-I1)."""

    async def no_vehicle(db: AsyncSession, vin: str) -> None:
        return None

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", no_vehicle
    )

    with pytest.raises(SupportVehicleNotFoundError):
        await support_service.create_support_ticket(
            fake_db_session(),
            SupportTicketCreateRequest(
                vehicle_vin="1HGBH41JXMN109186",
                driver_id=None,
                category=SupportCaseCategory.TECHNICAL,
                subject="App crashes on login",
                description=None,
                error_code=None,
                latitude=None,
                longitude=None,
            ),
        )


@pytest.mark.asyncio
async def test_create_support_ticket_rejects_unknown_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_support_ticket() raises when the driver ID doesn't resolve (F-I1)."""

    async def no_driver(db: AsyncSession, driver_id: UUID) -> None:
        return None

    monkeypatch.setattr(driver_service, "resolve_driver_reference_by_id", no_driver)

    with pytest.raises(SupportDriverNotFoundError):
        await support_service.create_support_ticket(
            fake_db_session(),
            SupportTicketCreateRequest(
                vehicle_vin=None,
                driver_id=uuid4(),
                category=SupportCaseCategory.TECHNICAL,
                subject="App crashes on login",
                description=None,
                error_code=None,
                latitude=None,
                longitude=None,
            ),
        )


@pytest.mark.asyncio
async def test_create_support_sos_uses_sos_sla_and_autofills_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_support_sos() uses the <=5 minute SOS SLA and fills a subject (F-I2)."""
    captured: dict[str, object] = {}

    async def insert(db: AsyncSession, values: dict[str, object]) -> SupportCaseModel:
        captured.update(values)
        return build_support_case_record(
            case_type=SupportCaseType.SOS,
            sla_response_minutes=settings.SUPPORT_SOS_RESPONSE_SLA_MINUTES,
        )

    monkeypatch.setattr(support_repository, "insert", insert)

    await support_service.create_support_sos(
        fake_db_session(),
        SupportSosCreateRequest(
            vehicle_vin=None,
            driver_id=None,
            category=SupportCaseCategory.BREAKDOWN,
            description=None,
            error_code=None,
            latitude=10.8,
            longitude=106.7,
        ),
    )

    assert captured["case_type"] == SupportCaseType.SOS
    assert captured["channel"] == SupportCaseChannel.IN_APP
    assert captured["sla_response_minutes"] == settings.SUPPORT_SOS_RESPONSE_SLA_MINUTES
    assert captured["subject"] == "SOS - BREAKDOWN"


@pytest.mark.asyncio
async def test_update_support_case_sets_first_responded_at_on_acknowledge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving a case to ACKNOWLEDGED stamps first_responded_at once (F-I1)."""
    case_record = build_support_case_record(
        status=SupportCaseStatus.OPEN, first_responded_at=None
    )
    captured: dict[str, object] = {}

    async def get_by_id(db: AsyncSession, case_id: UUID) -> SupportCaseModel:
        return case_record

    async def update_fields(
        db: AsyncSession, case_id: UUID, values: dict[str, object]
    ) -> SupportCaseModel:
        captured.update(values)
        return build_support_case_record(status=SupportCaseStatus.ACKNOWLEDGED)

    monkeypatch.setattr(support_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(support_repository, "update_fields", update_fields)

    await support_service.update_support_case(
        fake_db_session(),
        case_record.case_id,
        SupportCaseUpdateRequest(
            status=SupportCaseStatus.ACKNOWLEDGED,
            category=None,
            subject=None,
            description=None,
        ),
    )

    assert captured["status"] == SupportCaseStatus.ACKNOWLEDGED
    assert isinstance(captured["first_responded_at"], datetime)


@pytest.mark.asyncio
async def test_update_support_case_rejects_when_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLOSED support case refuses any further update (F-I1)."""
    case_record = build_support_case_record(status=SupportCaseStatus.CLOSED)

    async def get_by_id(db: AsyncSession, case_id: UUID) -> SupportCaseModel:
        return case_record

    monkeypatch.setattr(support_repository, "get_by_id", get_by_id)

    with pytest.raises(SupportCaseStateError):
        await support_service.update_support_case(
            fake_db_session(),
            case_record.case_id,
            SupportCaseUpdateRequest(
                status=SupportCaseStatus.RESOLVED,
                category=None,
                subject=None,
                description=None,
            ),
        )


def test_calculate_is_sla_breached_true_when_past_due_without_response() -> None:
    """A case past its response deadline with no response yet is breached (F-I1/F-I2)."""
    now = datetime.now(timezone.utc)
    case_record = build_support_case_record(response_due_at=now - timedelta(minutes=1))

    assert support_service.calculate_is_sla_breached(case_record) is True


def test_calculate_is_sla_breached_false_when_responded_before_due() -> None:
    """A case responded to before its deadline is not breached (F-I1/F-I2)."""
    now = datetime.now(timezone.utc)
    case_record = build_support_case_record(
        response_due_at=now + timedelta(minutes=10),
        first_responded_at=now,
    )

    assert support_service.calculate_is_sla_breached(case_record) is False


@pytest.mark.asyncio
async def test_get_support_case_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_support_case() raises for an unknown case ID (F-I1/F-I2)."""

    async def no_case(db: AsyncSession, case_id: UUID) -> None:
        return None

    monkeypatch.setattr(support_repository, "get_by_id", no_case)

    with pytest.raises(SupportCaseNotFoundError):
        await support_service.get_support_case(fake_db_session(), uuid4())
