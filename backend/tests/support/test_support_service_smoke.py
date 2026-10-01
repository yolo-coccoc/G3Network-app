"""Smoke tests for the support service: tickets, SOS and SLA (F-I1, F-I2)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
import app.domains.notifications.service as notifications_service
import app.domains.support.repository as support_repository
import app.domains.support.service as support_service
import app.domains.vehicles.service as vehicles_public_service
from app.domains.notifications.types import (
    NotificationReference,
    NotificationSeverity,
    NotificationType,
)
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
    SupportCaseListFilter,
    SupportCaseStatus,
    SupportCaseType,
)
from app.domains.vehicles.types import VehicleReference
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

    async def create_notification(
        db: AsyncSession, **kwargs: object
    ) -> NotificationReference:
        return NotificationReference(notification_id=1)

    monkeypatch.setattr(support_repository, "insert", insert)
    monkeypatch.setattr(
        notifications_service, "create_notification", create_notification
    )

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
async def test_create_support_sos_from_hotline_keeps_channel_and_raises_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hotline SOS (D12) keeps its channel and SOS SLA, needs no location,
    and raises one CRITICAL SOS_ALERT carrying the case context (F-I2)."""
    vehicle_id = uuid4()
    vin = "1HGBH41JXMN109186"
    captured_values: dict[str, object] = {}
    captured_notifications: list[dict[str, object]] = []

    async def resolve_vin(db: AsyncSession, vehicle_vin: str) -> VehicleReference:
        return VehicleReference(
            vehicle_id=vehicle_id, vin=vehicle_vin, battery_capacity_kwh=None
        )

    async def insert(db: AsyncSession, values: dict[str, object]) -> SupportCaseModel:
        captured_values.update(values)
        case_record = build_support_case_record(
            case_type=SupportCaseType.SOS,
            sla_response_minutes=settings.SUPPORT_SOS_RESPONSE_SLA_MINUTES,
        )
        case_record.channel = SupportCaseChannel.HOTLINE
        case_record.vehicle_id = vehicle_id
        case_record.vin = vin
        case_record.error_code = "E-042"
        return case_record

    async def create_notification(
        db: AsyncSession, **kwargs: object
    ) -> NotificationReference:
        captured_notifications.append(kwargs)
        return NotificationReference(notification_id=1)

    monkeypatch.setattr(
        vehicles_public_service, "resolve_vehicle_reference_by_vin", resolve_vin
    )
    monkeypatch.setattr(support_repository, "insert", insert)
    monkeypatch.setattr(
        notifications_service, "create_notification", create_notification
    )

    response = await support_service.create_support_sos(
        fake_db_session(),
        SupportSosCreateRequest.model_validate(
            {"vehicle_vin": vin, "channel": "HOTLINE", "error_code": "E-042"}
        ),
    )

    assert captured_values["channel"] == SupportCaseChannel.HOTLINE
    assert captured_values["location"] is None
    assert (
        captured_values["sla_response_minutes"]
        == settings.SUPPORT_SOS_RESPONSE_SLA_MINUTES
    )
    assert len(captured_notifications) == 1
    notification = captured_notifications[0]
    assert notification["notification_type"] is NotificationType.SOS_ALERT
    assert notification["severity"] is NotificationSeverity.CRITICAL
    assert notification["vehicle_id"] == vehicle_id
    payload = notification["payload"]
    assert isinstance(payload, dict)
    assert payload["case_id"] == str(response.case_id)
    assert payload["vehicle_id"] == str(vehicle_id)
    assert payload["vehicle_vin"] == vin
    assert payload["channel"] == "HOTLINE"
    assert payload["error_code"] == "E-042"
    assert payload["latitude"] is None
    assert payload["longitude"] is None


