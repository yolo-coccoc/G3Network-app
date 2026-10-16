"""Reach checks and shared input normalization of the charging_sessions domain.

Who may read which session (the person who started it, their organization's
staff, internal staff; the fleet limit, FL-14), the loaders that return a
session only inside that reach (otherwise "not found"), and the UTC
normalization of times that arrive from the API or the gateway. Shared by
``service.py`` (the lifecycle the gateway and the QR flow drive) and the area
modules (``read_service``, ``report_service``). Internal module: other
domains use ``service.py``.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.repository as charging_session_repository
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
    ChargingSessionNotFoundError,
)
from app.domains.charging_sessions.models import (
    ChargingSessionModel,
)
from app.domains.identity.types import Principal, UserRole, roles_for

# Roles that read every session of their organization (CHG-02/04/05); a
# caller holding only DRIVER sees the sessions they started themselves.
SESSION_STAFF_ROLES = roles_for("CHG-02", "CHG-04", "CHG-05") - {UserRole.DRIVER}


def normalize_utc(value: datetime, field_name: str) -> datetime:
    """Check that the timestamp is timezone-aware and normalize it to UTC.

    Args:
        value: The input timestamp from the adapter or API.
        field_name: The field name used in the error message.

    Returns:
        A timestamp with UTC timezone.

    Raises:
        ChargingSessionInputError: If the timestamp lacks a timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ChargingSessionInputError(f"{field_name} must have a timezone")
    return value.astimezone(timezone.utc)


def session_started_by_scope(principal: Principal) -> UUID | None:
    """Tell whose sessions a caller may read inside their organization.

    Args:
        principal: The HTTP caller.

    Returns:
        `None` for staff roles (every session of the organization); the
        caller's own user ID for a DRIVER-only caller.
    """
    if principal.has_any_role(*SESSION_STAFF_ROLES):
        return None
    return principal.user_id


async def get_charging_session_record(
    db: AsyncSession,
    session_id: UUID,
    principal: Principal | None = None,
    *,
    visible_vehicle_ids: frozenset[UUID] | None = None,
    for_update: bool = False,
) -> ChargingSessionModel:
    """Load a session aggregate that must exist.

    Private on purpose: it returns the ORM model, which must never cross
    the domain boundary.

    Args:
        db: The current async session.
        session_id: UUID of the session to load.
        principal: The HTTP caller whose data reach limits the lookup; `None`
            for a system caller (OCPP adapters).
        visible_vehicle_ids: Trucks the caller's fleet limit allows (FL-10):
            a session on another truck, or with no truck, is not found unless
            the caller started it (hardening D5). `None` means no limit; read
            only with a ``principal``.
        for_update: Lock the row until the transaction ends (CE-29).

    Returns:
        The existing session aggregate.

    Raises:
        ChargingSessionNotFoundError: If the session is not found.
    """
    if principal is None:
        session_record = await charging_session_repository.get_session_by_id(
            db, session_id, for_update=for_update
        )
    else:
        session_record = await charging_session_repository.get_session_by_id(
            db,
            session_id,
            organization_id=principal.data_scope,
            started_by=session_started_by_scope(principal),
            vehicle_ids=visible_vehicle_ids,
            caller_user_id=(
                principal.user_id if visible_vehicle_ids is not None else None
            ),
            for_update=for_update,
        )
    if session_record is None:
        raise ChargingSessionNotFoundError(f"Session '{session_id}' not found")
    return session_record
