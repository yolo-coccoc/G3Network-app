"""Wallet top-up by VietQR bank transfer and the bank notification (PAY-06, BL-15).

Flow:

1. The person asks for a top-up (``create_top_up``): a PENDING ``payments`` row
   gets a unique transfer code and the VietQR string that carries it, the
   configured bank account and the amount. The code waits
   ``BILLING_TOPUP_EXPIRY_MINUTES``.
2. The person transfers the money with the code as the content. The bank
   notification service calls the public webhook, which authenticates the
   shared secret (``verify_webhook_secret``) and hands the notification to
   ``process_bank_notification``.
3. The payment is matched by the code found in the transfer content, marked
   SUCCEEDED with the amount **actually received** (BL-15) and the wallet is
   credited by it, once: the bank's transaction reference is unique and the
   payment row is locked while it changes.

Rules for the odd cases (BL-19): a transfer whose content carries no known code
is only logged (there is no table for it); a transfer for a payment that is
already paid is only logged (not credited twice); a transfer that arrives after
the code expired, or for a different amount, is still credited with the amount
received, because the money is really in the bank; a blocked wallet is credited
too (a block stops spending, it cannot refuse a bank transfer).

Money is whole dong; no floats.
"""

import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.billing.ledger_service as ledger_service
import app.domains.billing.providers as bank_providers
import app.domains.billing.repository as billing_repository
import app.domains.billing.vietqr as vietqr
from app.domains.billing.exceptions import (
    BankProviderUnavailableError,
    PaymentInputError,
    PaymentNotFoundError,
    WalletBlockedError,
    WebhookAuthenticationError,
)
from app.domains.billing.models import PaymentModel
from app.domains.billing.schemas import (
    BankNotificationResponse,
    PaymentResponse,
    TopUpRequest,
    TopUpResponse,
)
from app.domains.billing.types import (
    BankNotification,
    BankNotificationResult,
    PaymentMethod,
    PaymentPurpose,
    PaymentStatus,
    WalletStatus,
    WalletTransactionType,
)
from app.domains.identity.types import Principal, roles_for
from app.libs.common.clock import utc_now
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

# The transfer code is this prefix plus random characters from an alphabet
# without 0/O/1/I, so a person copying it by hand cannot confuse them. The
# whole code fits the 25 characters a VietQR transfer content may hold.
TRANSFER_CODE_PREFIX: Final[str] = "G3NAP"
_TRANSFER_CODE_ALPHABET: Final[str] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_TRANSFER_CODE_RANDOM_LENGTH: Final[int] = 5
_TRANSFER_CODE_ATTEMPTS: Final[int] = 5
_TRANSFER_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"{TRANSFER_CODE_PREFIX}[{_TRANSFER_CODE_ALPHABET}]{{{_TRANSFER_CODE_RANDOM_LENGTH}}}"
)
_NON_ALPHANUMERIC: Final[re.Pattern[str]] = re.compile(r"[^A-Z0-9]")


def calculate_expires_at(payment_record: PaymentModel) -> datetime:
    """Tell when an unpaid top-up code stops waiting.

    Args:
        payment_record: The payment.

    Returns:
        Its creation time plus ``BILLING_TOPUP_EXPIRY_MINUTES``.
    """
    return payment_record.created_at + timedelta(
        minutes=settings.BILLING_TOPUP_EXPIRY_MINUTES
    )


def to_payment_response(payment_record: PaymentModel) -> PaymentResponse:
    """Convert a payment row into its response (pure mapping).

    Args:
        payment_record: The payment.

    Returns:
        The response; money in whole dong.
    """
    return PaymentResponse(
        payment_id=payment_record.payment_id,
        user_id=payment_record.user_id,
        purpose=PaymentPurpose(payment_record.purpose),
        amount=int(payment_record.amount),
        currency=payment_record.currency,
        method=payment_record.method,
        transfer_code=payment_record.transfer_code,
        status=PaymentStatus(payment_record.status),
        gateway_reference=payment_record.gateway_reference,
        expires_at=calculate_expires_at(payment_record),
        created_at=payment_record.created_at,
        completed_at=payment_record.completed_at,
    )


