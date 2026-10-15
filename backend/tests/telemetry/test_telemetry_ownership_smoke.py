"""Smoke tests for telemetry reads after a truck sale (VH-11, bug-fix planner D1).

Rows recorded under the previous owner stay with that owner: a customer reads
only its own organization's rows, a former owner still reads its own period's
history and reports, the live view is the current owner's only, and the
battery-health trend follows the truck. The vehicles and fleet domains are
monkeypatched at their public services, the telemetry repository at its
functions; the SQL tests compile the statement a repository function sends.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
import app.domains.fleet.service as fleet_service
import app.domains.telematics.service as telematics_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
import app.domains.vehicles.service as vehicle_service
from app.domains.identity.types import UserRole
from app.domains.telemetry.exceptions import TelemetryNotFoundError
from app.domains.telemetry.models import TelemetryModel
from app.domains.telemetry.types import (
    VehicleBatteryHealthDay,
    VehicleTelemetryWindowSummary,
)
from app.domains.vehicles.types import VehicleReference
from tests.builders import build_telemetry_record, fake_db_session
from tests.principals import (
    DEFAULT_ORGANIZATION_ID,
    OTHER_ORGANIZATION_ID,
    build_internal_principal,
    build_principal,
)

_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
_END = datetime(2026, 9, 2, tzinfo=timezone.utc)

_EMPTY_SUMMARY = VehicleTelemetryWindowSummary(
    soc_discharge_percent=0.0,
    soc_charge_percent=0.0,
    distance_km=0.0,
    sample_count=0,
    odometer_sample_count=0,
    first_recorded_at=None,
    last_recorded_at=None,
)


def _patch_vehicle(
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner_organization_id: UUID,
    former_owner_ids: frozenset[UUID] = frozenset(),
) -> list[UUID]:
    """Make the vehicles domain resolve one truck with a given owner history.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        owner_organization_id: The truck's current owner.
        former_owner_ids: Organizations that owned the truck before.

    Returns:
        The organizations the ownership lookup was asked about, in call order.
    """
    ownership_lookups: list[UUID] = []

    async def resolve_reference(db: AsyncSession, vehicle_id: UUID) -> VehicleReference:
        return VehicleReference(
            organization_id=owner_organization_id,
            vehicle_id=vehicle_id,
            vin="1HGBH41JXMN109186",
            battery_capacity_kwh=None,
        )

    async def has_owned(
        db: AsyncSession, vehicle_id: UUID, *, organization_id: UUID
    ) -> bool:
        ownership_lookups.append(organization_id)
        return (
            organization_id == owner_organization_id
            or organization_id in former_owner_ids
        )

    monkeypatch.setattr(
        vehicle_service, "resolve_vehicle_reference_by_id", resolve_reference
    )
    monkeypatch.setattr(vehicle_service, "has_organization_owned_vehicle", has_owned)
    return ownership_lookups


def _patch_history(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the arguments of every history query and return no rows.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The keyword arguments of each call, in call order.
    """
    calls: list[dict[str, Any]] = []

    async def history(db: AsyncSession, **kwargs: Any) -> list[TelemetryModel]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(telemetry_repository, "get_vehicle_telemetry_history", history)
    return calls


def _patch_latest(
    monkeypatch: pytest.MonkeyPatch, telemetry: TelemetryModel | None
) -> list[UUID | None]:
    """Make the latest-reading queries return a given row and record the filter.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        telemetry: Row returned as the newest reading, or `None`.

    Returns:
        The ``organization_id`` filter of each latest-reading query.
    """
    filters: list[UUID | None] = []

    async def latest_reading(
        db: AsyncSession, vehicle_id: UUID, *, organization_id: UUID | None = None
    ) -> TelemetryModel | None:
        filters.append(organization_id)
        return telemetry

    async def latest_received_at(
        db: AsyncSession, vehicle_id: UUID, *, organization_id: UUID | None = None
    ) -> datetime | None:
        return telemetry.received_at if telemetry is not None else None

    async def resolve_serial(db: AsyncSession, telematic_id: UUID) -> str:
        return "TBOX-TEST-001"

    monkeypatch.setattr(
        telemetry_repository, "get_latest_vehicle_telemetry", latest_reading
    )
    monkeypatch.setattr(
        telemetry_repository, "find_latest_received_at", latest_received_at
    )
    monkeypatch.setattr(telematics_service, "resolve_serial_by_id", resolve_serial)
    return filters


