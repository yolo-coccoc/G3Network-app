"""Repository querying the support_cases table; contains no business rules."""

from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.support.models import SupportCaseModel
from app.domains.support.types import SupportCaseStatus, SupportCaseType
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


async def list_all(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    status_filter: SupportCaseStatus | None = None,
    case_type_filter: SupportCaseType | None = None,
    vehicle_id_filter: UUID | None = None,
) -> list[SupportCaseModel]:
    """Get a paginated list of support cases, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        status_filter: Status filter, if any.
        case_type_filter: Case type filter, if any.
        vehicle_id_filter: Vehicle ID filter, if any.

    Returns:
        List of support case records, newest first.
    """
    conditions: list[ColumnElement[bool]] = [SupportCaseModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(SupportCaseModel.status == status_filter)
    if case_type_filter:
        conditions.append(SupportCaseModel.case_type == case_type_filter)
    if vehicle_id_filter:
        conditions.append(SupportCaseModel.vehicle_id == vehicle_id_filter)

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
    *,
    status_filter: SupportCaseStatus | None = None,
    case_type_filter: SupportCaseType | None = None,
    vehicle_id_filter: UUID | None = None,
) -> int:
    """Count the total number of support cases, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        status_filter: Status filter, if any.
        case_type_filter: Case type filter, if any.
        vehicle_id_filter: Vehicle ID filter, if any.

    Returns:
        Total number of matching support cases.
    """
    conditions: list[ColumnElement[bool]] = [SupportCaseModel.deleted_at.is_(None)]

    if status_filter:
        conditions.append(SupportCaseModel.status == status_filter)
    if case_type_filter:
        conditions.append(SupportCaseModel.case_type == case_type_filter)
    if vehicle_id_filter:
        conditions.append(SupportCaseModel.vehicle_id == vehicle_id_filter)

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
