"""The bill of a charging session: quote, settle, review, read (PAY-10, BL-10).

Life of a bill:

1. **QUOTED** at the scan: the tariff version, the price for that hour and the
   VAT rate are frozen on the bill (``create_quoted_bill``).
2. When the session ends, a hook calls ``settle_session_bill``: the energy is
   decided (stop minus start reading, CE-12), the two amounts are rounded to
   whole dong (``pricing.calculate_bill_amounts``) and the bill becomes
   **BILLED**; in the same transaction the person's wallet is debited by the
   total (``SESSION_BILL``, BL-14), possibly below zero. A bill that cannot be
   trusted goes **ON_HOLD** with a reason and waits for staff to release or
   void it. An abandoned scan makes the bill **VOID**.
3. A BILLED bill is never edited; corrections are refunds or adjustments.

Every status change of a bill is a tracked update: the caller is the staff
member who decided, or no one (the system) with a fixed reason.

Limitations: an energy of exactly zero is BILLED for 0 dong and writes no
ledger line (the ledger refuses a zero debit); an ON_HOLD bill without a
computed energy cannot be released, only voided.
"""

import logging
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.billing.ledger_service as ledger_service
import app.domains.billing.pricing as pricing
import app.domains.billing.repository as billing_repository
from app.domains.billing.exceptions import BillNotFoundError, BillStateError
from app.domains.billing.models import ChargingSessionBillModel
from app.domains.billing.schemas import SessionBillListResponse, SessionBillResponse
from app.domains.billing.types import (
    ChargingSessionBillStatus,
    TariffQuote,
    WalletTransactionType,
)
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window
from app.libs.db.history import set_change_context

logger = logging.getLogger(__name__)

_STATUS_REASON_MAX_LENGTH = 200


def _fit_reason(text: str) -> str:
    """Cut a reason to the width of the ``status_reason`` column.

    Args:
        text: The reason.

    Returns:
        At most 200 characters.
    """
    return text[:_STATUS_REASON_MAX_LENGTH]


async def create_quoted_bill(
    db: AsyncSession, *, session_id: UUID, quote: TariffQuote
) -> ChargingSessionBillModel:
    """Freeze the quoted price on a new QUOTED bill at the scan (BL-10, PAY-10).

    Args:
        db: The async session owned by the entry boundary.
        session_id: The new PENDING session.
        quote: What `resolve_tariff_for_station` answered for the scan's moment.

    Returns:
        The bill.

    Side Effects:
        One INSERT; a second bill for the same session fails at flush time on
        the unique ``session_id``.
    """
    return await billing_repository.insert_bill(
        db,
        {
            "session_id": session_id,
            "tariff_version_id": quote.tariff_version_id,
            "price_per_kwh": quote.price_per_kwh,
            "vat_rate_percent": quote.vat_rate_percent,
            "status": ChargingSessionBillStatus.QUOTED.value,
        },
    )


async def _debit_wallet_for_bill(
    db: AsyncSession, bill_record: ChargingSessionBillModel, payer_user_id: UUID
) -> None:
    """Pay a BILLED bill from the payer's wallet (BL-14).

    The bill's unique link in the ledger makes this happen at most once per
    bill; a zero total writes nothing.

    Args:
        db: The async session owned by the entry boundary.
        bill_record: The bill, already BILLED with its amounts.
        payer_user_id: The person whose wallet pays (the scanning user).

    Side Effects:
        May create the wallet; appends one ledger line and moves the balance.
    """
    if bill_record.amount_before_vat is None or bill_record.vat_amount is None:
        raise BillStateError("A bill without amounts cannot be paid")
    total = bill_record.amount_before_vat + bill_record.vat_amount
    if total <= 0:
        return
    wallet_record = await ledger_service.get_or_create_wallet_for_update(
        db, payer_user_id
    )
    await ledger_service.apply_wallet_movement(
        db,
        wallet_record,
        transaction_type=WalletTransactionType.SESSION_BILL,
        amount=-total,
        charging_session_bill_id=bill_record.charging_session_bill_id,
    )


