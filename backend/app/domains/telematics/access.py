"""Device reach and input normalization of the telematics domain.

The loader that returns a device only inside the caller's data scope and
fleet limit (FL-14; otherwise "not found"), and the UTC normalization of
times that arrive from the API. Shared by ``service.py`` and the area
modules. Internal module: other domains use ``service.py``.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.repository as telematics_repository
from app.domains.identity.types import Principal
from app.domains.telematics.exceptions import (
    TelematicDateInvalidError,
    TelematicNotFoundError,
)
from app.domains.telematics.models import TelematicModel


def normalize_utc(value: datetime, field_name: str) -> datetime:
    """Require a time with a time zone and convert it to UTC.

    A naive time cannot be compared with the stored UTC times, and asyncpg
    would store it as the host's local time, silently shifted.

    Args:
        value: The time from the request.
        field_name: Name used in the error message.

    Returns:
        ``value`` in UTC.

    Raises:
        TelematicDateInvalidError: If ``value`` has no time zone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise TelematicDateInvalidError(f"{field_name} must have a time zone")
    return value.astimezone(timezone.utc)


async def get_telematic_record(
    db_session: AsyncSession,
    telematic_id: UUID,
    principal: Principal,
    *,
    visible_vehicle_ids: frozenset[UUID] | None = None,
) -> TelematicModel:
    """Load a live device inside the caller's data reach and fleet limit.

    Args:
        db_session: Current database session.
        telematic_id: Internal ID of the device.
        principal: The caller; a device of another organization is not found
            unless the caller is internal.
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10);
            a device not mounted on one of them, an unmounted one included,
            is not found (fail closed, D5). `None` means no limit.

    Returns:
        The device record.

    Raises:
        TelematicNotFoundError: The device does not exist, is soft-deleted,
            belongs to another organization or is outside the fleet limit.
    """
    telematic_record = await telematics_repository.get_by_id(
        db_session, telematic_id, organization_id=principal.data_scope
    )
    if telematic_record is None or (
        visible_vehicle_ids is not None
        and telematic_record.vehicle_id not in visible_vehicle_ids
    ):
        raise TelematicNotFoundError("Telematic not found")
    return telematic_record
