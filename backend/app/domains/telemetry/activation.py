"""Vehicle activation, computed at read time (VEH-05, VH-06, VH-20).

A truck is activated when the device mounted on it now has delivered data
after the truck's handover. Nothing is stored: the device comes from
``telematics``, the handover date from the ``vehicles`` ownership periods, and
the first sample from the ``telemetry`` table, so a truck whose T-Box was
removed shows ``NO_DEVICE`` again. The code sits in ``telemetry`` because
``telemetry`` already depends on ``vehicles``, ``telematics`` and ``fleet``
and none of them may call back (a ``vehicles`` endpoint would close a cycle).

Pure arithmetic (``calculate_activation_hours``, ``summarize_activations``) is
kept apart from the lookups so it can be tested without a database. The list
walks every live truck of the caller's scope with a few simple queries each,
per the no-preemptive-batching rule.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.telematics.service as telematics_service
import app.domains.telemetry.repository as telemetry_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.telemetry.schemas import (
    VehicleActivationResponse,
    VehicleActivationSummaryResponse,
)
from app.domains.telemetry.types import VehicleActivationStatus
from app.domains.vehicles.types import VehicleSummary

# Trucks are read from the vehicles domain in pages of this size.
_VEHICLE_PAGE_SIZE = 200


def calculate_activation_hours(
    handover_at: datetime | None, first_data_at: datetime | None
) -> float | None:
    """Hours from a truck's handover to its first data.

    Args:
        handover_at: When the truck first went to an owner, if known.
        first_data_at: When the mounted device first delivered data, if it has.

    Returns:
        The hours, rounded to one decimal; `None` when either time is unknown
        or the data arrived before the handover (a test drive does not count
        as activation time).
    """
    if handover_at is None or first_data_at is None or first_data_at < handover_at:
        return None
    return round((first_data_at - handover_at).total_seconds() / 3600, 1)


def summarize_activations(
    activations: list[VehicleActivationResponse],
) -> VehicleActivationSummaryResponse:
    """Count trucks per activation status and compute the success rate.

    Args:
        activations: The trucks of the scope.

    Returns:
        The counts, the success rate over trucks with a device (VH-06:
        activated / (awaiting + activated)) and the mean activation time.
    """
    no_device_count = sum(
        1
        for activation in activations
        if activation.activation_status is VehicleActivationStatus.NO_DEVICE
    )
    awaiting_data_count = sum(
        1
        for activation in activations
        if activation.activation_status is VehicleActivationStatus.AWAITING_DATA
    )
    activated = [
        activation
        for activation in activations
        if activation.activation_status is VehicleActivationStatus.ACTIVATED
    ]
    attempted_count = awaiting_data_count + len(activated)
    activation_hours = [
        activation.activation_hours
        for activation in activated
        if activation.activation_hours is not None
    ]
    return VehicleActivationSummaryResponse(
        total_count=len(activations),
        no_device_count=no_device_count,
        awaiting_data_count=awaiting_data_count,
        activated_count=len(activated),
        activation_rate_percent=(
            round(100 * len(activated) / attempted_count, 1)
            if attempted_count
            else None
        ),
        average_activation_hours=(
            round(sum(activation_hours) / len(activation_hours), 1)
            if activation_hours
            else None
        ),
    )


async def build_vehicle_activation(
    db_session: AsyncSession, vehicle_summary: VehicleSummary
) -> VehicleActivationResponse:
    """Compute the activation of one live truck.

    Rule:
        No mounted device is ``NO_DEVICE``. Otherwise the first sample that
        device delivered at or after the later of its mounting and the
        truck's handover decides: none is ``AWAITING_DATA``, one is
        ``ACTIVATED``.

    Args:
        db_session: Current database session.
        vehicle_summary: The truck, from the vehicles domain.

    Returns:
        The truck's activation.

    Side Effects:
        Three read-only lookups (handover, mounted device, first sample).
    """
    handover_at = await vehicle_service.resolve_first_handover_at(
        db_session, vehicle_summary.vehicle_id
    )
    mounted_device = await telematics_service.resolve_mounted_device_by_vehicle_id(
        db_session, vehicle_summary.vehicle_id
    )
    if mounted_device is None:
        return VehicleActivationResponse(
            vehicle_id=vehicle_summary.vehicle_id,
            vin=vehicle_summary.vin,
            license_plate=vehicle_summary.license_plate,
            activation_status=VehicleActivationStatus.NO_DEVICE,
            handover_at=handover_at,
            telematic_id=None,
            telematic_serial=None,
            device_mounted_at=None,
            first_data_at=None,
            activation_hours=None,
        )
    counted_from = (
        max(handover_at, mounted_device.mounted_at)
        if handover_at is not None
        else mounted_device.mounted_at
    )
    first_data_at = await telemetry_repository.find_first_received_at(
        db_session,
        vehicle_summary.vehicle_id,
        mounted_device.telematic_id,
        counted_from,
    )
    return VehicleActivationResponse(
        vehicle_id=vehicle_summary.vehicle_id,
        vin=vehicle_summary.vin,
        license_plate=vehicle_summary.license_plate,
        activation_status=(
            VehicleActivationStatus.ACTIVATED
            if first_data_at is not None
            else VehicleActivationStatus.AWAITING_DATA
        ),
        handover_at=handover_at,
        telematic_id=mounted_device.telematic_id,
        telematic_serial=mounted_device.telematic_serial,
        device_mounted_at=mounted_device.mounted_at,
        first_data_at=first_data_at,
        activation_hours=calculate_activation_hours(handover_at, first_data_at),
    )


async def collect_vehicle_activations(
    db_session: AsyncSession, *, organization_id: UUID | None
) -> list[VehicleActivationResponse]:
    """Compute the activation of every live truck in a data scope.

    Args:
        db_session: Current database session.
        organization_id: Only trucks of this organization; `None` means every
            organization.

    Returns:
        One entry per live truck, newest truck first.

    Side Effects:
        Read-only: the trucks page by page, then the lookups of
        ``build_vehicle_activation`` per truck.
    """
    activations: list[VehicleActivationResponse] = []
    offset = 0
    while True:
        vehicle_summaries = await vehicle_service.list_vehicle_summaries(
            db_session,
            organization_id=organization_id,
            offset=offset,
            limit=_VEHICLE_PAGE_SIZE,
        )
        for vehicle_summary in vehicle_summaries:
            activations.append(
                await build_vehicle_activation(db_session, vehicle_summary)
            )
        if len(vehicle_summaries) < _VEHICLE_PAGE_SIZE:
            return activations
        offset += _VEHICLE_PAGE_SIZE