async def settle_session_bill(
    db: AsyncSession,
    *,
    session_id: UUID,
    payer_user_id: UUID,
    meter_start_wh: Decimal | None,
    meter_stop_wh: Decimal | None,
    last_measured_wh: Decimal | None,
) -> ChargingSessionBillModel | None:
    """Compute the bill of a completed session and pay it from the wallet.

    Idempotent: a bill that is no longer QUOTED is returned unchanged, so a
    repeated call never debits twice.

    Rule:
        1. The energy and its source are decided by
           `pricing.decide_billable_energy` (CE-12); a bill that cannot be
           trusted goes ON_HOLD with the reason (figures kept when computable).
        2. Otherwise the amounts are rounded half up to whole dong from the
           frozen price and VAT rate, the bill becomes BILLED, and the payer's
           wallet is debited by the total in the same transaction.

    Args:
        db: The async session owned by the entry boundary.
        session_id: The completed session.
        payer_user_id: The scanning user, whose wallet pays (BL-13).
        meter_start_wh: The charger's start reading.
        meter_stop_wh: The charger's closing reading.
        last_measured_wh: The newest outlet energy measurement.

    Returns:
        The bill, or ``None`` when the session has none (it was created
        without a scan, so nothing was quoted).

    Side Effects:
        Updates the bill and may append a ledger line, in the caller's
        transaction.
    """
    bill_record = await billing_repository.find_bill_by_session_id(
        db, session_id, for_update=True
    )
    if bill_record is None:
        logger.warning(
            "Completed session has no bill to settle",
            extra={"session_id": str(session_id)},
        )
        return None
    if bill_record.status != ChargingSessionBillStatus.QUOTED.value:
        return bill_record
    decision = pricing.decide_billable_energy(
        meter_start_wh=meter_start_wh,
        meter_stop_wh=meter_stop_wh,
        last_measured_wh=last_measured_wh,
        tolerance_percent=settings.BILLING_ENERGY_MISMATCH_TOLERANCE_PERCENT,
    )
    now = utc_now()
    if decision.energy_wh is not None:
        amount_before_vat, vat_amount = pricing.calculate_bill_amounts(
            decision.energy_wh, bill_record.price_per_kwh, bill_record.vat_rate_percent
        )
        bill_record_values = {
            "energy_wh": decision.energy_wh,
            "energy_source": decision.energy_source,
            "amount_before_vat": amount_before_vat,
            "vat_amount": vat_amount,
        }
    else:
        bill_record_values = {}
    if decision.hold_reason is not None:
        await set_change_context(
            db,
            changed_by=None,
            change_reason=_fit_reason(f"Held for review: {decision.hold_reason}"),
        )
        bill_record.status = ChargingSessionBillStatus.ON_HOLD.value
        bill_record.status_reason = _fit_reason(decision.hold_reason)
        for field_name, value in bill_record_values.items():
            setattr(bill_record, field_name, value)
        await db.flush()
        logger.warning(
            "Session bill put on hold",
            extra={"session_id": str(session_id), "reason": decision.hold_reason},
        )
        return bill_record
    await set_change_context(
        db, changed_by=None, change_reason="Billed at the end of the charge"
    )
    for field_name, value in bill_record_values.items():
        setattr(bill_record, field_name, value)
    bill_record.status = ChargingSessionBillStatus.BILLED.value
    bill_record.billed_at = now
    await db.flush()
    await _debit_wallet_for_bill(db, bill_record, payer_user_id)
    return bill_record


