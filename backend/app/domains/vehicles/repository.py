"""Repository querying the vehicles and vehicle_models tables; no business rules.

Only the vehicles service calls this module; other domains go through
``vehicles/service.py``. Every lookup excludes soft-deleted rows. Functions
flush when they need a generated value or a constraint error, and never
commit or roll back - the entry boundary owns the transaction.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
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


async def get_by_id(db_session: AsyncSession, vehicle_id: UUID) -> VehicleModel | None:
    """Find a vehicle by ID, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The vehicle record, or None if not found.
    """
    query_result = await db_session.execute(
        select(VehicleModel).where(
            and_(
                VehicleModel.vehicle_id == vehicle_id,
                VehicleModel.deleted_at.is_(None),
            )
        )
    )
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
                VehicleModel.license_plate == license_plate,
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
            and_(VehicleModel.vin == vin, VehicleModel.deleted_at.is_(None))
        )
    )
    return query_result.scalar_one_or_none()


def _build_list_conditions(
    status_filter: VehicleStatus | None,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by ``list_all`` and ``count``.

    Args:
        status_filter: Service status filter, if any.

    Returns:
        Conditions to AND together: always "not soft-deleted", plus the
        status filter when given.
    """
    conditions: list[ColumnElement[bool]] = [VehicleModel.deleted_at.is_(None)]
    if status_filter is not None:
        conditions.append(VehicleModel.status == status_filter)
    return conditions


async def list_all(
    db_session: AsyncSession,
    *,
    offset: int,
    limit: int,
    status_filter: VehicleStatus | None = None,
) -> list[VehicleModel]:
    """Get a page of vehicles, newest first, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        status_filter: Service status filter, if any.

    Returns:
        List of vehicle records.
    """
    conditions = _build_list_conditions(status_filter)

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
) -> int:
    """Count the total number of vehicles, excluding soft-deleted records.

    Args:
        db_session: Current database session.
        status_filter: Service status filter, if any.

    Returns:
        Total number of vehicles matching the filter.
    """
    conditions = _build_list_conditions(status_filter)

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
            (vehicles are change-tracked). The actor is unknown until
            authentication exists, so ``changed_by`` is ``None`` (DM-29).

    Returns:
        The updated vehicle record, or None if not found or soft-deleted.

    Raises:
        IntegrityError: When the new values break a unique constraint (VIN,
            license plate); raised by the flush for the service to convert.

    Side Effects:
        Flushes the UPDATE and reloads the record. ``updated_at`` is set by
        the column's ``onupdate`` hook as part of that flushed UPDATE.
    """
    vehicle_record = await get_by_id(db_session, vehicle_id)
    if not vehicle_record:
        return None

    await set_change_context(db_session, changed_by=None, change_reason=change_reason)
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


async def list_vehicle_models(
    db_session: AsyncSession, *, offset: int, limit: int
) -> list[VehicleModelModel]:
    """Get a page of live vehicle models, ordered by make and model name.

    Args:
        db_session: Current database session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        List of vehicle model records.
    """
    query_result = await db_session.execute(
        select(VehicleModelModel)
        .where(VehicleModelModel.deleted_at.is_(None))
        .order_by(VehicleModelModel.make, VehicleModelModel.model_name)
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_vehicle_models(db_session: AsyncSession) -> int:
    """Count the live vehicle models.

    Args:
        db_session: Current database session.

    Returns:
        Number of vehicle models not removed.
    """
    query_result = await db_session.execute(
        select(func.count(VehicleModelModel.vehicle_model_id)).where(
            VehicleModelModel.deleted_at.is_(None)
        )
    )
    return query_result.scalar() or 0
