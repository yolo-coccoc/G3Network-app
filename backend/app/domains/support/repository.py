"""Repository querying the support_cases table; contains no business rules."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.support.models import SupportCaseModel
from app.domains.support.types import (
    SupportCaseListFilter,
    SupportCaseStatus,
    is_terminal_status,
)
from app.libs.common.clock import utc_now


async def insert(db_session: AsyncSession, values: dict[str, Any]) -> SupportCaseModel:
    """Insert a support case record into the database.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Fields used to initialize the ORM record.

    Returns:
        The newly created support case record.
    """
    case_record = SupportCaseModel(**values)
    db_session.add(case_record)
    await db_session.flush()
    await db_session.refresh(case_record)
    return case_record


async def get_by_id(db_session: AsyncSession, case_id: UUID) -> SupportCaseModel | None:
    """Find a support case by ID, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.

    Returns:
        The support case record, or None if not found.
    """
    query_result = await db_session.execute(
        select(SupportCaseModel).where(
            and_(
                SupportCaseModel.case_id == case_id,
                SupportCaseModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


def _case_list_conditions(
    case_list_filter: SupportCaseListFilter, *, evaluated_at: datetime
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by `list_all` and `count`.

    The awaiting-response and SLA-breach conditions are the SQL form of
    `is_terminal_status` and the service's `calculate_is_sla_breached`;
    a change to either rule must change these conditions too, or a case's
    `is_sla_breached` field would disagree with the filter that listed it.

    Args:
        case_list_filter: The filters to apply.
        evaluated_at: "Now" for the SLA-breach condition, so one request
            judges every case against the same instant.

    Returns:
        Conditions to AND together; always excludes soft-deleted cases.
    """
    conditions: list[ColumnElement[bool]] = [SupportCaseModel.deleted_at.is_(None)]

    if case_list_filter.status:
        conditions.append(SupportCaseModel.status == case_list_filter.status)
    if case_list_filter.case_type:
        conditions.append(SupportCaseModel.case_type == case_list_filter.case_type)
    if case_list_filter.vehicle_id:
        conditions.append(SupportCaseModel.vehicle_id == case_list_filter.vehicle_id)
    if case_list_filter.category:
        conditions.append(SupportCaseModel.category == case_list_filter.category)
    if case_list_filter.channel:
        conditions.append(SupportCaseModel.channel == case_list_filter.channel)
    if case_list_filter.driver_id:
        conditions.append(SupportCaseModel.driver_id == case_list_filter.driver_id)

    if case_list_filter.is_awaiting_response is not None:
        terminal_statuses = [
            status for status in SupportCaseStatus if is_terminal_status(status)
        ]
        is_awaiting_response = and_(
            SupportCaseModel.first_responded_at.is_(None),
            SupportCaseModel.status.not_in(terminal_statuses),
        )
        conditions.append(
            is_awaiting_response
            if case_list_filter.is_awaiting_response
            else not_(is_awaiting_response)
        )

    if case_list_filter.is_sla_breached is not None:
        # Same reference time as calculate_is_sla_breached: the first
        # response; else, for a case cancelled unanswered, its cancellation
        # time; else now. Never NULL, so NOT() below is a true complement.
        reference_time = func.coalesce(
            SupportCaseModel.first_responded_at,
            case(
                (
                    SupportCaseModel.status == SupportCaseStatus.CANCELLED,
                    SupportCaseModel.closed_at,
                )
            ),
            evaluated_at,
        )
        is_sla_breached = reference_time > SupportCaseModel.response_due_at
        conditions.append(
            is_sla_breached
            if case_list_filter.is_sla_breached
            else not_(is_sla_breached)
        )
    return conditions


async def list_all(
    db_session: AsyncSession,
    case_list_filter: SupportCaseListFilter,
    *,
    offset: int,
    limit: int,
    evaluated_at: datetime,
) -> list[SupportCaseModel]:
    """Get a paginated list of support cases, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        case_list_filter: The filters to apply.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        evaluated_at: "Now" for the SLA-breach filter.

    Returns:
        List of support case records, newest first.
    """
    conditions = _case_list_conditions(case_list_filter, evaluated_at=evaluated_at)

    query_result = await db_session.execute(
        select(SupportCaseModel)
        .where(and_(*conditions))
        .order_by(SupportCaseModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession,
    case_list_filter: SupportCaseListFilter,
    *,
    evaluated_at: datetime,
) -> int:
    """Count the support cases matching the same filters as `list_all`.

    Args:
        db_session: Current database session.
        case_list_filter: The filters to apply.
        evaluated_at: "Now" for the SLA-breach filter.

    Returns:
        Total number of matching support cases.
    """
    conditions = _case_list_conditions(case_list_filter, evaluated_at=evaluated_at)

    query_result = await db_session.execute(
        select(func.count(SupportCaseModel.case_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession, case_id: UUID, values: dict[str, Any]
) -> SupportCaseModel | None:
    """Update the specified fields of a support case.

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.
        values: Fields to update.

    Returns:
        The updated support case record, or None if not found.
    """
    case_record = await get_by_id(db_session, case_id)
    if not case_record:
        return None

    for field_name, value in values.items():
        if hasattr(case_record, field_name):
            setattr(case_record, field_name, value)

    case_record.updated_at = utc_now()
    await db_session.flush()
    await db_session.refresh(case_record)
    return case_record


async def soft_delete(
    db_session: AsyncSession, case_id: UUID
) -> SupportCaseModel | None:
    """Soft-delete a support case by updating deleted_at.

    Args:
        db_session: Current database session.
        case_id: Internal ID of the support case.

    Returns:
        The support case record after soft delete, or None if not found.
    """
    case_record = await get_by_id(db_session, case_id)
    if not case_record:
        return None

    case_record.deleted_at = utc_now()
    await db_session.flush()
    await db_session.refresh(case_record)
    return case_record