def generate_transfer_code() -> str:
    """Draw a transfer code.

    Returns:
        ``G3NAP`` and five random characters, e.g. ``G3NAP7K2Q9``.
    """
    suffix = "".join(
        secrets.choice(_TRANSFER_CODE_ALPHABET)
        for _ in range(_TRANSFER_CODE_RANDOM_LENGTH)
    )
    return f"{TRANSFER_CODE_PREFIX}{suffix}"


def extract_transfer_code(content: str) -> str | None:
    """Find a transfer code inside the content a bank passes on.

    Banks upper-case the content, drop punctuation and sometimes add text
    before or after it, so everything but letters and digits is removed first.

    Args:
        content: The transfer content as the bank sent it.

    Returns:
        The first code found, or ``None``.
    """
    cleaned = _NON_ALPHANUMERIC.sub("", content.upper())
    match = _TRANSFER_CODE_PATTERN.search(cleaned)
    return None if match is None else match.group(0)


async def create_top_up(
    db: AsyncSession, top_up_request: TopUpRequest, *, principal: Principal
) -> TopUpResponse:
    """Issue a VietQR code for the caller to top up their wallet (PAY-06).

    Args:
        db: The async session owned by the entry boundary.
        top_up_request: The amount to transfer.
        principal: The caller; the wallet is the person's (BL-13).

    Returns:
        The pending payment with the VietQR string and the bank account.

    Raises:
        PaymentInputError: The amount is outside the configured limits (400).
        WalletBlockedError: The wallet is blocked (403).

    Side Effects:
        May create the wallet; inserts one PENDING payment; tells the
        bank-notification provider a transfer is expected.
    """
    amount = top_up_request.amount
    if not (settings.BILLING_TOPUP_MIN_VND <= amount <= settings.BILLING_TOPUP_MAX_VND):
        raise PaymentInputError(
            f"A top-up must be between {settings.BILLING_TOPUP_MIN_VND} and "
            f"{settings.BILLING_TOPUP_MAX_VND} VND"
        )
    wallet_record = await ledger_service.get_or_create_wallet_for_update(
        db, principal.user_id
    )
    if wallet_record.status == WalletStatus.BLOCKED.value:
        raise WalletBlockedError("Your wallet is blocked, so you cannot top it up")
    payment_record: PaymentModel | None = None
    for _ in range(_TRANSFER_CODE_ATTEMPTS):
        transfer_code = generate_transfer_code()
        try:
            async with db.begin_nested():
                payment_record = await billing_repository.insert_payment(
                    db,
                    {
                        "user_id": principal.user_id,
                        "purpose": PaymentPurpose.TOP_UP.value,
                        "amount": Decimal(amount),
                        "currency": ledger_service.CURRENCY_VND,
                        "method": PaymentMethod.BANK_TRANSFER.value,
                        "transfer_code": transfer_code,
                        "status": PaymentStatus.PENDING.value,
                    },
                )
            break
        except IntegrityError:
            # Another payment holds this code: draw a new one.
            continue
    if payment_record is None:
        raise PaymentInputError("Could not issue a transfer code; try again")
    expires_at = calculate_expires_at(payment_record)
    await bank_providers.get_bank_notification_provider().announce_expected_transfer(
        transfer_code=transfer_code, amount=amount, expires_at=expires_at
    )
    payload = vietqr.build_vietqr_payload(
        bank_bin=settings.BILLING_VIETQR_BANK_BIN,
        account_number=settings.BILLING_VIETQR_ACCOUNT_NUMBER,
        amount=amount,
        transfer_content=transfer_code,
    )
    return TopUpResponse(
        **to_payment_response(payment_record).model_dump(),
        vietqr_payload=payload,
        bank_bin=settings.BILLING_VIETQR_BANK_BIN,
        account_number=settings.BILLING_VIETQR_ACCOUNT_NUMBER,
        account_name=settings.BILLING_VIETQR_ACCOUNT_NAME,
    )


