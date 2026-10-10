"""Public service of the billing domain: what other domains and the API flow call.

The tariff, wallet, top-up and bill logic lives in the domain's own modules
(``tariff_service``, ``ledger_service``, ``topup_service``, ``bill_service``),
reached over HTTP through ``router``. This module is the narrow surface the rest
of the application uses (WP8, WP9):

* ``resolve_tariff_for_station``: the price in force at a charger (PAY-09);
* ``create_quoted_bill``: freeze that price on a session at the scan (PAY-10);
* ``settle_session_bill`` / ``void_session_bill``: the end of a session, called
  from the session-ended hook wired in ``app/api/billing_hooks.py`` (BL-19);
* ``get_session_bill_response``: the bill of a session, for the API;
* the wallet minimum-balance rule of BL-14, driven by the setting
  ``BILLING_MIN_BALANCE_VND`` (``0`` turns it off), and the bill link on a
  session receipt (CHG-03).

Functions run inside the caller's transaction and never commit or roll back.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.billing.bill_service as bill_service
import app.domains.billing.repository as billing_repository
import app.domains.billing.tariff_service as tariff_service
from app.domains.billing.models import ChargingSessionBillModel
from app.domains.billing.schemas import SessionBillResponse
from app.domains.billing.types import (
    ChargingSessionBillStatus,
    SessionBillReference,
    TariffQuote,
    WalletStanding,
    WalletStatus,
)
from app.libs.common.config import settings


async def resolve_wallet_standing(db: AsyncSession, user_id: UUID) -> WalletStanding:
    """Read a person's balance and whether the wallet is blocked.

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person whose wallet pays (BL-13).

    Returns:
        The balance (``0`` when the person has no wallet) and the blocked flag.

    Side Effects:
        One read query; a missing wallet is not created.
    """
    wallet_record = await billing_repository.find_wallet_by_user_id(db, user_id)
    if wallet_record is None:
        return WalletStanding(balance=Decimal(0), is_blocked=False)
    return WalletStanding(
        balance=wallet_record.balance,
        is_blocked=wallet_record.status == WalletStatus.BLOCKED.value,
    )


async def has_minimum_balance(db: AsyncSession, user_id: UUID) -> bool:
    """Tell whether a person's wallet may start a charge (BL-14).

    The minimum is the platform setting ``BILLING_MIN_BALANCE_VND`` and is
    never below zero (BL-25): at the default ``0`` a wallet left negative by an
    earlier charge must be topped up before the next one. A person without a
    wallet counts as a zero balance. A blocked wallet is not judged here: the
    caller reads ``resolve_wallet_standing`` for that.

    Args:
        db: The async session owned by the entry boundary.
        user_id: The person whose wallet pays.

    Returns:
        ``True`` when the balance is at or above the minimum.
    """
    minimum_balance = settings.BILLING_MIN_BALANCE_VND
    standing = await resolve_wallet_standing(db, user_id)
    return standing.balance >= minimum_balance


def to_session_bill_reference(
    bill_record: ChargingSessionBillModel,
) -> SessionBillReference:
    """Convert a bill row into the DTO other domains and the receipt use.

    Args:
        bill_record: The bill queried by the repository.

    Returns:
        The frozen reference.
    """
    return SessionBillReference(
        status=ChargingSessionBillStatus(bill_record.status),
        price_per_kwh=bill_record.price_per_kwh,
        vat_rate_percent=bill_record.vat_rate_percent,
        energy_wh=bill_record.energy_wh,
        amount_before_vat=bill_record.amount_before_vat,
        vat_amount=bill_record.vat_amount,
        billed_at=bill_record.billed_at,
    )


async def find_session_bill_reference(
    db: AsyncSession, session_id: UUID
) -> SessionBillReference | None:
    """Find the bill of a charging session, if one exists (CHG-03).

    Args:
        db: The async session owned by the entry boundary.
        session_id: The charging session.

    Returns:
        The bill reference, or ``None`` when the session has no bill.
    """
    bill_record = await billing_repository.find_bill_by_session_id(db, session_id)
    return None if bill_record is None else to_session_bill_reference(bill_record)


async def resolve_tariff_for_station(
    db: AsyncSession, station_id: UUID, at: datetime
) -> TariffQuote:
    """Find the price in force at a charger at a moment (PAY-09, BL-08, BL-09).

    The charger's location owner's ACTIVE tariff for that location, else the
    owner's default; the version in force at `at`; the price of the time-of-use
    period covering `at`, else the normal price.

    Args:
        db: The async session owned by the entry boundary.
        station_id: The charger.
        at: The moment; must carry a timezone.

    Returns:
        The quote, with the tariff version to freeze on the bill.

    Raises:
        TariffStationNotFoundError: The charger does not exist (404).
        NoTariffInForceError: No tariff prices the charger now (409).
    """
    return await tariff_service.resolve_tariff_for_station(db, station_id, at)


async def create_quoted_bill(
    db: AsyncSession, *, session_id: UUID, quote: TariffQuote
) -> SessionBillReference:
    """Freeze a quote on a new QUOTED bill for a session at the scan (PAY-10).

    Args:
        db: The async session owned by the entry boundary.
        session_id: The new PENDING session.
        quote: The answer of `resolve_tariff_for_station`.

    Returns:
        The bill reference.

    Side Effects:
        One INSERT in the caller's transaction.
    """
    return to_session_bill_reference(
        await bill_service.create_quoted_bill(db, session_id=session_id, quote=quote)
    )


async def settle_session_bill(
    db: AsyncSession,
    *,
    session_id: UUID,
    payer_user_id: UUID,
    meter_start_wh: Decimal | None,
    meter_stop_wh: Decimal | None,
    last_measured_wh: Decimal | None,
) -> SessionBillReference | None:
    """Compute the bill of a completed session and debit the payer's wallet.

    Idempotent. A bill that cannot be trusted goes ON_HOLD instead of BILLED.

    Args:
        db: The async session owned by the entry boundary.
        session_id: The completed session.
        payer_user_id: The scanning user, whose wallet pays.
        meter_start_wh: The charger's start reading.
        meter_stop_wh: The charger's closing reading.
        last_measured_wh: The newest outlet energy measurement.

    Returns:
        The bill reference, or ``None`` when the session has no bill.

    Side Effects:
        Updates the bill and may append a ledger line, in the caller's
        transaction.
    """
    bill_record = await bill_service.settle_session_bill(
        db,
        session_id=session_id,
        payer_user_id=payer_user_id,
        meter_start_wh=meter_start_wh,
        meter_stop_wh=meter_stop_wh,
        last_measured_wh=last_measured_wh,
    )
    return None if bill_record is None else to_session_bill_reference(bill_record)


async def void_session_bill(db: AsyncSession, session_id: UUID) -> bool:
    """Void the QUOTED bill of a scan that never started.

    Args:
        db: The async session owned by the entry boundary.
        session_id: The abandoned session.

    Returns:
        ``True`` when a bill was voided.
    """
    return await bill_service.void_session_bill(db, session_id)


async def get_session_bill_response(
    db: AsyncSession, session_id: UUID
) -> SessionBillResponse:
    """Get the bill of a session the caller may already read (API).

    Args:
        db: The async session owned by the entry boundary.
        session_id: The charging session; the caller checked their reach on it.

    Returns:
        The bill with its payment state.

    Raises:
        BillNotFoundError: The session has no bill (404).
    """
    return await bill_service.get_session_bill(db, session_id)
