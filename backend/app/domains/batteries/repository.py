"""Repository querying the battery_models and batteries tables; no business rules.

Only the batteries service calls this module; other domains go through
``batteries/service.py``. Lookups exclude soft-deleted rows unless asked
otherwise. Functions flush when they need a generated value or a constraint
error, and never commit or roll back - the entry boundary owns the
transaction. Installation periods are read only through the view
``battery_installation_periods`` (DM-22), never from ``battery_history``.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.batteries.models import BatteryModel, BatteryModelModel
from app.libs.db.history import set_change_context


def escape_like_pattern(search_text: str) -> str:
    """Escape ``%``, ``_`` and the escape character for a ``LIKE`` pattern.

    Args:
        search_text: Text typed by a person.

    Returns:
        The text with the wildcards neutralised (escape character ``\\``).
    """
    return search_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- battery models ---------------------------------------------------------


async def insert_battery_model(
    db_session: AsyncSession, values: dict[str, Any]
) -> BatteryModelModel:
    """Insert a battery model into the catalog.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Fields used to initialize the ORM record.

    Returns:
        The new record, reloaded after the flush.

    Raises:
        IntegrityError: When the maker and name exist; raised by the flush.
    """
    battery_model_record = BatteryModelModel(**values)
    db_session.add(battery_model_record)
    await db_session.flush()
    await db_session.refresh(battery_model_record)
    return battery_model_record


async def get_battery_model_by_id(
    db_session: AsyncSession, battery_model_id: UUID, *, include_deleted: bool = False
) -> BatteryModelModel | None:
    """Find a battery model by ID.

    Args:
        db_session: Current database session.
        battery_model_id: Internal ID of the model.
        include_deleted: Also return a removed model (a battery keeps the
            model it already points to).

    Returns:
        The record, or None if not found.
    """
    conditions = [BatteryModelModel.battery_model_id == battery_model_id]
    if not include_deleted:
        conditions.append(BatteryModelModel.deleted_at.is_(None))
    query_result = await db_session.execute(
        select(BatteryModelModel).where(and_(*conditions))
    )
    return query_result.scalar_one_or_none()


async def find_battery_model_by_manufacturer_and_name(
    db_session: AsyncSession, manufacturer: str, model_name: str
) -> BatteryModelModel | None:
    """Find a live battery model by maker and model name.

    Args:
        db_session: Current database session.
        manufacturer: Battery maker.
        model_name: Model name.

    Returns:
        The record, or None if not found or removed.
    """
    query_result = await db_session.execute(
        select(BatteryModelModel).where(
            and_(
                BatteryModelModel.manufacturer == manufacturer,
                BatteryModelModel.model_name == model_name,
                BatteryModelModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


def _build_battery_model_conditions(
    search: str | None, chemistry: str | None
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by the model list and count.

    Args:
        search: Maker or model-name fragment (case-insensitive).
        chemistry: Exact chemistry value.

    Returns:
        Conditions to AND together: always "not removed", plus the filters.
    """
    conditions: list[ColumnElement[bool]] = [BatteryModelModel.deleted_at.is_(None)]
    if chemistry:
        conditions.append(BatteryModelModel.chemistry == chemistry)
    if search:
        pattern = f"%{escape_like_pattern(search)}%"
        conditions.append(
            or_(
                BatteryModelModel.manufacturer.ilike(pattern, escape="\\"),
                BatteryModelModel.model_name.ilike(pattern, escape="\\"),
            )
        )
    return conditions