async def _read_history(principal_organization_id: UUID | None) -> None:
    """Call the history service as a customer ORG_ADMIN or as internal staff.

    Args:
        principal_organization_id: The customer's organization, or `None` for
            an internal caller.
    """
    principal = (
        build_internal_principal()
        if principal_organization_id is None
        else build_principal(organization_id=principal_organization_id)
    )
    await telemetry_service.get_vehicle_telemetry_history_response(
        fake_db_session(),
        vehicle_id=uuid4(),
        start_time=_START,
        end_time=_END,
        principal=principal,
    )


@pytest.mark.asyncio
async def test_customer_history_reads_only_its_own_organization_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The current owner's history query carries its organization filter (VH-11)."""
    _patch_vehicle(monkeypatch, owner_organization_id=DEFAULT_ORGANIZATION_ID)
    calls = _patch_history(monkeypatch)

    await _read_history(DEFAULT_ORGANIZATION_ID)

    assert [call["organization_id"] for call in calls] == [DEFAULT_ORGANIZATION_ID]


@pytest.mark.asyncio
async def test_internal_staff_history_reads_every_owner_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal staff carry no organization filter on the history query."""
    _patch_vehicle(monkeypatch, owner_organization_id=DEFAULT_ORGANIZATION_ID)
    calls = _patch_history(monkeypatch)

    await _read_history(None)

    assert [call["organization_id"] for call in calls] == [None]


@pytest.mark.asyncio
async def test_former_owner_reads_history_of_its_own_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seller passes the access check for history and reads only its own rows."""
    ownership_lookups = _patch_vehicle(
        monkeypatch,
        owner_organization_id=OTHER_ORGANIZATION_ID,
        former_owner_ids=frozenset({DEFAULT_ORGANIZATION_ID}),
    )
    calls = _patch_history(monkeypatch)

    await _read_history(DEFAULT_ORGANIZATION_ID)

    assert ownership_lookups == [DEFAULT_ORGANIZATION_ID]
    assert [call["organization_id"] for call in calls] == [DEFAULT_ORGANIZATION_ID]


@pytest.mark.asyncio
async def test_organization_that_never_owned_the_truck_gets_not_found_for_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A customer outside the truck's owner history still gets 404."""
    _patch_vehicle(monkeypatch, owner_organization_id=OTHER_ORGANIZATION_ID)
    calls = _patch_history(monkeypatch)

    with pytest.raises(TelemetryNotFoundError):
        await _read_history(DEFAULT_ORGANIZATION_ID)

    assert calls == []


@pytest.mark.asyncio
async def test_former_owner_gets_not_found_for_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live view is the current owner's only: a seller gets 404 on /latest."""
    ownership_lookups = _patch_vehicle(
        monkeypatch,
        owner_organization_id=OTHER_ORGANIZATION_ID,
        former_owner_ids=frozenset({DEFAULT_ORGANIZATION_ID}),
    )
    filters = _patch_latest(
        monkeypatch, build_telemetry_record(vehicle_id=uuid4(), recorded_at=_START)
    )

    with pytest.raises(TelemetryNotFoundError):
        await telemetry_service.get_latest_vehicle_telemetry_response(
            fake_db_session(),
            uuid4(),
            principal=build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        )

    assert ownership_lookups == []
    assert filters == []


@pytest.mark.asyncio
async def test_buyer_without_own_reading_gets_not_found_for_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The buyer's /latest reads only its own rows: none yet means 404."""
    _patch_vehicle(monkeypatch, owner_organization_id=DEFAULT_ORGANIZATION_ID)
    filters = _patch_latest(monkeypatch, None)

    with pytest.raises(TelemetryNotFoundError):
        await telemetry_service.get_latest_vehicle_telemetry_response(
            fake_db_session(),
            uuid4(),
            principal=build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        )

    assert filters == [DEFAULT_ORGANIZATION_ID]


