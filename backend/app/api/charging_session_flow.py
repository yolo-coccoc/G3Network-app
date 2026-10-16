"""QR charging flow, orchestrated across domains (CHG-01, CHG-03, CO-13, CO-14).

Starting a charge needs five domains in one transaction: the charger and its
location (``charging_stations``), the wallet (``billing``), the truck the driver
is at the wheel of (``drivers``), the session row (``charging_sessions``) and
the remote-start command the OCPP gateway sends (``charging_stations`` again).
``charging_stations`` already calls ``charging_sessions`` (the OCPP adapters),
so ``charging_sessions`` may not call back without closing a cycle; like the
ownership transfer (``vehicle_transfer.py``), the flow therefore sits above the
domains in the HTTP layer and calls each owner's public service in turn inside
the request's single transaction (``get_db`` commits once, or rolls everything
back when a step raises).

The module holds no business rule of its own (domain-boundaries.md, CE-20):
every check and decision is the owning domain's (``charging_sessions``: a free
gun, one open charge per person, the stop of a PENDING scan, a receipt only
after completion, the scan's expiry; ``billing``: the wallet checks, the money
a receipt shows), and each domain raises its own errors. The flow only calls
the services in order and passes values between domains that may not call
each other.

Endpoints (all under ``/charging-sessions``):

* ``POST /scan`` - checks, creates the PENDING session and queues the remote
  start (CHG-01);
* ``POST /{session_id}/stop`` - queues a remote stop for the person who
  started the charge, or staff (CHG-01);
* ``GET /{session_id}/receipt`` - the receipt data of a finished charge
  (CHG-03), with the bill when billing has one;
* ``GET /{session_id}/bill`` - the bill of a session the caller may read
  (PAY-10);
* ``POST /{session_id}/bill/settle`` - internal staff re-run the billing of a
  finished session whose bill stayed QUOTED because the session-ended hook
  failed (hardening D2).

The scan also freezes the price: the tariff in force for the charger and the
hour is resolved by billing and stored on a QUOTED bill in the same transaction
(BL-10, PAY-09). The end of the session is billed by a hook, not here (BL-19);
the retry endpoint calls that same hook function, so the rule lives in one
place.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi import status as http_status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.billing_hooks as billing_hooks
import app.domains.billing.service as billing_service
import app.domains.charging_sessions.service as charging_session_service
import app.domains.charging_stations.service as charging_stations_service
import app.domains.drivers.service as driver_service
from app.domains.billing.schemas import SessionBillResponse
from app.domains.charging_sessions.schemas import (
    ChargingSessionScanRequest,
    ChargingSessionScanResponse,
    ChargingSessionStopRequest,
    ChargingSessionStopResponse,
)
from app.domains.charging_sessions.types import SessionStatus
from app.domains.charging_stations.types import StationCommandType
from app.domains.identity.dependencies import get_visible_vehicle_ids, require_roles
from app.domains.identity.types import Principal, roles_for
from app.libs.common.clock import utc_now
from app.libs.db.session import get_db

router = APIRouter(tags=["charging-sessions"])

# CHG-01: the driver scans and stops (the role table adds the administrators);
# CHG-02 adds our operations and customer-care staff to the stop. CHG-03: the
# receipt is for the driver and the accountant.
SESSION_STARTERS = require_roles(*roles_for("CHG-01"))
SESSION_STOPPERS = require_roles(*roles_for("CHG-01", "CHG-02"))
SESSION_RECEIPT_READERS = require_roles(*roles_for("CHG-03"))
# PAY-10: the bill of a session is read by whoever may read the session (the
# starter, the organization's staff); the session's own scope decides.
SESSION_BILL_READERS = require_roles(*roles_for("CHG-03", "CHG-04", "PAY-10"))
# Re-running a session's billing changes money: our own bill reviewers only,
# the same people who release or void a held bill.
SESSION_BILL_SETTLERS = require_roles(*roles_for("PAY-10"), internal_only=True)

# Text stored as the reason of a remote stop the person asked for in the app.
_APP_STOP_REASON = "Stopped from the app"


def _to_whole_dong(amount: Decimal | None) -> int | None:
    """Convert a bill amount to the whole-dong integer the receipt shows.

    Args:
        amount: An amount from billing, already rounded to whole dong.

    Returns:
        The integer amount, or ``None`` when billing gave none.
    """
    return None if amount is None else int(amount)


class ChargingSessionQuotedScanResponse(ChargingSessionScanResponse):
    """The scan result with the price frozen for the whole charge (PAY-09, BL-10).

    Attributes:
        tariff_version_id: The tariff version whose price was quoted.
        currency: ISO 4217 currency code.
        price_per_kwh: Price per kWh for the scan's hour, before VAT, whole dong.
        vat_rate_percent: VAT rate frozen with the price.
    """

    tariff_version_id: UUID
    currency: str
    price_per_kwh: int
    vat_rate_percent: Decimal


class ChargingSessionReceiptResponse(BaseModel):
    """The receipt data of a finished charge (CHG-03).

    The money fields (whole dong) come from the session's bill and stay
    ``null`` while the bill is not BILLED; the receipt never recomputes a
    price.

    Attributes:
        session_id: UUID of the session.
        status: The session status (a receipt exists only for ``COMPLETED``).
        station_id: UUID of the charger.
        location_name: Display name of the charging location.
        location_address: Street address of the location.
        charger_reference: The number printed on the charger, if any.
        gun_number: The gun's number on the charger.
        connector_standard: The plug standard used.
        vehicle_id: The truck charged, if known (CHG-07).
        started_at: The charger's start time.
        ended_at: The charger's stop time.
        duration_seconds: Charging time.
        meter_start_wh: Meter reading at the start.
        meter_stop_wh: Meter reading at the stop.
        energy_delivered_wh: Stop minus start reading.
        stop_reason: Why the charger stopped, as sent.
        bill_status: Status of the session's bill, ``null`` without one.
        price_per_kwh: Price frozen at the scan, before VAT (whole dong).
        vat_rate_percent: VAT rate frozen with the price.
        amount_before_vat: Amount before VAT in dong, ``null`` until billed.
        vat_amount: VAT amount in dong, ``null`` until billed.
        total_amount: Amount before VAT plus VAT in dong, ``null`` until billed.
        billed_at: When the amount was fixed.
    """

    session_id: UUID
    status: SessionStatus
    station_id: UUID
    location_name: str | None
    location_address: str | None
    charger_reference: str | None
    gun_number: int | None
    connector_standard: str | None
    vehicle_id: UUID | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int = Field(..., ge=0)
    meter_start_wh: Decimal | None
    meter_stop_wh: Decimal | None
    energy_delivered_wh: Decimal | None
    stop_reason: str | None
    bill_status: str | None
    price_per_kwh: int | None
    vat_rate_percent: Decimal | None
    amount_before_vat: int | None
    vat_amount: int | None
    total_amount: int | None
    billed_at: datetime | None


@router.post(
    "/charging-sessions/scan",
    response_model=ChargingSessionQuotedScanResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Scan a charger's QR code and start the charge",
)
async def scan_charging_session_endpoint(
    scan_request: ChargingSessionScanRequest,
    principal: Principal = Depends(SESSION_STARTERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> ChargingSessionQuotedScanResponse:
    """Check the scan, create the PENDING session and queue the remote start.

    The checks run in this order, so the caller sees the first problem:

    0. the person's wallet is locked, so their scans run one at a time (RV-BL3);
    1. the charger exists, its location is visible to the caller, it is in
       service and connected (``charging_stations``);
    2. no charge is running on the named gun (on every gun, when no gun is
       named), and the caller has no other charge open (``charging_sessions``);
    3. the caller's wallet is not blocked and holds the minimum balance
       (``billing``, BL-14);
    4. a tariff must price the charger now (the price of the scan's hour is
       frozen for the whole charge);
    5. the session row is created PENDING with a single-use token, attributed to
       the truck the caller is checked in to (CE-13), and a ``REMOTE_START``
       command is queued for the OCPP gateway (PR-16). The driver then picks the
       gun and starts on the charger's screen (CO-14), and a QUOTED bill holds
       the frozen price (PAY-10).

    Args:
        scan_request: What the QR code names: the charger code and, optionally,
            the gun number.
        principal: The authenticated caller; their wallet pays and their
            organization is recorded as the payer.
        db_session: The async session whose transaction is owned by the
            ``get_db`` dependency.

    Returns:
        The new session's ID, status ``PENDING``, token, command ID, when the
        scan expires and the frozen price.

    Raises:
        ChargingStationNotFoundError: Unknown charger code or gun (404).
        ChargingLocationAccessDeniedError: Private location not granted to the
            caller's organization (403).
        ChargingStationUnavailableError: Charger, location or gun out of
            service (409).
        ChargingStationOfflineError: The charger is not connected (409).
        ChargingConnectorBusyError: The gun, or every gun, is charging (409).
        ChargingSessionAlreadyOpenError: The caller has a charge open (409).
        WalletBlockedError: The wallet is blocked (403).
        InsufficientBalanceError: The wallet is below the minimum (409).
        NoTariffInForceError: No tariff prices the charger now (409).
    """
    # Serialize this person's scans first: the one-open-charge check is an
    # unlocked read, so two simultaneous scans would both pass it (RV-BL3).
    await billing_service.lock_wallet_for_charge(db_session, principal.user_id)
    target = await charging_stations_service.resolve_scan_target(
        db_session,
        charger_code=scan_request.charger_code,
        gun_number=scan_request.connector_number,
        principal=principal,
    )
    await charging_session_service.ensure_session_may_start(
        db_session,
        user_id=principal.user_id,
        connector_id=target.connector_id,
        connector_ids=target.connector_ids,
    )
    await billing_service.ensure_wallet_may_start_charge(db_session, principal.user_id)
    quote = await billing_service.resolve_tariff_for_station(
        db_session, target.station_id, utc_now()
    )
    vehicle_id = await driver_service.find_open_vehicle_id_by_membership(
        db_session, principal.membership_id
    )
    pending_session = await charging_session_service.create_pending_session(
        db_session,
        station_id=target.station_id,
        organization_id=principal.organization_id,
        started_by=principal.user_id,
        vehicle_id=vehicle_id,
    )
    await billing_service.create_quoted_bill(
        db_session, session_id=pending_session.session_id, quote=quote
    )
    command = await charging_stations_service.queue_station_command(
        db_session,
        station_id=target.station_id,
        command_type=StationCommandType.REMOTE_START,
        evse_id=target.evse_id,
        session_id=pending_session.session_id,
        requested_by=principal.user_id,
    )
    return ChargingSessionQuotedScanResponse(
        session_id=pending_session.session_id,
        station_id=pending_session.station_id,
        status=SessionStatus.PENDING,
        id_token=pending_session.id_token,
        command_id=command.command_id,
        expires_at=pending_session.expires_at,
        tariff_version_id=quote.tariff_version_id,
        currency=quote.currency,
        price_per_kwh=int(quote.price_per_kwh),
        vat_rate_percent=quote.vat_rate_percent,
    )


@router.post(
    "/charging-sessions/{session_id}/stop",
    response_model=ChargingSessionStopResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Stop a running charge from the app",
)
async def stop_charging_session_endpoint(
    session_id: UUID,
    stop_request: ChargingSessionStopRequest | None = None,
    principal: Principal = Depends(SESSION_STOPPERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> ChargingSessionStopResponse:
    """Queue a ``REMOTE_STOP`` for a running session, or cancel a pending scan.

    The session stays ``ACTIVE`` until the charger confirms with its own stop
    message; the caller follows the command's outcome. A scan the charger has
    not started yet (``PENDING``) is abandoned at once instead: no command is
    queued, ``command_id`` is ``null``, and the session-ended hook voids the
    quoted bill (RV-BL9). Only the person who started the charge, or internal
    staff, may stop it. ``charging_sessions`` decides which of the two applies
    (``prepare_session_stop``); this endpoint only queues the command it asks
    for.

    Args:
        session_id: UUID of the session to stop.
        stop_request: Optional reason typed by staff.
        principal: The authenticated caller.
        db_session: The async session whose transaction is owned by the
            ``get_db`` dependency.

    Returns:
        The session ID, the queued command's ID (``None`` for a cancelled
        scan) and the session status.

    Raises:
        ChargingSessionNotFoundError: Unknown session or out of reach (404).
        ChargingSessionStopDeniedError: The caller did not start the charge
            and is not staff (403).
        ChargingSessionStateError: The session is neither ``ACTIVE`` nor
            ``PENDING``, or the charger started a pending one while it was
            being cancelled (409).
    """
    stop_target = await charging_session_service.prepare_session_stop(
        db_session, session_id, principal=principal
    )
    if not stop_target.should_queue_remote_stop:
        return ChargingSessionStopResponse(
            session_id=stop_target.session_id,
            command_id=None,
            status=stop_target.status,
        )
    reason = (
        stop_request.reason
        if stop_request is not None and stop_request.reason
        else None
    )
    command = await charging_stations_service.queue_station_command(
        db_session,
        station_id=stop_target.station_id,
        command_type=StationCommandType.REMOTE_STOP,
        evse_id=stop_target.evse_id,
        session_id=stop_target.session_id,
        requested_by=principal.user_id,
        reason=reason or _APP_STOP_REASON,
    )
    return ChargingSessionStopResponse(
        session_id=stop_target.session_id,
        command_id=command.command_id,
        status=stop_target.status,
    )


@router.get(
    "/charging-sessions/{session_id}/receipt",
    response_model=ChargingSessionReceiptResponse,
    summary="View the receipt of a finished charge",
)
async def get_charging_session_receipt_endpoint(
    session_id: UUID,
    principal: Principal = Depends(SESSION_RECEIPT_READERS),
    visible_vehicle_ids: frozenset[UUID] | None = Depends(get_visible_vehicle_ids),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> ChargingSessionReceiptResponse:
    """Build the receipt data of a ``COMPLETED`` session (CHG-03).

    Args:
        session_id: UUID of the session.
        principal: The authenticated caller; a session out of their reach is
            not found.
        visible_vehicle_ids: The caller's fleet limit (FL-10), `None` if
            none; a session on a truck outside it is not found, unless the
            caller started it.
        db_session: The async session whose transaction is owned by the
            ``get_db`` dependency.

    Returns:
        Place, times, energy and the bill (``null`` fields until billing has
        one; billing keeps the amounts ``null`` until the bill is BILLED, so a
        bill held for review shows its status and price only).

    Raises:
        ChargingSessionNotFoundError: Unknown session or out of reach (404).
        ChargingSessionStateError: The session has not finished (409).
    """
    session = await charging_session_service.get_completed_charging_session(
        db_session,
        session_id,
        principal=principal,
        visible_vehicle_ids=visible_vehicle_ids,
    )
    place = await charging_stations_service.resolve_session_place_reference(
        db_session, session.station_id, session.evse_id
    )
    bill = await billing_service.find_session_bill_receipt_summary(
        db_session, session_id
    )
    return ChargingSessionReceiptResponse(
        session_id=session.session_id,
        status=session.status,
        station_id=session.station_id,
        location_name=place.location_name if place else None,
        location_address=place.location_address if place else None,
        charger_reference=place.physical_reference if place else None,
        gun_number=place.gun_number if place else None,
        connector_standard=place.connector_standard if place else None,
        vehicle_id=session.vehicle_id,
        started_at=session.started_at,
        ended_at=session.ended_at,
        duration_seconds=session.duration_seconds,
        meter_start_wh=session.meter_start_wh,
        meter_stop_wh=session.meter_stop_wh,
        energy_delivered_wh=session.energy_delivered_wh,
        stop_reason=session.stop_reason,
        bill_status=bill.status.value if bill else None,
        price_per_kwh=int(bill.price_per_kwh) if bill else None,
        vat_rate_percent=bill.vat_rate_percent if bill else None,
        amount_before_vat=_to_whole_dong(bill.amount_before_vat if bill else None),
        vat_amount=_to_whole_dong(bill.vat_amount if bill else None),
        total_amount=_to_whole_dong(bill.total_amount if bill else None),
        billed_at=bill.billed_at if bill else None,
    )


@router.get(
    "/charging-sessions/{session_id}/bill",
    response_model=SessionBillResponse,
    summary="View the bill of a charging session",
)
async def get_charging_session_bill_endpoint(
    session_id: UUID,
    principal: Principal = Depends(SESSION_BILL_READERS),
    visible_vehicle_ids: frozenset[UUID] | None = Depends(get_visible_vehicle_ids),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> SessionBillResponse:
    """Get the bill of a session the caller may read (PAY-10).

    The session is read first with the caller's own scope (the person who
    started it, their organization's staff, internal staff) and fleet limit
    (FL-10), so a bill is visible to exactly the people who see the session.

    Args:
        session_id: UUID of the session.
        principal: The authenticated caller.
        visible_vehicle_ids: Trucks the caller's fleet limit allows; `None`
            when the caller is not limited.
        db_session: The async session whose transaction is owned by the
            ``get_db`` dependency.

    Returns:
        The bill: frozen price, status and, once billed, the amounts.

    Raises:
        ChargingSessionNotFoundError: Unknown session or out of reach (404).
        BillNotFoundError: The session has no bill (404).
    """
    await charging_session_service.get_charging_session(
        db_session,
        session_id,
        principal=principal,
        visible_vehicle_ids=visible_vehicle_ids,
    )
    return await billing_service.get_session_bill_response(db_session, session_id)


@router.post(
    "/charging-sessions/{session_id}/bill/settle",
    response_model=SessionBillResponse,
    summary="Bill a finished session again after a billing failure",
)
async def settle_charging_session_bill_endpoint(
    session_id: UUID,
    principal: Principal = Depends(SESSION_BILL_SETTLERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> SessionBillResponse:
    """Re-run the billing of a finished session (hardening D2, PAY-10).

    The billing of a session runs in a hook when the session ends; a failing
    hook is rolled back to its savepoint and only logged, so the session ends
    with its bill still QUOTED. Staff find such bills with the bill list's
    status filter and, once the cause is fixed, run the same hook again: a
    COMPLETED session is billed (or held), an ABANDONED one is voided. Both
    are idempotent, so a bill that is no longer QUOTED is returned unchanged.

    Args:
        session_id: UUID of the session.
        principal: The internal staff member; recorded as the actor.
        db_session: The async session whose transaction is owned by the
            ``get_db`` dependency.

    Returns:
        The bill after the retry.

    Raises:
        ChargingSessionNotFoundError: Unknown session (404).
        ChargingSessionStateError: The session has not ended (409).
        BillNotFoundError: The session has no bill (404).
    """
    session_ended_event = await charging_session_service.resolve_session_ended_event(
        db_session, session_id, principal=principal
    )
    await billing_hooks.bill_ended_session(db_session, session_ended_event)
    return await billing_service.get_session_bill_response(db_session, session_id)
