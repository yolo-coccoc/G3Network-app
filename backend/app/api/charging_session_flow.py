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

Endpoints (all under ``/charging-sessions``):

* ``POST /scan`` - checks, creates the PENDING session and queues the remote
  start (CHG-01);
* ``POST /{session_id}/stop`` - queues a remote stop for the person who
  started the charge, or staff (CHG-01);
* ``GET /{session_id}/receipt`` - the receipt data of a finished charge
  (CHG-03), with the bill when billing has one;
* ``GET /{session_id}/bill`` - the bill of a session the caller may read
  (PAY-10).

The scan also freezes the price: the tariff in force for the charger and the
hour is resolved by billing and stored on a QUOTED bill in the same transaction
(BL-10, PAY-09). The end of the session is billed by a hook, not here (BL-19).
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi import status as http_status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.billing.service as billing_service
import app.domains.charging_sessions.service as charging_session_service
import app.domains.charging_stations.service as charging_stations_service
import app.domains.drivers.service as driver_service
from app.domains.billing.exceptions import (
    InsufficientBalanceError,
    WalletBlockedError,
)
from app.domains.billing.schemas import SessionBillResponse
from app.domains.charging_sessions.exceptions import (
    ChargingConnectorBusyError,
    ChargingSessionAlreadyOpenError,
    ChargingSessionStateError,
)
from app.domains.charging_sessions.schemas import (
    ChargingSessionScanRequest,
    ChargingSessionScanResponse,
    ChargingSessionStopRequest,
    ChargingSessionStopResponse,
)
from app.domains.charging_sessions.types import SessionStatus
from app.domains.charging_stations.types import StationCommandType
from app.domains.identity.dependencies import require_roles
from app.domains.identity.types import Principal, roles_for
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
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

# Text stored as the reason of a remote stop the person asked for in the app.
_APP_STOP_REASON = "Stopped from the app"


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

    1. the charger exists, its location is visible to the caller, it is in
       service and connected (``charging_stations``);
    2. no charge is running on the named gun (on every gun, when no gun is
       named);
    3. the caller has no other charge open;
    4. the caller's wallet is not blocked and holds the minimum balance (BL-14);
    5. a tariff must price the charger now (the price of the scan's hour is
       frozen for the whole charge);
    6. the session row is created PENDING with a single-use token, attributed to
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
        The new session's ID, status ``PENDING``, token, command ID and the
        frozen price.

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
    target = await charging_stations_service.resolve_scan_target(
        db_session,
        charger_code=scan_request.charger_code,
        gun_number=scan_request.connector_number,
        principal=principal,
    )
    if target.connector_id is not None:
        is_busy = await charging_session_service.has_active_session_on_connector(
            db_session, target.connector_id
        )
    else:
        # No gun named: the driver may still pick a free one, so only a charger
        # whose every gun is charging refuses the scan.
        is_busy = bool(target.connector_ids)
        for connector_id in target.connector_ids:
            if not await charging_session_service.has_active_session_on_connector(
                db_session, connector_id
            ):
                is_busy = False
                break
    if is_busy:
        raise ChargingConnectorBusyError("A charge is already running on this charger")
    if await charging_session_service.has_open_session_by_user(
        db_session, principal.user_id
    ):
        raise ChargingSessionAlreadyOpenError(
            "You already have a charge open; finish or stop it first"
        )
    wallet = await billing_service.resolve_wallet_standing(
        db_session, principal.user_id
    )
    if wallet.is_blocked:
        raise WalletBlockedError("Your wallet is blocked, so you cannot start a charge")
    if not await billing_service.has_minimum_balance(db_session, principal.user_id):
        shortfall = settings.BILLING_MIN_BALANCE_VND - wallet.balance
        raise InsufficientBalanceError(
            "INSUFFICIENT_BALANCE: top up at least "
            f"{shortfall:.0f} VND to start a charge"
        )
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
        expires_at=utc_now()
        + timedelta(seconds=settings.CHARGING_PENDING_SESSION_TIMEOUT_SECONDS),
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
    """Queue a ``REMOTE_STOP`` for a running session.

    The session stays ``ACTIVE`` until the charger confirms with its own stop
    message; the caller follows the command's outcome. Only the person who
    started the charge, or internal staff, may stop it.

    Args:
        session_id: UUID of the session to stop.
        stop_request: Optional reason typed by staff.
        principal: The authenticated caller.
        db_session: The async session whose transaction is owned by the
            ``get_db`` dependency.

    Returns:
        The session ID, the queued command's ID and the session status.

    Raises:
        ChargingSessionNotFoundError: Unknown session or out of reach (404).
        ChargingSessionStopDeniedError: The caller did not start the charge
            and is not staff (403).
        ChargingSessionStateError: The session is not ``ACTIVE`` (409).
    """
    session = await charging_session_service.authorize_session_stop(
        db_session, session_id, principal=principal
    )
    reason = (
        stop_request.reason
        if stop_request is not None and stop_request.reason
        else None
    )
    command = await charging_stations_service.queue_station_command(
        db_session,
        station_id=session.station_id,
        command_type=StationCommandType.REMOTE_STOP,
        evse_id=session.evse_id,
        session_id=session.session_id,
        requested_by=principal.user_id,
        reason=reason or _APP_STOP_REASON,
    )
    return ChargingSessionStopResponse(
        session_id=session.session_id,
        command_id=command.command_id,
        status=session.status,
    )