async def get_payment(
    db: AsyncSession, payment_id: UUID, *, principal: Principal
) -> PaymentResponse:
    """Get a payment the caller owns, or any payment for internal wallet staff.

    Wallet staff are the internal roles of PAY-13; any other internal user
    (a driver, a support agent) sees their own payments only, because the read
    below can also expire a PENDING payment (RV-BL8).

    A PENDING payment past its expiry is shown, and stored, as FAILED
    ("expired unpaid"); a transfer that still arrives later is credited anyway.

    Args:
        db: The async session owned by the entry boundary.
        payment_id: UUID of the payment.
        principal: The caller.

    Returns:
        The payment.

    Raises:
        PaymentNotFoundError: Unknown, or another person's (404).

    Side Effects:
        May mark an expired PENDING payment FAILED.
    """
    payment_record = await billing_repository.get_payment_by_id(db, payment_id)
    is_wallet_staff = principal.is_internal and principal.has_any_role(
        *roles_for("PAY-13")
    )
    if payment_record is None or (
        not is_wallet_staff and payment_record.user_id != principal.user_id
    ):
        raise PaymentNotFoundError(f"Payment '{payment_id}' was not found")
    now = utc_now()
    if (
        payment_record.status == PaymentStatus.PENDING.value
        and calculate_expires_at(payment_record) <= now
    ):
        payment_record = await billing_repository.get_payment_by_id(
            db, payment_id, for_update=True
        )
        if payment_record is None:
            raise PaymentNotFoundError(f"Payment '{payment_id}' was not found")
        if payment_record.status == PaymentStatus.PENDING.value:
            payment_record.status = PaymentStatus.FAILED.value
            payment_record.completed_at = now
            await db.flush()
    return to_payment_response(payment_record)


def verify_webhook_secret(provided_secret: str | None) -> None:
    """Check the shared secret of a bank-notification call (BL-15).

    The comparison takes the same time whatever the input. An empty
    ``BILLING_WEBHOOK_SECRET`` refuses every call.

    Args:
        provided_secret: The ``X-Webhook-Secret`` header, if sent.

    Raises:
        WebhookAuthenticationError: The secret is missing or wrong (401).
    """
    expected_secret = settings.BILLING_WEBHOOK_SECRET
    if (
        not expected_secret
        or provided_secret is None
        or not hmac.compare_digest(
            provided_secret.encode("utf-8"), expected_secret.encode("utf-8")
        )
    ):
        raise WebhookAuthenticationError("The webhook secret is missing or wrong")


async def process_bank_notification(
    db: AsyncSession, notification: BankNotification
) -> BankNotificationResponse:
    """Match an incoming bank transfer to a top-up and credit the wallet once.

    Rule:
        1. A transaction reference already recorded is a duplicate: nothing
           changes (banks resend).
        2. The transfer code is read from the content; no known code means the
           notification is only logged (UNMATCHED).
        3. The payment is locked. One that already SUCCEEDED is not credited a
           second time (ALREADY_PAID).
        4. Otherwise the payment becomes SUCCEEDED with the amount received
           and the bank reference, and a ``TOP_UP`` line credits the wallet,
           in one transaction.

    Args:
        db: The async session owned by the entry boundary.
        notification: The normalized transfer.

    Returns:
        What happened, and the payment when there was a match.

    Side Effects:
        May update a payment and append a ledger line.
    """
    if notification.amount <= 0:
        return BankNotificationResponse(result=BankNotificationResult.REJECTED)
    method = PaymentMethod.BANK_TRANSFER.value
    existing = await billing_repository.find_payment_by_gateway_reference(
        db, method, notification.bank_transaction_id
    )
    if existing is not None:
        return BankNotificationResponse(
            result=BankNotificationResult.DUPLICATE, payment_id=existing.payment_id
        )
    transfer_code = extract_transfer_code(notification.content)
    payment_record = (
        None
        if transfer_code is None
        else await billing_repository.find_payment_by_transfer_code(
            db, transfer_code, for_update=True
        )
    )
    if payment_record is None:
        logger.warning(
            "Bank transfer matches no top-up",
            extra={
                "bank_transaction_id": notification.bank_transaction_id,
                "amount_vnd": notification.amount,
            },
        )
        return BankNotificationResponse(result=BankNotificationResult.UNMATCHED)
    if payment_record.status == PaymentStatus.SUCCEEDED.value:
        logger.warning(
            "Bank transfer for a top-up that is already paid",
            extra={
                "payment_id": str(payment_record.payment_id),
                "bank_transaction_id": notification.bank_transaction_id,
                "amount_vnd": notification.amount,
            },
        )
        return BankNotificationResponse(
            result=BankNotificationResult.ALREADY_PAID,
            payment_id=payment_record.payment_id,
        )
    amount_received = Decimal(notification.amount)
    amount_differs = amount_received != payment_record.amount
    if amount_differs:
        logger.warning(
            "Bank transfer amount differs from the top-up asked for",
            extra={
                "payment_id": str(payment_record.payment_id),
                "asked_vnd": int(payment_record.amount),
                "received_vnd": notification.amount,
            },
        )
    wallet_record = await ledger_service.get_or_create_wallet_for_update(
        db, payment_record.user_id
    )
    payment_record.amount = amount_received
    payment_record.gateway_reference = notification.bank_transaction_id
    payment_record.status = PaymentStatus.SUCCEEDED.value
    payment_record.completed_at = utc_now()
    await db.flush()
    await ledger_service.apply_wallet_movement(
        db,
        wallet_record,
        transaction_type=WalletTransactionType.TOP_UP,
        amount=amount_received,
        payment_id=payment_record.payment_id,
    )
    return BankNotificationResponse(
        result=BankNotificationResult.CREDITED,
        payment_id=payment_record.payment_id,
        amount_differs=amount_differs,
    )


