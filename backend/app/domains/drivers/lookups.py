"""Lookups shared by the drivers service modules (``service.py``, ``trip_service.py``).

Internal to the drivers domain (backend-coding-conventions.md §7.4): other
domains never import it, and it never imports ``service.py`` or
``trip_service.py``, so both can import it without a cycle. It holds two
read-only lookups each module needs:

- ``find_user_id_by_driver_id`` - the person behind a driver profile, to
  address a notification to a driver (NT-07, NT-15);
- ``read_recent_truck_reading`` - the truck's newest T-Box sample when it is
  recent enough to rely on (the check-in position check, DR-07, and the
  readings a trip records at both ends, DR-12).
"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.repository as driver_repository
import app.domains.identity.service as identity_service
import app.domains.telemetry.service as telemetry_service
from app.domains.telemetry.types import VehicleLiveStatusReference
from app.libs.common.config import settings


async def find_user_id_by_driver_id(
    db_session: AsyncSession, driver_id: UUID
) -> UUID | None:
    """Find the person behind a driver profile.

    Used to address a notification to a driver: the driver checked in to a
    truck (``service.find_checked_in_user_id_by_vehicle``, NT-07) or the
    driver a trip is assigned to (``trip_service``, NT-15).

    Args:
        db_session: Session owned by the caller's entry boundary.
        driver_id: Internal ID of the driver profile.

    Returns:
        The user ID, or `None` when the profile does not exist, was deleted
        or its membership does not resolve.

    Side Effects:
        Read-only queries; does not commit or rollback.
    """
    driver_record = await driver_repository.get_by_id(db_session, driver_id)
    if driver_record is None or driver_record.deleted_at is not None:
        return None
    person = await identity_service.resolve_membership_person_reference(
        db_session, driver_record.membership_id
    )
    return None if person is None else person.user_id


async def read_recent_truck_reading(
    db_session: AsyncSession, vehicle_id: UUID, *, now: datetime
) -> VehicleLiveStatusReference | None:
    """Read the truck's latest T-Box sample if it is recent enough to rely on.

    Used for the phone-to-truck check at check-in (DR-07) and for the
    position, odometer and battery % a trip records at both ends (DR-12).

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the truck.
        now: The current time.

    Returns:
        The latest reading, or `None` when the truck has no telemetry or its
        newest sample was received longer ago than
        ``settings.DRIVERS_CHECKIN_POSITION_MAX_AGE_MINUTES``.

    Side Effects:
        Read-only; calls the telemetry domain's public service.
    """
    live_status = await telemetry_service.resolve_vehicle_live_status(
        db_session, vehicle_id
    )
    if live_status is None:
        return None
    max_age = timedelta(minutes=settings.DRIVERS_CHECKIN_POSITION_MAX_AGE_MINUTES)
    if now - live_status.received_at > max_age:
        return None
    return live_status
