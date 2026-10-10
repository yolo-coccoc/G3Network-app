"""Repository querying the vehicles and vehicle_models tables; no business rules.

Only the vehicles service calls this module; other domains go through
``vehicles/service.py``. Every lookup excludes soft-deleted rows. Functions
flush when they need a generated value or a constraint error, and never
commit or roll back - the entry boundary owns the transaction.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.vehicles.models import VehicleModel, VehicleModelModel
from app.domains.vehicles.types import VehicleStatus
from app.libs.db.history import set_change_context


async def insert(db_session: AsyncSession, values: dict[str, Any]) -> VehicleModel:
    """Insert a vehicle record into the database.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Fields used to initialize the ORM record.

    Returns:
        The newly created vehicle record, reloaded after the flush.

    Raises:
        IntegrityError: When the VIN or license plate already exists;
            raised by the flush for the service to convert.
    """
    vehicle_record = VehicleModel(**values)
    db_session.add(vehicle_record)
    await db_session.flush()
    await db_session.refresh(vehicle_record)
    return vehicle_record


async def get_by_id(
    db_session: AsyncSession,
    vehicle_id: UUID,
    *,
    organization_id: UUID | None = None,
    for_update: bool = False,
) -> VehicleModel | None:
    """Find a vehicle by ID, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        organization_id: Data scope (ACC-15): only a vehicle owned by this
            organization is found; `None` means no restriction (internal
            staff and cross-domain system lookups).
        for_update: Lock the row until the transaction ends, so concurrent
            transfers queue up (RV-AS3).

    Returns:
        The vehicle record, or None if not found or out of scope.
    """
    conditions = [
        VehicleModel.vehicle_id == vehicle_id,
        VehicleModel.deleted_at.is_(None),
    ]
    if organization_id is not None:
        conditions.append(VehicleModel.organization_id == organization_id)
    statement = select(VehicleModel).where(and_(*conditions))
    if for_update:
        # populate_existing: a row already loaded in this session must show
        # what the lock holder committed, not the stale copy (RV-AS3).
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    query_result = await db_session.execute(statement)
    return query_result.scalar_one_or_none()


async def find_by_license_plate(
    db_session: AsyncSession, license_plate: str
) -> VehicleModel | None:
    """Find a vehicle by license plate, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        license_plate: License plate of the vehicle.

    Returns:
        The vehicle record, or None if not found.
    """
    query_result = await db_session.execute(
        select(VehicleModel).where(
            and_(
                func.upper(VehicleModel.license_plate) == license_plate.upper(),
                VehicleModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def find_by_vin(db_session: AsyncSession, vin: str) -> VehicleModel | None:
    """Find a vehicle by VIN, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        vin: VIN (chassis number) of the vehicle.

    Returns:
        The vehicle record, or None if not found.
    """
    query_result = await db_session.execute(
        select(VehicleModel).where(
            and_(
                func.upper(VehicleModel.vin) == vin.upper(),
                VehicleModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


def escape_like_pattern(search_text: str) -> str:
    """Escape ``%``, ``_`` and the escape character for a ``LIKE`` pattern.

    Args:
        search_text: Text typed by a person.

    Returns:
        The text with the wildcards neutralised (escape character ``\\``).
    """
    return search_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_list_conditions(
    status_filter: VehicleStatus | None,
    organization_id: UUID | None = None,
    *,
    search: str | None = None,
    vehicle_model_id: UUID | None = None,
    owner_organization_id: UUID | None = None,
    vehicle_ids: frozenset[UUID] | None = None,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by ``list_all`` and ``count``.

    Args:
        status_filter: Service status filter, if any.
        organization_id: Data scope; `None` means every organization.
        search: Text matched against the license plate or the VIN
            (case-insensitive, anywhere in the value).
        vehicle_model_id: Only trucks of this model.
        owner_organization_id: Only trucks owned by this organization (a
            filter the caller chose, on top of the data scope).
        vehicle_ids: Fleet limit of the caller (FL-10): only these trucks;
            `None` means no limit, an empty set matches nothing.

    Returns:
        Conditions to AND together: always "not soft-deleted", plus every
        filter and the organization scope when given.
    """
    conditions: list[ColumnElement[bool]] = [VehicleModel.deleted_at.is_(None)]
    if vehicle_ids is not None:
        conditions.append(VehicleModel.vehicle_id.in_(vehicle_ids))
    if organization_id is not None:
        conditions.append(VehicleModel.organization_id == organization_id)
    if owner_organization_id is not None:
        conditions.append(VehicleModel.organization_id == owner_organization_id)
    if status_filter is not None:
        conditions.append(VehicleModel.status == status_filter)
    if vehicle_model_id is not None:
        conditions.append(VehicleModel.vehicle_model_id == vehicle_model_id)
    if search:
        pattern = f"%{escape_like_pattern(search)}%"
        conditions.append(
            or_(
                VehicleModel.license_plate.ilike(pattern, escape="\\"),
                VehicleModel.vin.ilike(pattern, escape="\\"),
            )
        )
    return conditions