@pytest.mark.asyncio
async def test_list_support_cases_applies_the_same_filters_to_page_and_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every list filter reaches both the page and the count query, judged at
    one shared instant (F-I1/F-I2)."""
    driver_id = uuid4()
    seen: dict[str, tuple[object, object]] = {}

    async def list_all(
        db: AsyncSession, case_list_filter: object, **kwargs: object
    ) -> list[SupportCaseModel]:
        seen["list"] = (case_list_filter, kwargs["evaluated_at"])
        return []

    async def count(
        db: AsyncSession, case_list_filter: object, **kwargs: object
    ) -> int:
        seen["count"] = (case_list_filter, kwargs["evaluated_at"])
        return 0

    monkeypatch.setattr(support_repository, "list_all", list_all)
    monkeypatch.setattr(support_repository, "count", count)

    response = await support_service.list_support_cases(
        fake_db_session(),
        category_filter=SupportCaseCategory.CHARGING,
        channel_filter=SupportCaseChannel.ZALO,
        driver_id_filter=driver_id,
        awaiting_response_filter=True,
        sla_breached_filter=False,
    )

    assert response.total == 0
    assert seen["list"] == seen["count"]
    case_list_filter = seen["list"][0]
    assert case_list_filter == SupportCaseListFilter(
        category=SupportCaseCategory.CHARGING,
        channel=SupportCaseChannel.ZALO,
        driver_id=driver_id,
        is_awaiting_response=True,
        is_sla_breached=False,
    )


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


async def _capture_support_case_update(
    monkeypatch: pytest.MonkeyPatch,
    case_record: SupportCaseModel,
    new_status: SupportCaseStatus,
) -> dict[str, object]:
    """Run update_support_case to `new_status` and return the persisted values.

    Args:
        monkeypatch: Pytest fixture used to stub the repository.
        case_record: The case as stored before the update.
        new_status: The status the update moves the case to.

    Returns:
        The field values the service handed to `update_fields`.
    """
    captured: dict[str, object] = {}

    async def get_by_id(db: AsyncSession, case_id: UUID) -> SupportCaseModel:
        return case_record

    async def update_fields(
        db: AsyncSession, case_id: UUID, values: dict[str, object]
    ) -> SupportCaseModel:
        captured.update(values)
        return build_support_case_record(status=new_status)

    monkeypatch.setattr(support_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(support_repository, "update_fields", update_fields)
    await support_service.update_support_case(
        fake_db_session(),
        case_record.case_id,
        SupportCaseUpdateRequest(
            status=new_status, category=None, subject=None, description=None
        ),
    )
    return captured


@pytest.mark.asyncio
async def test_update_support_case_keeps_existing_first_response_time_on_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving an acknowledged case never moves its first-response time (F-I1).

    Regression: every non-OPEN update used to re-stamp first_responded_at,
    which made a late resolution look like a late first response.
    """
    responded_at = datetime.now(timezone.utc) - timedelta(hours=2)
    case_record = build_support_case_record(
        status=SupportCaseStatus.ACKNOWLEDGED, first_responded_at=responded_at
    )

    captured = await _capture_support_case_update(
        monkeypatch, case_record, SupportCaseStatus.RESOLVED
    )

    assert "first_responded_at" not in captured
    assert isinstance(captured["resolved_at"], datetime)


@pytest.mark.asyncio
async def test_update_support_case_keeps_existing_resolved_time_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing a resolved case keeps its resolution time and stamps closed_at (F-I1)."""
    now = datetime.now(timezone.utc)
    case_record = build_support_case_record(
        status=SupportCaseStatus.RESOLVED,
        first_responded_at=now - timedelta(hours=2),
        resolved_at=now - timedelta(hours=1),
    )

    captured = await _capture_support_case_update(
        monkeypatch, case_record, SupportCaseStatus.CLOSED
    )

    assert "first_responded_at" not in captured
    assert "resolved_at" not in captured
    assert isinstance(captured["closed_at"], datetime)


@pytest.mark.asyncio
async def test_update_support_case_cancel_is_not_recorded_as_a_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling an unanswered case stamps only closed_at (F-I1)."""
    case_record = build_support_case_record(status=SupportCaseStatus.OPEN)

    captured = await _capture_support_case_update(
        monkeypatch, case_record, SupportCaseStatus.CANCELLED
    )

    assert "first_responded_at" not in captured
    assert isinstance(captured["closed_at"], datetime)


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


def test_calculate_is_sla_breached_false_for_case_cancelled_before_due() -> None:
    """A case cancelled before its deadline never turns breached later (F-I1)."""
    now = datetime.now(timezone.utc)
    case_record = build_support_case_record(
        status=SupportCaseStatus.CANCELLED,
        response_due_at=now - timedelta(minutes=30),
        closed_at=now - timedelta(minutes=45),
    )

    assert support_service.calculate_is_sla_breached(case_record) is False


def test_calculate_is_sla_breached_true_for_case_cancelled_after_due() -> None:
    """A case cancelled only after its deadline had passed stays breached (F-I1)."""
    now = datetime.now(timezone.utc)
    case_record = build_support_case_record(
        status=SupportCaseStatus.CANCELLED,
        response_due_at=now - timedelta(minutes=30),
        closed_at=now - timedelta(minutes=5),
    )

    assert support_service.calculate_is_sla_breached(case_record) is True