async def handle_bank_webhook(
    db: AsyncSession, payload: dict[str, object], provided_secret: str | None
) -> BankNotificationResponse:
    """Authenticate and process one bank-notification webhook call.

    Args:
        db: The async session owned by the entry boundary.
        payload: The decoded JSON body, in the configured provider's shape.
        provided_secret: The ``X-Webhook-Secret`` header.

    Returns:
        What happened to the notification.

    Raises:
        WebhookAuthenticationError: The secret is missing or wrong (401).
        PaymentInputError: The body is unusable (400).
    """
    verify_webhook_secret(provided_secret)
    notification = bank_providers.get_bank_notification_provider().parse_notification(
        payload
    )
    return await process_bank_notification(db, notification)


async def simulate_bank_transfer(
    db: AsyncSession,
    *,
    transfer_code: str,
    amount: int | None,
    bank_transaction_id: str | None,
) -> BankNotificationResponse:
    """Make the fake provider report an incoming transfer (development only).

    Refused unless ``BILLING_SIMULATE_TRANSFERS_ENABLED`` is on (BL-26): it
    credits a wallet with no money received.

    Args:
        db: The async session owned by the entry boundary.
        transfer_code: The code of a pending top-up.
        amount: Amount received; the amount asked when omitted.
        bank_transaction_id: The bank reference; a random one when omitted.

    Returns:
        What happened to the notification.

    Raises:
        BankProviderUnavailableError: Simulation is turned off, or the
            provider cannot simulate (409).
        PaymentNotFoundError: No payment carries the code (404).
    """
    if not settings.BILLING_SIMULATE_TRANSFERS_ENABLED:
        raise BankProviderUnavailableError(
            "Simulating bank transfers is turned off "
            "(BILLING_SIMULATE_TRANSFERS_ENABLED)"
        )
    provider = bank_providers.get_bank_notification_provider()
    if not provider.supports_simulation:
        raise BankProviderUnavailableError(
            f"The '{provider.name}' bank provider cannot simulate transfers"
        )
    payment_record = await billing_repository.find_payment_by_transfer_code(
        db, transfer_code
    )
    if payment_record is None:
        raise PaymentNotFoundError(f"No payment carries the code '{transfer_code}'")
    notification = provider.build_simulated_notification(
        bank_transaction_id=bank_transaction_id or f"SIM{secrets.token_hex(8).upper()}",
        amount=amount if amount is not None else int(payment_record.amount),
        content=f"{transfer_code} NAP VI",
    )
    return await process_bank_notification(db, notification)