async def list_all(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    status_filter: VehicleStatus | None = None,
    organization_id: UUID | None = None,
    search: str | None = None,
    vehicle_model_id: UUID | None = None,
    owner_organization_id: UUID | None = None,
    vehicle_ids: frozenset[UUID] | None = None,
) -> list[VehicleModel]:
    """Get a page of vehicles, newest first, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        status_filter: Service status filter, if any.
        organization_id: Data scope; `None` means every organization.
        search: Plate or VIN fragment, if any.
        vehicle_model_id: Only trucks of this model, if given.
        owner_organization_id: Only trucks owned by this organization, if given.
        vehicle_ids: Fleet limit of the caller (FL-10), if any.

    Returns:
        List of vehicle records.
    """
    conditions = _build_list_conditions(
        status_filter,
        organization_id,
        search=search,
        vehicle_model_id=vehicle_model_id,
        owner_organization_id=owner_organization_id,
        vehicle_ids=vehicle_ids,
    )

    query_result = await db_session.execute(
        select(VehicleModel)
        .where(and_(*conditions))
        .order_by(VehicleModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count(
    db_session: AsyncSession,
    *,
    status_filter: VehicleStatus | None = None,
    organization_id: UUID | None = None,
    search: str | None = None,
    vehicle_model_id: UUID | None = None,
    owner_organization_id: UUID | None = None,
    vehicle_ids: frozenset[UUID] | None = None,
) -> int:
    """Count the total number of vehicles, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        status_filter: Service status filter, if any.
        organization_id: Data scope; `None` means every organization.
        search: Plate or VIN fragment, if any.
        vehicle_model_id: Only trucks of this model, if given.
        owner_organization_id: Only trucks owned by this organization, if given.
        vehicle_ids: Fleet limit of the caller (FL-10), if any.

    Returns:
        Total number of vehicles matching the filter.
    """
    conditions = _build_list_conditions(
        status_filter,
        organization_id,
        search=search,
        vehicle_model_id=vehicle_model_id,
        owner_organization_id=owner_organization_id,
        vehicle_ids=vehicle_ids,
    )

    query_result = await db_session.execute(
        select(func.count(VehicleModel.vehicle_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_fields(
    db_session: AsyncSession,
    vehicle_id: UUID,
    values: dict[str, Any],
    *,
    change_reason: str,
    changed_by: UUID | None = None,
    organization_id: UUID | None = None,
) -> VehicleModel | None:
    """Update the specified fields of a live vehicle.

    Also used for a soft delete: the service passes ``deleted_at`` (and the
    status it decides on) like any other field.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        values: Fields to update, keyed by ORM attribute name. A key that is
            not an attribute of ``VehicleModel`` is ignored.
        change_reason: Why the row changes; recorded in ``vehicle_history``
            (vehicles are change-tracked).
        changed_by: The acting user, recorded as ``changed_by``.
        organization_id: Data scope; a vehicle of another organization is not
            found. `None` means no restriction.

    Returns:
        The updated vehicle record, or None if not found or soft-deleted.

    Raises:
        IntegrityError: When the new values break a unique constraint (VIN,
            license plate); raised by the flush for the service to convert.

    Side Effects:
        Flushes the UPDATE and reloads the record. ``updated_at`` is set by
        the column's ``onupdate`` hook as part of that flushed UPDATE.
    """
    vehicle_record = await get_by_id(
        db_session, vehicle_id, organization_id=organization_id
    )
    if not vehicle_record:
        return None

    await set_change_context(
        db_session, changed_by=changed_by, change_reason=change_reason
    )
    for field_name, value in values.items():
        if hasattr(vehicle_record, field_name):
            setattr(vehicle_record, field_name, value)

    await db_session.flush()
    await db_session.refresh(vehicle_record)
    return vehicle_record


async def get_vehicle_model_by_id(
    db_session: AsyncSession, vehicle_model_id: UUID, *, include_deleted: bool = False
) -> VehicleModelModel | None:
    """Find a vehicle model by ID.

    Args:
        db_session: Current database session.
        vehicle_model_id: Internal ID of the vehicle model.
        include_deleted: Also return a removed model. A vehicle keeps the
            model it already points to, so reading a vehicle's specifications
            includes removed ones; offering a model to a new vehicle does not.

    Returns:
        The vehicle model record, or None if not found.
    """
    conditions = [VehicleModelModel.vehicle_model_id == vehicle_model_id]
    if not include_deleted:
        conditions.append(VehicleModelModel.deleted_at.is_(None))
    query_result = await db_session.execute(
        select(VehicleModelModel).where(and_(*conditions))
    )
    return query_result.scalar_one_or_none()


async def find_vehicle_model_by_make_and_name(
    db_session: AsyncSession, make: str, model_name: str
) -> VehicleModelModel | None:
    """Find a live vehicle model by manufacturer and model name.

    Args:
        db_session: Current database session.
        make: Manufacturer.
        model_name: Model line name.

    Returns:
        The vehicle model record, or None if not found or removed.
    """
    query_result = await db_session.execute(
        select(VehicleModelModel).where(
            and_(
                VehicleModelModel.make == make,
                VehicleModelModel.model_name == model_name,
                VehicleModelModel.deleted_at.is_(None),
            )
        )
    )
    return query_result.scalar_one_or_none()


async def insert_vehicle_model(
    db_session: AsyncSession, values: dict[str, Any]
) -> VehicleModelModel:
    """Insert a vehicle model into the catalog.

    Args:
        db_session: Database session owned by the entry boundary.
        values: Fields used to initialize the ORM record.

    Returns:
        The newly created record, reloaded after the flush.

    Raises:
        IntegrityError: When the make and model name already exist; raised by
            the flush for the service to convert.
    """
    vehicle_model_record = VehicleModelModel(**values)
    db_session.add(vehicle_model_record)
    await db_session.flush()
    await db_session.refresh(vehicle_model_record)
    return vehicle_model_record


def _build_vehicle_model_conditions(
    search: str | None, make: str | None
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by the model list and count.

    Args:
        search: Text matched against the make or the model name
            (case-insensitive, anywhere in the value).
        make: Exact manufacturer (case-insensitive).

    Returns:
        Conditions to AND together: always "not removed", plus the filters.
    """
    conditions: list[ColumnElement[bool]] = [VehicleModelModel.deleted_at.is_(None)]
    if make:
        conditions.append(func.lower(VehicleModelModel.make) == make.lower())
    if search:
        pattern = f"%{escape_like_pattern(search)}%"
        conditions.append(
            or_(
                VehicleModelModel.make.ilike(pattern, escape="\\"),
                VehicleModelModel.model_name.ilike(pattern, escape="\\"),
            )
        )
    return conditions


async def list_vehicle_models(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    search: str | None = None,
    make: str | None = None,
) -> list[VehicleModelModel]:
    """Get a page of live vehicle models, ordered by make and model name.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        search: Make or model-name fragment, if any.
        make: Exact manufacturer, if any.

    Returns:
        List of vehicle model records.
    """
    conditions = _build_vehicle_model_conditions(search, make)
    query_result = await db_session.execute(
        select(VehicleModelModel)
        .where(and_(*conditions))
        .order_by(VehicleModelModel.make, VehicleModelModel.model_name)
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_vehicle_models(
    db_session: AsyncSession, *, search: str | None = None, make: str | None = None
) -> int:
    """Count the live vehicle models matching the filters.

    Args:
        db_session: Current database session.
        search: Make or model-name fragment, if any.
        make: Exact manufacturer, if any.

    Returns:
        Number of vehicle models not removed.
    """
    conditions = _build_vehicle_model_conditions(search, make)
    query_result = await db_session.execute(
        select(func.count(VehicleModelModel.vehicle_model_id)).where(and_(*conditions))
    )
    return query_result.scalar() or 0


async def update_vehicle_model_fields(
    db_session: AsyncSession,
    vehicle_model_id: UUID,
    values: dict[str, Any],
    *,
    change_reason: str,
    changed_by: UUID | None = None,
) -> VehicleModelModel | None:
    """Update fields of a live catalog model (also used to remove it).

    Args:
        db_session: Current database session.
        vehicle_model_id: Internal ID of the model.
        values: Fields to update, keyed by ORM attribute name.
        change_reason: Why the row changes; recorded in
            ``vehicle_model_history`` (the table is change-tracked).
        changed_by: The acting user, recorded as ``changed_by``.

    Returns:
        The updated record, or None if not found or already removed.

    Raises:
        IntegrityError: When the new make and name belong to another live
            model; raised by the flush for the service to convert.
    """
    vehicle_model_record = await get_vehicle_model_by_id(db_session, vehicle_model_id)
    if vehicle_model_record is None:
        return None
    await set_change_context(
        db_session, changed_by=changed_by, change_reason=change_reason
    )
    for field_name, value in values.items():
        if hasattr(vehicle_model_record, field_name):
            setattr(vehicle_model_record, field_name, value)
    await db_session.flush()
    await db_session.refresh(vehicle_model_record)
    return vehicle_model_record


async def list_ownership_periods(
    db_session: AsyncSession,
    vehicle_id: UUID,
    *,
    organization_id: UUID | None = None,
) -> list[tuple[UUID, datetime, datetime | None]]:
    """Read a truck's ownership periods from the view (VH-10, DM-22).

    The periods are read only through the view
    ``vehicle_ownership_periods``, never from ``vehicle_history``.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        organization_id: Data scope; only this organization's periods are
            returned. `None` means every period.

    Returns:
        ``(organization_id, owned_from, owned_until)`` rows, oldest first;
        ``owned_until`` is `None` for the owner now.
    """
    query = (
        "SELECT organization_id, owned_from, owned_until "
        "FROM vehicle_ownership_periods WHERE vehicle_id = :vehicle_id"
    )
    parameters: dict[str, Any] = {"vehicle_id": vehicle_id}
    if organization_id is not None:
        query += " AND organization_id = :organization_id"
        parameters["organization_id"] = organization_id
    query += " ORDER BY owned_from, owned_until NULLS LAST"
    query_result = await db_session.execute(text(query), parameters)
    return [
        (row.organization_id, row.owned_from, row.owned_until)
        for row in query_result.all()
    ]