async def list_battery_models(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    search: str | None = None,
    chemistry: str | None = None,
) -> list[BatteryModelModel]:
    """Get a page of live battery models, ordered by maker and name.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        search: Maker or model-name fragment, if any.
        chemistry: Exact chemistry value, if any.

    Returns:
        List of battery model records.
    """
    conditions = _build_battery_model_conditions(search, chemistry)
    query_result = await db_session.execute(
        select(BatteryModelModel)
        .where(and_(*conditions))
        .order_by(BatteryModelModel.manufacturer, BatteryModelModel.model_name)
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_battery_models(
    db_session: AsyncSession,
    *,
    search: str | None = None,
    chemistry: str | None = None,
) -> int:
    """Count the live battery models matching the filters.

    Args:
        db_session: Current database session.
        search: Maker or model-name fragment, if any.
        chemistry: Exact chemistry value, if any.

    Returns:
        Number of battery models not removed.
    """
    conditions = _build_battery_model_conditions(search, chemistry)
    query_result = await db_session.execute(
        select(func.count(BatteryModelModel.battery_model_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_battery_model_fields(
    db_session: AsyncSession,
    battery_model_id: UUID,
    values: dict[str, Any],
    *,
    change_reason: str,
    changed_by: UUID | None = None,
) -> BatteryModelModel | None:
    """Update fields of a live battery model (also used to remove it).

    Args:
        db_session: Current database session.
        battery_model_id: Internal ID of the model.
        values: Fields to update, keyed by ORM attribute name.
        change_reason: Why the row changes (``battery_model_history``).
        changed_by: The acting user.

    Returns:
        The updated record, or None if not found or already removed.

    Raises:
        IntegrityError: When the new maker and name belong to another model.
    """
    battery_model_record = await get_battery_model_by_id(db_session, battery_model_id)
    if battery_model_record is None:
        return None
    await set_change_context(
        db_session, changed_by=changed_by, change_reason=change_reason
    )
    for field_name, value in values.items():
        if hasattr(battery_model_record, field_name):
            setattr(battery_model_record, field_name, value)
    await db_session.flush()
    await db_session.refresh(battery_model_record)
    return battery_model_record


# --- batteries --------------------------------------------------------------


async def insert(db_session: AsyncSession, values: dict[str, Any]) -> BatteryModel:
    """Insert a battery record.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Fields used to initialize the ORM record.

    Returns:
        The new record, reloaded after the flush.

    Raises:
        IntegrityError: When the serial number exists among live batteries.
    """
    battery_record = BatteryModel(**values)
    db_session.add(battery_record)
    await db_session.flush()
    await db_session.refresh(battery_record)
    return battery_record


async def get_by_id(
    db_session: AsyncSession,
    battery_id: UUID,
    *,
    organization_id: UUID | None = None,
) -> BatteryModel | None:
    """Find a live battery by ID.

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        organization_id: Data scope: only a battery owned by this
            organization is found; `None` means no restriction.

    Returns:
        The record, or None if not found, removed or out of scope.
    """
    conditions = [
        BatteryModel.battery_id == battery_id,
        BatteryModel.deleted_at.is_(None),
    ]
    if organization_id is not None:
        conditions.append(BatteryModel.organization_id == organization_id)
    query_result = await db_session.execute(
        select(BatteryModel).where(and_(*conditions))
    )
    return query_result.scalar_one_or_none()


async def find_by_serial_number(
    db_session: AsyncSession, serial_number: str
) -> BatteryModel | None:
    """Find a live battery by serial number.

    Args:
        db_session: Current database session.
        serial_number: Manufacturer's serial number.

    Returns:
        The record, or None.
    """
    query_result = await db_session.execute(
        select(BatteryModel).where(
            and_(
                BatteryModel.serial_number == serial_number,
                BatteryModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_vehicle_id(
    db_session: AsyncSession, vehicle_id: UUID
) -> BatteryModel | None:
    """Find the live battery fitted to a truck now.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the truck.

    Returns:
        The record, or None when the truck holds no battery.
    """
    query_result = await db_session.execute(
        select(BatteryModel).where(
            and_(
                BatteryModel.vehicle_id == vehicle_id,
                BatteryModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


def _build_battery_conditions(
    organization_id: UUID | None,
    *,
    search: str | None,
    status: str | None,
    battery_model_id: UUID | None,
    vehicle_id: UUID | None,
    is_installed: bool | None,
    owner_organization_id: UUID | None,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by ``list_all`` and ``count``.

    Args:
        organization_id: Data scope; `None` means every organization.
        search: Serial-number fragment (case-insensitive).
        status: Exact status value.
        battery_model_id: Only batteries of this model.
        vehicle_id: Only the battery fitted to this truck.
        is_installed: True for fitted packs, False for packs in stock.
        owner_organization_id: Only batteries owned by this organization.

    Returns:
        Conditions to AND together: always "not removed", plus the filters.
    """
    conditions: list[ColumnElement[bool]] = [BatteryModel.deleted_at.is_(None)]
    if organization_id is not None:
        conditions.append(BatteryModel.organization_id == organization_id)
    if owner_organization_id is not None:
        conditions.append(BatteryModel.organization_id == owner_organization_id)
    if status:
        conditions.append(BatteryModel.status == status)
    if battery_model_id is not None:
        conditions.append(BatteryModel.battery_model_id == battery_model_id)
    if vehicle_id is not None:
        conditions.append(BatteryModel.vehicle_id == vehicle_id)
    if is_installed is True:
        conditions.append(BatteryModel.vehicle_id.is_not(None))
    if is_installed is False:
        conditions.append(BatteryModel.vehicle_id.is_(None))
    if search:
        pattern = f"%{escape_like_pattern(search)}%"
        conditions.append(BatteryModel.serial_number.ilike(pattern, escape="\\"))
    return conditions


async def list_all(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    organization_id: UUID | None = None,
    search: str | None = None,
    status: str | None = None,
    battery_model_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    is_installed: bool | None = None,
    owner_organization_id: UUID | None = None,
) -> list[BatteryModel]:
    """Get a page of live batteries, newest first.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        organization_id: Data scope; `None` means every organization.
        search: Serial-number fragment, if any.
        status: Exact status value, if any.
        battery_model_id: Only batteries of this model, if given.
        vehicle_id: Only the battery fitted to this truck, if given.
        is_installed: Fitted (True) or in stock (False), if given.
        owner_organization_id: Only batteries of this owner, if given.

    Returns:
        List of battery records.
    """
    conditions = _build_battery_conditions(
        organization_id,
        search=search,
        status=status,
        battery_model_id=battery_model_id,
        vehicle_id=vehicle_id,
        is_installed=is_installed,
        owner_organization_id=owner_organization_id,
    )
    query_result = await db_session.execute(
        select(BatteryModel)
        .where(and_(*conditions))
        .order_by(BatteryModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    search: str | None = None,
    status: str | None = None,
    battery_model_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    is_installed: bool | None = None,
    owner_organization_id: UUID | None = None,
) -> int:
    """Count the live batteries matching the filters.

    Args:
        db_session: Current database session.
        organization_id: Data scope; `None` means every organization.
        search: Serial-number fragment, if any.
        status: Exact status value, if any.
        battery_model_id: Only batteries of this model, if given.
        vehicle_id: Only the battery fitted to this truck, if given.
        is_installed: Fitted (True) or in stock (False), if given.
        owner_organization_id: Only batteries of this owner, if given.

    Returns:
        Number of matching batteries.
    """
    conditions = _build_battery_conditions(
        organization_id,
        search=search,
        status=status,
        battery_model_id=battery_model_id,
        vehicle_id=vehicle_id,
        is_installed=is_installed,
        owner_organization_id=owner_organization_id,
    )
    query_result = await db_session.execute(
        select(func.count(BatteryModel.battery_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession,
    battery_id: UUID,
    values: dict[str, Any],
    *,
    change_reason: str,
    changed_by: UUID | None = None,
    organization_id: UUID | None = None,
) -> BatteryModel | None:
    """Update fields of a live battery (also used to fit, remove and delete).

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        values: Fields to update, keyed by ORM attribute name. Explicit
            ``None`` values are written (clearing ``vehicle_id``).
        change_reason: Why the row changes (``battery_history``).
        changed_by: The acting user.
        organization_id: Data scope; `None` means no restriction.

    Returns:
        The updated record, or None if not found, removed or out of scope.

    Raises:
        IntegrityError: When the new values break a unique index (serial
            number, one battery per truck).
    """
    battery_record = await get_by_id(
        db_session, battery_id, organization_id=organization_id
    )
    if battery_record is None:
        return None
    await set_change_context(
        db_session, changed_by=changed_by, change_reason=change_reason
    )
    for field_name, value in values.items():
        if hasattr(battery_record, field_name):
            setattr(battery_record, field_name, value)
    await db_session.flush()
    await db_session.refresh(battery_record)
    return battery_record


async def list_installation_periods(
    db_session: AsyncSession, battery_id: UUID
) -> list[tuple[UUID, datetime, datetime | None]]:
    """Read a battery's stays in trucks from the view (VH-16, DM-22).

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.

    Returns:
        ``(vehicle_id, installed_from, installed_until)`` rows, oldest first;
        ``installed_until`` is `None` while the battery is still fitted.
    """
    query_result = await db_session.execute(
        text(
            "SELECT vehicle_id, installed_from, installed_until "
            "FROM battery_installation_periods WHERE battery_id = :battery_id "
            "ORDER BY installed_from"
        ),
        {"battery_id": battery_id},
    )
    return [
        (row.vehicle_id, row.installed_from, row.installed_until)
        for row in query_result.all()
    ]