@pytest.mark.asyncio
async def test_internal_staff_latest_reads_every_owner_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal staff carry no organization filter on /latest."""
    vehicle_id = uuid4()
    _patch_vehicle(monkeypatch, owner_organization_id=DEFAULT_ORGANIZATION_ID)
    filters = _patch_latest(
        monkeypatch, build_telemetry_record(vehicle_id=vehicle_id, recorded_at=_START)
    )

    response = await telemetry_service.get_latest_vehicle_telemetry_response(
        fake_db_session(), vehicle_id, principal=build_internal_principal()
    )

    assert response.vehicle_id == vehicle_id
    assert filters == [None]


@pytest.mark.asyncio
async def test_checked_in_driver_latest_reads_the_current_owner_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver on a borrowed truck reads the owner's rows, not its own org's (DR-11)."""
    vehicle_id = uuid4()
    _patch_vehicle(monkeypatch, owner_organization_id=OTHER_ORGANIZATION_ID)
    filters = _patch_latest(
        monkeypatch, build_telemetry_record(vehicle_id=vehicle_id, recorded_at=_START)
    )

    async def checked_in(
        db: AsyncSession, membership_id: UUID, checked_vehicle_id: UUID
    ) -> bool:
        return checked_vehicle_id == vehicle_id

    monkeypatch.setattr(
        driver_service, "is_membership_checked_in_to_vehicle", checked_in
    )

    await telemetry_service.get_latest_vehicle_telemetry_response(
        fake_db_session(),
        vehicle_id,
        principal=build_principal(
            organization_id=DEFAULT_ORGANIZATION_ID,
            roles=frozenset({UserRole.DRIVER}),
        ),
    )

    assert filters == [OTHER_ORGANIZATION_ID]


@pytest.mark.asyncio
async def test_former_owner_reads_operating_report_of_its_own_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report folds only the seller's rows when the seller asks for it."""
    _patch_vehicle(
        monkeypatch,
        owner_organization_id=OTHER_ORGANIZATION_ID,
        former_owner_ids=frozenset({DEFAULT_ORGANIZATION_ID}),
    )
    window_filters: list[UUID | None] = []

    async def window_summary(
        db: AsyncSession, **kwargs: Any
    ) -> VehicleTelemetryWindowSummary:
        window_filters.append(kwargs["organization_id"])
        return _EMPTY_SUMMARY

    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )

    await telemetry_service.get_vehicle_operating_report(
        fake_db_session(),
        vehicle_id=uuid4(),
        start_time=_START,
        end_time=_END,
        principal=build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
    )
    await telemetry_service.get_vehicle_energy_usage_report(
        fake_db_session(),
        vehicle_id=uuid4(),
        start_time=_START,
        end_time=_END,
        principal=build_internal_principal(),
    )

    assert window_filters == [DEFAULT_ORGANIZATION_ID, None]


@pytest.mark.asyncio
async def test_battery_health_follows_the_truck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SOH trend reads every owner's rows for the owner; a seller gets 404."""
    ownership_lookups = _patch_vehicle(
        monkeypatch,
        owner_organization_id=OTHER_ORGANIZATION_ID,
        former_owner_ids=frozenset({DEFAULT_ORGANIZATION_ID}),
    )
    health_calls: list[dict[str, Any]] = []

    async def health_days(
        db: AsyncSession, **kwargs: Any
    ) -> list[VehicleBatteryHealthDay]:
        health_calls.append(kwargs)
        return []

    monkeypatch.setattr(
        telemetry_repository, "list_vehicle_battery_health_days", health_days
    )

    with pytest.raises(TelemetryNotFoundError):
        await telemetry_service.get_vehicle_battery_health_response(
            fake_db_session(),
            vehicle_id=uuid4(),
            start_time=_START,
            end_time=_END,
            principal=build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        )
    await telemetry_service.get_vehicle_battery_health_response(
        fake_db_session(),
        vehicle_id=uuid4(),
        start_time=_START,
        end_time=_END,
        principal=build_principal(organization_id=OTHER_ORGANIZATION_ID),
    )

    assert ownership_lookups == []
    assert len(health_calls) == 1
    assert "organization_id" not in health_calls[0]


class _StatementCapturingSession:
    """Fake session that records each statement and returns no rows.

    Attributes:
        statements: Every statement passed to ``execute``, in call order.
    """

    def __init__(self) -> None:
        """Start with no recorded statement."""
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> "_EmptyResult":
        """Record a statement and return an empty result.

        Args:
            statement: The SQLAlchemy statement the repository built.

        Returns:
            A result holding no rows.
        """
        self.statements.append(statement)
        return _EmptyResult()