async def void_session_bill(db: AsyncSession, session_id: UUID) -> bool:
    """Void the QUOTED bill of a scan that never started (BL-10).

    Args:
        db: The async session owned by the entry boundary.
        session_id: The abandoned session.

    Returns:
        ``True`` when a QUOTED bill was voided.

    Side Effects:
        One tracked UPDATE of the bill.
    """
    bill_record = await billing_repository.find_bill_by_session_id(
        db, session_id, for_update=True
    )
    if (
        bill_record is None
        or bill_record.status != ChargingSessionBillStatus.QUOTED.value
    ):
        return False
    await set_change_context(
        db, changed_by=None, change_reason="The charge never started"
    )
    bill_record.status = ChargingSessionBillStatus.VOID.value
    bill_record.status_reason = "The charge never started"
    await db.flush()
    return True


async def _get_bill_for_review(
    db: AsyncSession, bill_id: UUID
) -> ChargingSessionBillModel:
    """Find and lock an ON_HOLD bill for a staff decision.

    Args:
        db: The async session owned by the entry boundary.
        bill_id: UUID of the bill.

    Returns:
        The locked bill.

    Raises:
        BillNotFoundError: Unknown bill (404).
        BillStateError: The bill is not ON_HOLD (409).
    """
    bill_record = await billing_repository.get_bill_by_id(db, bill_id, for_update=True)
    if bill_record is None:
        raise BillNotFoundError(f"Bill '{bill_id}' was not found")
    if bill_record.status != ChargingSessionBillStatus.ON_HOLD.value:
        raise BillStateError(
            f"The bill is {bill_record.status}; only a bill on hold can be reviewed"
        )
    return bill_record


async def release_bill(
    db: AsyncSession, bill_id: UUID, reason: str, *, principal: Principal
) -> SessionBillResponse:
    """Release a held bill: BILLED with its computed amounts, and paid.

    Args:
        db: The async session owned by the entry boundary.
        bill_id: UUID of the bill.
        reason: What the reviewer checked.
        principal: The staff member who decides.

    Returns:
        The bill.

    Raises:
        BillNotFoundError: Unknown bill (404).
        BillStateError: Not ON_HOLD, or the hold left no computed amounts (409).
    """
    bill_record = await _get_bill_for_review(db, bill_id)
    if bill_record.amount_before_vat is None:
        raise BillStateError(
            "The bill has no computed energy; void it and adjust the wallet by hand"
        )
    payer = await billing_repository.find_session_payer(db, bill_record.session_id)
    if payer is None:
        raise BillNotFoundError(f"Session of bill '{bill_id}' was not found")
    cleaned_reason = reason.strip()
    await set_change_context(
        db, changed_by=principal.user_id, change_reason=_fit_reason(cleaned_reason)
    )
    bill_record.status = ChargingSessionBillStatus.BILLED.value
    bill_record.status_reason = _fit_reason(cleaned_reason)
    bill_record.billed_at = utc_now()
    await db.flush()
    await _debit_wallet_for_bill(db, bill_record, payer[1])
    return await build_bill_response(db, bill_record)


async def void_held_bill(
    db: AsyncSession, bill_id: UUID, reason: str, *, principal: Principal
) -> SessionBillResponse:
    """Void a held bill: no charge, amounts cleared.

    Args:
        db: The async session owned by the entry boundary.
        bill_id: UUID of the bill.
        reason: Why nothing is charged.
        principal: The staff member who decides.

    Returns:
        The bill.

    Raises:
        BillNotFoundError: Unknown bill (404).
        BillStateError: Not ON_HOLD (409).
    """
    bill_record = await _get_bill_for_review(db, bill_id)
    cleaned_reason = reason.strip()
    await set_change_context(
        db, changed_by=principal.user_id, change_reason=_fit_reason(cleaned_reason)
    )
    bill_record.status = ChargingSessionBillStatus.VOID.value
    bill_record.status_reason = _fit_reason(cleaned_reason)
    bill_record.energy_wh = None
    bill_record.energy_source = None
    bill_record.amount_before_vat = None
    bill_record.vat_amount = None
    bill_record.billed_at = None
    await db.flush()
    return await build_bill_response(db, bill_record)


