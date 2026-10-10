"""Ownership transfer of a truck, orchestrated across domains (VEH-02, VH-12).

VH-12 makes the transfer one action in one transaction: the truck changes
owner and everything that belongs to the seller ends. The pieces live in
different domains, and the dependency edges run one way (``fleet``,
``drivers`` and ``batteries`` call ``vehicles``, never the reverse; FL-01,
VH-13), so ``vehicles`` cannot perform the whole action itself. This module
sits above the domains, in the HTTP layer, and calls each owner's public
service in turn inside the request's single transaction (``get_db`` commits
once, or rolls everything back if any step raises).

Closed in this version: the seller's fleet membership (``fleet``), the open
driving session with `OWNER_CHANGED` (``drivers``) and a battery the seller
owns, which moves to the buyer (``batteries``). The charging-policy
assignments, the per-vehicle subscription and the VIN charging credential of
VH-12 belong to parked groups with no tables yet (PR-15); a T-Box keeps its
owner (TX-07); warranties, support cases and maintenance bookings are
untouched, as VH-12 says.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.batteries.service as battery_service
import app.domains.drivers.service as driver_service
import app.domains.fleet.service as fleet_service
import app.domains.vehicles.service as vehicle_service
from app.domains.identity.dependencies import require_roles
from app.domains.identity.types import Principal, roles_for
from app.domains.vehicles.schemas import (
    VehicleOwnershipTransferRequest,
    VehicleResponse,
)
from app.libs.db.session import get_db

router = APIRouter(tags=["vehicles"])

# VEH-02 is an internal feature (offer: internal): only staff of our own
# organizations hand a truck over.
VEHICLE_TRANSFER_WRITERS = require_roles(*roles_for("VEH-02"), internal_only=True)


class VehicleOwnershipTransferResponse(BaseModel):
    """Result of a truck ownership transfer: the truck and what ended with it."""

    vehicle: VehicleResponse = Field(..., description="The truck with its new owner")
    previous_organization_id: UUID = Field(..., description="The seller")
    closed_fleet_id: UUID | None = Field(
        default=None, description="Fleet whose membership of the truck was closed"
    )
    ended_driving_session_id: UUID | None = Field(
        default=None, description="Driving session ended with OWNER_CHANGED"
    )
    moved_battery_id: UUID | None = Field(
        default=None, description="Battery that moved to the buyer with the truck"
    )


@router.post(
    "/{vehicle_id}/transfer-ownership",
    status_code=status.HTTP_200_OK,
    response_model=VehicleOwnershipTransferResponse,
    summary="Transfer a truck to a new owner",
    description="One action in one transaction: the truck changes owner and "
    "effective date (history keeps the reason), the seller's fleet membership "
    "and open driving session end, and a battery the seller owns moves to the "
    "buyer. Internal staff only.",
)
async def transfer_vehicle_ownership_endpoint(
    vehicle_id: UUID,
    vehicle_ownership_transfer_request: VehicleOwnershipTransferRequest,
    principal: Principal = Depends(VEHICLE_TRANSFER_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> VehicleOwnershipTransferResponse:
    """Transfer a truck and close what belongs to the seller (VH-12).

    Args:
        vehicle_id: Internal ID of the truck.
        vehicle_ownership_transfer_request: New owner, effective date, reason.
        principal: The authenticated caller (internal staff).
        db_session: Database session owned by the HTTP boundary; the whole
            action commits or rolls back together.

    Returns:
        The truck and the IDs of what ended or moved with it.

    Raises:
        VehicleNotFoundError: The truck does not exist (404).
        OrganizationNotFoundError: The new owner does not exist (404).
        VehicleTransferInvalidError: Same owner, or a bad date (400).
    """
    transfer_result = await vehicle_service.transfer_vehicle_ownership(
        db_session,
        vehicle_id,
        vehicle_ownership_transfer_request,
        principal=principal,
    )
    closed_fleet_id = await fleet_service.close_membership_of_sold_vehicle(
        db_session, vehicle_id, removed_at=transfer_result.acquired_at
    )
    ended_driving_session_id = (
        await driver_service.end_open_session_on_ownership_change(
            db_session, vehicle_id, ended_at=transfer_result.acquired_at
        )
    )
    moved_battery_id = await battery_service.transfer_installed_battery_with_vehicle(
        db_session,
        vehicle_id,
        from_organization_id=transfer_result.previous_organization_id,
        to_organization_id=transfer_result.organization_id,
        acquired_at=transfer_result.acquired_at,
        changed_by=principal.user_id,
    )
    vehicle_response = await vehicle_service.get_vehicle(
        db_session, vehicle_id, principal=principal
    )
    return VehicleOwnershipTransferResponse(
        vehicle=vehicle_response,
        previous_organization_id=transfer_result.previous_organization_id,
        closed_fleet_id=closed_fleet_id,
        ended_driving_session_id=ended_driving_session_id,
        moved_battery_id=moved_battery_id,
    )
