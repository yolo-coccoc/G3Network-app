"""FastAPI router for the HTTP endpoints of the billing domain.

Tariffs and their versions (PAY-09), session bills (PAY-10), wallets and the
ledger (PAY-07), and the VietQR top-up with its bank-notification webhook
(PAY-06). Handlers only translate HTTP to service calls; domain exceptions are
mapped once by `app/api/main.py` (not found 404, conflict 409, invalid input
400, unauthenticated 401, permission 403).

The router is mounted at ``/api/v1`` and the paths below carry their full
resource names. ``POST /payments/vietqr/notifications`` is the one public
endpoint: it is called by the bank-notification service, not by a person, and
authenticates a shared secret header instead of a bearer token (BL-15).
``GET /charging-sessions/{id}/bill`` lives in `app/api/charging_session_flow.py`
because it needs the session's own access rule.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.billing.bill_service as bill_service
import app.domains.billing.ledger_service as ledger_service
import app.domains.billing.tariff_service as tariff_service
import app.domains.billing.topup_service as topup_service
from app.domains.billing.schemas import (
    BankNotificationResponse,
    BankNotificationSimulationRequest,
    BillReviewRequest,
    PaymentResponse,
    SessionBillListResponse,
    SessionBillResponse,
    TariffCreateRequest,
    TariffListResponse,
    TariffQuoteResponse,
    TariffResponse,
    TariffStatusRequest,
    TariffUpdateRequest,
    TariffVersionPublishRequest,
    TariffVersionResponse,
    TopUpRequest,
    TopUpResponse,
    WalletAdjustmentRequest,
    WalletResponse,
    WalletStatusRequest,
    WalletTransactionListResponse,
    WalletTransactionResponse,
)
from app.domains.billing.types import ChargingSessionBillStatus, TariffStatus
from app.domains.identity.dependencies import require_roles
from app.domains.identity.types import (
    ALWAYS_ALLOWED_ROLES,
    Principal,
    UserRole,
    roles_for,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["billing"])

# PAY-09: operations staff read tariffs; the drivers' app reads the price of a
# charger it may see. Writing a price is the owner's decision: our
# administrators for the public network, the organization administrator for a
# customer's own chargers (BL-08).
TARIFF_READERS = require_roles(*(roles_for("PAY-09") - {UserRole.DRIVER}))
TARIFF_WRITERS = require_roles(*ALWAYS_ALLOWED_ROLES)
STATION_PRICE_READERS = require_roles(*roles_for("PAY-09"))
# PAY-10: accountants and fleet managers read the bills of their organization
# (PAY-08 lists the same two roles); only our own billing staff review a
# held bill.
BILL_READERS = require_roles(*roles_for("PAY-10", "PAY-08"))
BILL_REVIEWERS = require_roles(*roles_for("PAY-10"), internal_only=True)
# PAY-07 / PAY-06: a person's own wallet and top-ups. Our billing staff (the
# PAY-13 roles: head administrator, accountant, plus administrators) look at
# and correct anybody's wallet.
OWN_WALLET_USERS = require_roles(*roles_for("PAY-07"))
TOP_UP_USERS = require_roles(*roles_for("PAY-06"))
PAYMENT_READERS = require_roles(*roles_for("PAY-06", "PAY-07"))
WALLET_STAFF = require_roles(*roles_for("PAY-13"), internal_only=True)
# Development tool: only the head administrators may fake a bank transfer.
BANK_SIMULATORS = require_roles(
    UserRole.HEAD_ADMIN, UserRole.CO_ADMIN, internal_only=True
)

_PAGE = Query(settings.API_DEFAULT_PAGE, ge=1, description="Page number")
_PAGE_SIZE = Query(
    settings.API_DEFAULT_PAGE_SIZE,
    ge=1,
    le=settings.API_MAX_PAGE_SIZE,
    description="Number of records per page",
)


# --- Tariffs (PAY-09) -------------------------------------------------------


@router.post(
    "/tariffs",
    status_code=status.HTTP_201_CREATED,
    response_model=TariffResponse,
    summary="Create a tariff",
    description="A tariff prices one location, or is the owner's default for all "
    "its locations. Publish a version to give it prices.",
)
async def create_tariff_endpoint(
    tariff_create_request: TariffCreateRequest,
    principal: Principal = Depends(TARIFF_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TariffResponse:
    """Create an ACTIVE tariff.

    Args:
        tariff_create_request: Name, optional location and owner.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown location (404).
        TariffInputError: The location is another owner's (400).
        TariffConflictError: A second ACTIVE tariff for the owner and location (409).
    """
    return await tariff_service.create_tariff(
        db_session, tariff_create_request, principal=principal
    )


@router.get(
    "/tariffs",
    response_model=TariffListResponse,
    summary="List tariffs",
    description="Tariffs of the caller's organization (all for internal staff), "
    "newest first, each with the version in force now.",
)
async def list_tariffs_endpoint(
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    organization_id: UUID | None = Query(
        None, description="Owner filter (internal staff only)"
    ),
    location_id: UUID | None = Query(None, description="Tariff of one location"),
    status_filter: TariffStatus | None = Query(
        None, alias="status", description="Filter by status"
    ),
    principal: Principal = Depends(TARIFF_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TariffListResponse:
    """List tariffs.

    Args:
        page: Page number.
        page_size: Rows per page.
        organization_id: Owner filter, honored for internal staff only.
        location_id: Only the tariff of this location.
        status_filter: Status filter (query parameter ``status``).
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of tariffs.
    """
    return await tariff_service.list_tariffs(
        db_session,
        principal=principal,
        page=page,
        page_size=page_size,
        organization_id=organization_id,
        location_id=location_id,
        status=status_filter,
    )


@router.get(
    "/tariffs/in-force",
    response_model=TariffQuoteResponse,
    summary="Price of a charger now",
    description="The price a charger asks at a moment (default now): the tariff "
    "of its location, else its owner's default, with the time-of-use price of "
    "that hour.",
)
async def get_station_price_endpoint(
    station_id: UUID = Query(..., description="The charger"),
    at: datetime | None = Query(
        None, description="Moment to price (with timezone); default now"
    ),
    principal: Principal = Depends(STATION_PRICE_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TariffQuoteResponse:
    """Quote the price of a charger the caller may see.

    Args:
        station_id: The charger.
        at: The moment to price.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The quote.

    Raises:
        ChargingStationNotFoundError: Unknown or not visible charger (404).
        NoTariffInForceError: No tariff prices the charger (409).
    """
    return await tariff_service.get_station_price(
        db_session, station_id, at, principal=principal
    )


@router.get(
    "/tariffs/{tariff_id}",
    response_model=TariffResponse,
    summary="Get a tariff",
)
async def get_tariff_endpoint(
    tariff_id: UUID,
    principal: Principal = Depends(TARIFF_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TariffResponse:
    """Get a tariff with its current version.

    Args:
        tariff_id: UUID of the tariff.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
    """
    return await tariff_service.get_tariff(db_session, tariff_id, principal=principal)


@router.patch(
    "/tariffs/{tariff_id}",
    response_model=TariffResponse,
    summary="Rename a tariff",
)
async def update_tariff_endpoint(
    tariff_id: UUID,
    tariff_update_request: TariffUpdateRequest,
    principal: Principal = Depends(TARIFF_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TariffResponse:
    """Rename a tariff (prices change only through new versions).

    Args:
        tariff_id: UUID of the tariff.
        tariff_update_request: The new name.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
    """
    return await tariff_service.update_tariff(
        db_session, tariff_id, tariff_update_request, principal=principal
    )


@router.post(
    "/tariffs/{tariff_id}/retire",
    response_model=TariffResponse,
    summary="Retire a tariff",
)
async def retire_tariff_endpoint(
    tariff_id: UUID,
    status_request: TariffStatusRequest,
    principal: Principal = Depends(TARIFF_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TariffResponse:
    """Retire a tariff (INACTIVE, with the reason).

    Args:
        tariff_id: UUID of the tariff.
        status_request: The reason.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
        TariffConflictError: Already retired (409).
    """
    return await tariff_service.retire_tariff(
        db_session, tariff_id, status_request.reason, principal=principal
    )


@router.post(
    "/tariffs/{tariff_id}/reactivate",
    response_model=TariffResponse,
    summary="Put a retired tariff back in use",
)
async def reactivate_tariff_endpoint(
    tariff_id: UUID,
    status_request: TariffStatusRequest,
    principal: Principal = Depends(TARIFF_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TariffResponse:
    """Reactivate a retired tariff.

    Args:
        tariff_id: UUID of the tariff.
        status_request: The reason.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
        TariffConflictError: Already active, or another active tariff holds
            its owner and location (409).
    """
    return await tariff_service.reactivate_tariff(
        db_session, tariff_id, status_request.reason, principal=principal
    )


@router.post(
    "/tariffs/{tariff_id}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=TariffVersionResponse,
    summary="Publish a new tariff version",
    description="A version is never edited: a price change is a new version, "
    "effective now or later.",
)
async def publish_tariff_version_endpoint(
    tariff_id: UUID,
    publish_request: TariffVersionPublishRequest,
    principal: Principal = Depends(TARIFF_WRITERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TariffVersionResponse:
    """Publish the next version of a tariff.

    Args:
        tariff_id: UUID of the tariff.
        publish_request: Prices, periods, VAT, start and reason.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The new version.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
        TariffConflictError: The tariff is retired (409).
        TariffInputError: A date in the past, or bad or overlapping periods (400).
    """
    return await tariff_service.publish_tariff_version(
        db_session, tariff_id, publish_request, principal=principal
    )


@router.get(
    "/tariffs/{tariff_id}/versions",
    response_model=list[TariffVersionResponse],
    summary="List the versions of a tariff",
)
async def list_tariff_versions_endpoint(
    tariff_id: UUID,
    principal: Principal = Depends(TARIFF_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> list[TariffVersionResponse]:
    """List every version of a tariff, newest number first.

    Args:
        tariff_id: UUID of the tariff.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The versions.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
    """
    return await tariff_service.list_tariff_versions(
        db_session, tariff_id, principal=principal
    )


# --- Session bills (PAY-10) -------------------------------------------------


@router.get(
    "/charging-session-bills",
    response_model=SessionBillListResponse,
    summary="List session bills",
    description="Bills of the sessions the caller's organization paid for (all "
    "organizations for internal staff), newest first.",
)
async def list_session_bills_endpoint(
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    organization_id: UUID | None = Query(
        None, description="Payer organization (internal staff only)"
    ),
    started_by: UUID | None = Query(None, description="User who scanned"),
    status_filter: ChargingSessionBillStatus | None = Query(
        None, alias="status", description="Filter by bill status"
    ),
    created_from: datetime | None = Query(None, alias="from"),
    created_to: datetime | None = Query(None, alias="to"),
    principal: Principal = Depends(BILL_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> SessionBillListResponse:
    """List session bills.

    Args:
        page: Page number.
        page_size: Rows per page.
        organization_id: Payer filter, honored for internal staff only.
        started_by: Only bills of sessions this user scanned.
        status_filter: Bill status (query parameter ``status``).
        created_from: Only bills scanned at or after this time (``from``).
        created_to: Only bills scanned before this time (``to``).
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of bills.
    """
    return await bill_service.list_session_bills(
        db_session,
        principal=principal,
        page=page,
        page_size=page_size,
        organization_id=organization_id,
        started_by=started_by,
        status=status_filter,
        created_from=created_from,
        created_to=created_to,
    )


@router.post(
    "/charging-session-bills/{bill_id}/release",
    response_model=SessionBillResponse,
    summary="Release a bill that is on hold",
    description="Bills the held amounts and debits the payer's wallet.",
)
async def release_session_bill_endpoint(
    bill_id: UUID,
    review_request: BillReviewRequest,
    principal: Principal = Depends(BILL_REVIEWERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> SessionBillResponse:
    """Release an ON_HOLD bill.

    Args:
        bill_id: UUID of the bill.
        review_request: What the reviewer checked.
        principal: The authenticated billing staff member.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The bill.

    Raises:
        BillNotFoundError: Unknown bill (404).
        BillStateError: Not on hold, or no computed amounts (409).
    """
    return await bill_service.release_bill(
        db_session, bill_id, review_request.reason, principal=principal
    )


@router.post(
    "/charging-session-bills/{bill_id}/void",
    response_model=SessionBillResponse,
    summary="Void a bill that is on hold",
)
async def void_session_bill_endpoint(
    bill_id: UUID,
    review_request: BillReviewRequest,
    principal: Principal = Depends(BILL_REVIEWERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> SessionBillResponse:
    """Void an ON_HOLD bill: nothing is charged.

    Args:
        bill_id: UUID of the bill.
        review_request: Why nothing is charged.
        principal: The authenticated billing staff member.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The bill.

    Raises:
        BillNotFoundError: Unknown bill (404).
        BillStateError: Not on hold (409).
    """
    return await bill_service.void_held_bill(
        db_session, bill_id, review_request.reason, principal=principal
    )


# --- Wallets (PAY-07) and top-ups (PAY-06) ----------------------------------


@router.get(
    "/wallets/me",
    response_model=WalletResponse,
    summary="My wallet",
    description="The caller's own wallet, created on first use. The balance can "
    "be negative after a long charge.",
)
async def get_my_wallet_endpoint(
    principal: Principal = Depends(OWN_WALLET_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WalletResponse:
    """Get the caller's wallet.

    Args:
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The wallet.
    """
    return await ledger_service.get_my_wallet(db_session, principal=principal)


@router.get(
    "/wallets/me/transactions",
    response_model=WalletTransactionListResponse,
    summary="My wallet statement",
)
async def list_my_wallet_transactions_endpoint(
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    principal: Principal = Depends(OWN_WALLET_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WalletTransactionListResponse:
    """List the caller's ledger lines, newest first.

    Args:
        page: Page number.
        page_size: Rows per page.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of ledger lines.
    """
    return await ledger_service.list_my_wallet_transactions(
        db_session, principal=principal, page=page, page_size=page_size
    )


@router.post(
    "/wallets/me/top-ups",
    status_code=status.HTTP_201_CREATED,
    response_model=TopUpResponse,
    summary="Ask for a VietQR code to top up my wallet",
    description="Returns the VietQR string to draw, the transfer code the "
    "transfer must carry, and when the code expires. Poll GET /payments/{id}.",
)
async def create_top_up_endpoint(
    top_up_request: TopUpRequest,
    principal: Principal = Depends(TOP_UP_USERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> TopUpResponse:
    """Issue a top-up QR code.

    Args:
        top_up_request: The amount to transfer.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The pending payment and the QR content.

    Raises:
        PaymentInputError: Amount outside the limits (400).
        WalletBlockedError: The wallet is blocked (403).
    """
    return await topup_service.create_top_up(
        db_session, top_up_request, principal=principal
    )


@router.get(
    "/wallets/{user_id}",
    response_model=WalletResponse,
    summary="A person's wallet (billing staff)",
)
async def get_user_wallet_endpoint(
    user_id: UUID,
    principal: Principal = Depends(WALLET_STAFF),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WalletResponse:
    """Get a person's wallet.

    Args:
        user_id: The person.
        principal: The authenticated billing staff member.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The wallet.

    Raises:
        WalletNotFoundError: The person has no wallet (404).
    """
    del principal
    return await ledger_service.get_user_wallet(db_session, user_id)


@router.get(
    "/wallets/{user_id}/transactions",
    response_model=WalletTransactionListResponse,
    summary="A person's wallet statement (billing staff)",
)
async def list_user_wallet_transactions_endpoint(
    user_id: UUID,
    page: int = _PAGE,
    page_size: int = _PAGE_SIZE,
    principal: Principal = Depends(WALLET_STAFF),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WalletTransactionListResponse:
    """List a person's ledger lines, newest first.

    Args:
        user_id: The person.
        page: Page number.
        page_size: Rows per page.
        principal: The authenticated billing staff member.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        A page of ledger lines.

    Raises:
        WalletNotFoundError: The person has no wallet (404).
    """
    del principal
    return await ledger_service.list_user_wallet_transactions(
        db_session, user_id, page=page, page_size=page_size
    )


@router.post(
    "/wallets/{user_id}/adjustments",
    status_code=status.HTTP_201_CREATED,
    response_model=WalletTransactionResponse,
    summary="Adjust a wallet balance (billing staff)",
    description="Adds an ADJUSTMENT line with the typed reason; the amount is "
    "signed and not zero.",
)
async def adjust_wallet_endpoint(
    user_id: UUID,
    adjustment_request: WalletAdjustmentRequest,
    principal: Principal = Depends(WALLET_STAFF),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WalletTransactionResponse:
    """Adjust a balance by hand.

    Args:
        user_id: The person.
        adjustment_request: Signed amount and reason.
        principal: The authenticated billing staff member.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The ledger line.

    Raises:
        WalletInputError: Zero amount or blank reason (400).
        WalletNotFoundError: Unknown user (404).
    """
    return await ledger_service.adjust_wallet(
        db_session, user_id, adjustment_request, principal=principal
    )


@router.post(
    "/wallets/{user_id}/status",
    response_model=WalletResponse,
    summary="Block or unblock a wallet (billing staff)",
)
async def set_wallet_status_endpoint(
    user_id: UUID,
    status_request: WalletStatusRequest,
    principal: Principal = Depends(WALLET_STAFF),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> WalletResponse:
    """Block or unblock a wallet.

    Args:
        user_id: The person.
        status_request: The status and the reason.
        principal: The authenticated billing staff member.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The wallet.

    Raises:
        WalletNotFoundError: The person has no wallet (404).
        WalletConflictError: Already in that status (409).
    """
    return await ledger_service.set_wallet_status(
        db_session, user_id, status_request, principal=principal
    )


# --- Payments ---------------------------------------------------------------


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentResponse,
    summary="Get a payment",
    description="Poll this after showing the QR code: PENDING until the bank "
    "tells us the money arrived, then SUCCEEDED; FAILED when the code expired "
    "unpaid.",
)
async def get_payment_endpoint(
    payment_id: UUID,
    principal: Principal = Depends(PAYMENT_READERS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> PaymentResponse:
    """Get a payment of the caller (any payment for internal staff).

    Args:
        payment_id: UUID of the payment.
        principal: The authenticated caller.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        The payment.

    Raises:
        PaymentNotFoundError: Unknown, or another person's (404).
    """
    return await topup_service.get_payment(db_session, payment_id, principal=principal)


@router.post(
    "/payments/vietqr/notifications",
    response_model=BankNotificationResponse,
    summary="Bank notification webhook (bank-notification service)",
    description="Public endpoint for the bank-notification service. It must send "
    "the shared secret in the X-Webhook-Secret header. Credits the wallet of the "
    "matched top-up exactly once per bank transaction.",
)
async def receive_bank_notification_endpoint(
    payload: dict[str, Any] = Body(...),
    webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BankNotificationResponse:
    """Receive one incoming bank transfer.

    Args:
        payload: The notification body, in the configured provider's shape.
        webhook_secret: The shared secret header.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        What happened to the notification (always 200 once authenticated, so
        the service does not retry an unmatched or duplicate transfer).

    Raises:
        WebhookAuthenticationError: The secret is missing or wrong (401).
        PaymentInputError: The body is unusable (400).
    """
    return await topup_service.handle_bank_webhook(db_session, payload, webhook_secret)


@router.post(
    "/payments/vietqr/simulate",
    response_model=BankNotificationResponse,
    summary="Simulate a bank transfer (development)",
    description="With the fake bank provider, makes it report an incoming "
    "transfer for a pending top-up, exactly as the webhook would.",
)
async def simulate_bank_transfer_endpoint(
    simulation_request: BankNotificationSimulationRequest,
    principal: Principal = Depends(BANK_SIMULATORS),
    db_session: AsyncSession = Depends(get_db, scope="function"),
) -> BankNotificationResponse:
    """Simulate an incoming transfer through the provider.

    Args:
        simulation_request: The code, optional amount and bank reference.
        principal: The authenticated head administrator.
        db_session: Database session owned by the HTTP boundary.

    Returns:
        What happened to the simulated notification.

    Raises:
        BankProviderUnavailableError: The provider cannot simulate (409).
        PaymentNotFoundError: No payment carries the code (404).
    """
    del principal
    return await topup_service.simulate_bank_transfer(
        db_session,
        transfer_code=simulation_request.transfer_code,
        amount=simulation_request.amount,
        bank_transaction_id=simulation_request.bank_transaction_id,
    )