class _EmptyResult:
    """Query result without rows, for the reads under test."""

    def scalar_one_or_none(self) -> None:
        """Return no row.

        Returns:
            `None`.
        """
        return None

    def scalars(self) -> "_EmptyResult":
        """Return itself, so ``scalars().all()`` works.

        Returns:
            This empty result.
        """
        return self

    def all(self) -> list[Any]:
        """Return no rows.

        Returns:
            An empty list.
        """
        return []


def _compiled_sql(statement: Any) -> str:
    """Compile a statement to PostgreSQL text with its parameters inlined.

    Args:
        statement: The SQLAlchemy statement.

    Returns:
        The SQL text.
    """
    return str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.asyncio
async def test_history_sql_filters_by_organization_only_when_given() -> None:
    """The history statement adds ``organization_id = ...`` for a customer only."""
    session = _StatementCapturingSession()
    organization_id = uuid4()

    for scope in (organization_id, None):
        await telemetry_repository.get_vehicle_telemetry_history(
            cast(AsyncSession, session),
            vehicle_id=uuid4(),
            start_time=_START,
            end_time=_START + timedelta(hours=1),
            limit=10,
            organization_id=scope,
        )

    scoped_sql, unscoped_sql = (
        _compiled_sql(statement) for statement in session.statements
    )
    assert "telemetry.organization_id =" in scoped_sql
    assert str(organization_id) in scoped_sql
    assert "organization_id =" not in unscoped_sql


@pytest.mark.asyncio
async def test_latest_sql_filters_by_organization_when_given() -> None:
    """The newest-reading and newest-receive statements carry the owner filter."""
    session = _StatementCapturingSession()
    organization_id = uuid4()

    await telemetry_repository.get_latest_vehicle_telemetry(
        cast(AsyncSession, session), uuid4(), organization_id=organization_id
    )
    await telemetry_repository.find_latest_received_at(
        cast(AsyncSession, session), uuid4(), organization_id=organization_id
    )

    for statement in session.statements:
        assert "telemetry.organization_id =" in _compiled_sql(statement)


@pytest.mark.asyncio
async def test_fleet_views_read_only_the_customer_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fleet map and rollup pass a customer's scope; internal staff pass none."""
    member_id = uuid4()
    _patch_vehicle(monkeypatch, owner_organization_id=DEFAULT_ORGANIZATION_ID)
    window_filters: list[UUID | None] = []
    live_filters: list[UUID | None] = []

    async def list_member_ids(
        db: AsyncSession, fleet_id: UUID, **_scope: object
    ) -> list[UUID]:
        return [member_id]

    async def window_summary(
        db: AsyncSession, **kwargs: Any
    ) -> VehicleTelemetryWindowSummary:
        window_filters.append(kwargs["organization_id"])
        return _EMPTY_SUMMARY

    async def resolve_summary(db: AsyncSession, vehicle_id: UUID) -> None:
        return None

    async def live_status(
        db: AsyncSession, vehicle_id: UUID, *, organization_id: UUID | None = None
    ) -> None:
        live_filters.append(organization_id)
        return None

    monkeypatch.setattr(
        fleet_service, "list_active_member_vehicle_ids", list_member_ids
    )
    monkeypatch.setattr(
        telemetry_repository, "get_vehicle_window_summary", window_summary
    )
    monkeypatch.setattr(
        vehicle_service, "resolve_vehicle_summary_by_id", resolve_summary
    )
    monkeypatch.setattr(telemetry_service, "resolve_vehicle_live_status", live_status)

    for principal in (
        build_principal(organization_id=DEFAULT_ORGANIZATION_ID),
        build_internal_principal(),
    ):
        await telemetry_service.get_fleet_operating_report(
            fake_db_session(),
            uuid4(),
            start_time=_START,
            end_time=_END,
            principal=principal,
        )
        await telemetry_service.list_fleet_vehicle_live_statuses(
            fake_db_session(), uuid4(), principal=principal
        )

    assert window_filters == [DEFAULT_ORGANIZATION_ID, None]
    assert live_filters == [DEFAULT_ORGANIZATION_ID, None]