async def build_bill_response(
    db: AsyncSession,
    bill_record: ChargingSessionBillModel,
    payer: tuple[UUID, UUID] | None = None,
) -> SessionBillResponse:
    """Build a bill response with who paid and whether the ledger holds it.

    Args:
        db: The async session owned by the entry boundary.
        bill_record: The bill.
        payer: ``(organization_id, started_by)`` when the caller already read
            it (the list does); read from the session otherwise.

    Returns:
        The response; money in whole dong.

    Raises:
        BillNotFoundError: The bill's session cannot be read.
    """
    if payer is None:
        payer = await billing_repository.find_session_payer(db, bill_record.session_id)
    if payer is None:
        raise BillNotFoundError(
            f"Session of bill '{bill_record.charging_session_bill_id}' was not found"
        )
    amount_before_vat = bill_record.amount_before_vat
    vat_amount = bill_record.vat_amount
    return SessionBillResponse(
        bill_id=bill_record.charging_session_bill_id,
        session_id=bill_record.session_id,
        organization_id=payer[0],
        started_by=payer[1],
        status=ChargingSessionBillStatus(bill_record.status),
        status_reason=bill_record.status_reason,
        tariff_version_id=bill_record.tariff_version_id,
        price_per_kwh=int(bill_record.price_per_kwh),
        vat_rate_percent=bill_record.vat_rate_percent,
        energy_wh=bill_record.energy_wh,
        energy_source=bill_record.energy_source,
        amount_before_vat=None if amount_before_vat is None else int(amount_before_vat),
        vat_amount=None if vat_amount is None else int(vat_amount),
        total_amount=(
            None
            if amount_before_vat is None or vat_amount is None
            else int(amount_before_vat + vat_amount)
        ),
        is_paid=await billing_repository.is_bill_paid(
            db, bill_record.charging_session_bill_id
        ),
        billed_at=bill_record.billed_at,
        created_at=bill_record.created_at,
    )


async def get_session_bill(db: AsyncSession, session_id: UUID) -> SessionBillResponse:
    """Get the bill of a session the caller was already allowed to read.

    The access check is the session's: ``app/api/charging_session_flow.py``
    reads the session through ``charging_sessions`` with the caller's scope
    first and only then asks for its bill.

    Args:
        db: The async session owned by the entry boundary.
        session_id: The charging session.

    Returns:
        The bill.

    Raises:
        BillNotFoundError: The session has no bill (404).
    """
    bill_record = await billing_repository.find_bill_by_session_id(db, session_id)
    if bill_record is None:
        raise BillNotFoundError(f"Session '{session_id}' has no bill")
    return await build_bill_response(db, bill_record)


async def list_session_bills(
    db: AsyncSession,
    *,
    principal: Principal,
    page: int,
    page_size: int,
    organization_id: UUID | None,
    started_by: UUID | None,
    status: ChargingSessionBillStatus | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> SessionBillListResponse:
    """List session bills inside the caller's reach, newest first.

    Args:
        db: The async session owned by the entry boundary.
        principal: The caller; a customer sees the bills of sessions their
            organization paid for, internal staff every organization's.
        page: Page number.
        page_size: Rows per page.
        organization_id: Payer-organization filter, honored for staff only.
        started_by: Only bills of sessions this user scanned.
        status: Only this bill status.
        created_from: Only bills created (scanned) at or after this time.
        created_to: Only bills created before this time.

    Returns:
        A page of bills.
    """
    window = normalize_page_window(page, page_size)
    rows, total = await billing_repository.list_bills(
        db,
        organization_id=principal.data_scope,
        payer_organization_id=organization_id,
        started_by=started_by,
        status=None if status is None else status.value,
        created_from=created_from,
        created_to=created_to,
        offset=window.offset,
        limit=window.page_size,
    )
    return SessionBillListResponse(
        items=[
            await build_bill_response(db, bill, (payer_org, payer_user))
            for bill, payer_org, payer_user in rows
        ],
        total=total,
        page=window.page,
        page_size=window.page_size,
    )