@router.get(
    "/charging-sessions/{session_id}/receipt",
    response_model=ChargingSessionReceiptResponse,
    summary="View the receipt of a finished charge",
)
async def get_charging_session_receipt_endpoint(
    session_id: UUID,
    principal: Principal = Depends(SESSION_RECEIPT_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> ChargingSessionReceiptResponse:
    """Build the receipt data of a ``COMPLETED`` session (CHG-03).

    Args:
        session_id: UUID of the session.
        principal: The authenticated caller; a session out of their reach is
            not found.
        db_session: The async session whose transaction is owned by the
            ``get_db`` dependency.

    Returns:
        Place, times, energy and the bill (``null`` fields until billing has
        one).

    Raises:
        ChargingSessionNotFoundError: Unknown session or out of reach (404).
        ChargingSessionStateError: The session has not finished (409).
    """
    session = await charging_session_service.get_charging_session(
        db_session, session_id, principal=principal
    )
    if session.status is not SessionStatus.COMPLETED:
        raise ChargingSessionStateError(
            f"Session '{session_id}' is {session.status.value}; "
            "a receipt exists only after the charge completed"
        )
    place = await charging_stations_service.resolve_session_place_reference(
        db_session, session.station_id, session.evse_id
    )
    bill = await billing_service.find_session_bill_reference(db_session, session_id)
    total_amount = (
        bill.amount_before_vat + bill.vat_amount
        if bill is not None
        and bill.amount_before_vat is not None
        and bill.vat_amount is not None
        else None
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
        amount_before_vat=(
            int(bill.amount_before_vat)
            if bill and bill.amount_before_vat is not None
            else None
        ),
        vat_amount=(
            int(bill.vat_amount) if bill and bill.vat_amount is not None else None
        ),
        total_amount=None if total_amount is None else int(total_amount),
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
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> SessionBillResponse:
    """Get the bill of a session the caller may read (PAY-10).

    The session is read first with the caller's own scope (the person who
    started it, their organization's staff, internal staff), so a bill is
    visible to exactly the people who see the session.

    Args:
        session_id: UUID of the session.
        principal: The authenticated caller.
        db_session: The async session whose transaction is owned by the
            ``get_db`` dependency.

    Returns:
        The bill: frozen price, status and, once billed, the amounts.

    Raises:
        ChargingSessionNotFoundError: Unknown session or out of reach (404).
        BillNotFoundError: The session has no bill (404).
    """
    await charging_session_service.get_charging_session(
        db_session, session_id, principal=principal
    )
    return await billing_service.get_session_bill_response(db_session, session_id)
