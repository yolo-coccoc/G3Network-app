"""Review tests for billing money rules: guards that hold today and known defects.

Guards (they pass today) pin money-critical behaviour verified in the review:
the bank webhook's shared secret, the once-only credit of a bank transaction,
a person's payments, wallets and bills staying theirs, and the time-of-use and
rounding rules of a bill.

Tests marked ``xfail(strict=True)`` describe the correct behaviour of a defect
found in the review (``REVIEW BL-n``); the fix makes them pass, which strict
mode turns into a failure until the marker is removed. The in-memory
repository is the ``store`` fixture of ``test_billing_services_smoke``.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import app.domains.billing.bill_service as bill_service
import app.domains.billing.ledger_service as ledger_service
import app.domains.billing.pricing as pricing
import app.domains.billing.repository as billing_repository
import app.domains.billing.router as billing_router
import app.domains.billing.topup_service as topup_service
from app.domains.billing.exceptions import (
    BankProviderUnavailableError,
    PaymentInputError,
    PaymentNotFoundError,
    WebhookAuthenticationError,
)
from app.domains.billing.models import PaymentModel
from app.domains.billing.providers import FakeBankNotificationProvider
from app.domains.billing.schemas import (
    TariffVersionPublishRequest,
    WalletAdjustmentRequest,
)
from app.domains.billing.types import BankNotificationResult
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import Principal, UserRole
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from tests.billing import test_billing_services_smoke as services_smoke
from tests.principals import OTHER_ORGANIZATION_ID, build_principal

# The in-memory billing repository of the services smoke tests, reused as is.
store = services_smoke.store
Store = services_smoke.Store
DB: Any = services_smoke.FakeDb()
WEBHOOK_SECRET = "review-secret"
TRANSFER_CODE = "G3NAPREV23"


def _pending_top_up(store: Store, *, user_id: UUID | None = None) -> PaymentModel:
    """Add a PENDING 500,000 VND top-up carrying ``TRANSFER_CODE`` to the store.

    Args:
        store: The in-memory repository.
        user_id: The owner; a random person when omitted.

    Returns:
        The payment.
    """
    payment = PaymentModel(
        payment_id=uuid4(),
        user_id=user_id or uuid4(),
        purpose="TOP_UP",
        amount=Decimal(500_000),
        currency="VND",
        method="BANK_TRANSFER",
        transfer_code=TRANSFER_CODE,
        status="PENDING",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    store.payments[payment.payment_id] = payment
    return payment


def _webhook_body(reference: str = "FT-REVIEW-1") -> dict[str, object]:
    """Build a fake-provider webhook body paying the pending top-up in full.

    Args:
        reference: The bank's transaction reference.

    Returns:
        The decoded JSON body.
    """
    return {
        "bank_transaction_id": reference,
        "amount": 500_000,
        "content": f"MBVCB.123.{TRANSFER_CODE}.NAP VI",
    }


def _patch_payment_lookup(monkeypatch: pytest.MonkeyPatch, store: Store) -> None:
    """Serve ``get_payment_by_id`` from the store (the fixture does not).

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        store: The in-memory repository.
    """

    async def get_payment(db: object, payment_id: UUID, **_: object) -> Any:
        """Return the stored payment, if any."""
        return store.payments.get(payment_id)

    monkeypatch.setattr(billing_repository, "get_payment_by_id", get_payment)


async def _is_allowed(gate: Any, principal: Principal) -> bool:
    """Tell whether a role gate lets a principal through.

    Args:
        gate: A dependency built by ``require_roles``.
        principal: The caller.

    Returns:
        ``True`` when the gate admits the caller.
    """
    try:
        await gate(principal=principal)
    except AccessDeniedError:
        return False
    return True


# --- Bank webhook (guards) ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_secret", "provided_secret"),
    [
        (WEBHOOK_SECRET, None),
        (WEBHOOK_SECRET, "wrong-secret"),
        (WEBHOOK_SECRET, WEBHOOK_SECRET + " "),
        ("", ""),
        ("", None),
    ],
)
async def test_webhook_without_the_right_secret_is_refused_and_credits_nothing(
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    configured_secret: str,
    provided_secret: str | None,
) -> None:
    """A missing or wrong secret, or no secret configured at all, is a 401."""
    monkeypatch.setattr(settings, "BILLING_WEBHOOK_SECRET", configured_secret)
    payment = _pending_top_up(store)

    with pytest.raises(WebhookAuthenticationError):
        await topup_service.handle_bank_webhook(DB, _webhook_body(), provided_secret)

    assert payment.status == "PENDING"
    assert store.ledger == []
    assert store.wallets == {}


@pytest.mark.asyncio
async def test_the_same_bank_transaction_delivered_twice_credits_the_wallet_once(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resent notification is DUPLICATE: one TOP_UP line, one balance move."""
    monkeypatch.setattr(settings, "BILLING_WEBHOOK_SECRET", WEBHOOK_SECRET)
    payment = _pending_top_up(store)

    first = await topup_service.handle_bank_webhook(DB, _webhook_body(), WEBHOOK_SECRET)
    second = await topup_service.handle_bank_webhook(
        DB, _webhook_body(), WEBHOOK_SECRET
    )

    assert first.result is BankNotificationResult.CREDITED
    assert second.result is BankNotificationResult.DUPLICATE
    assert second.payment_id == payment.payment_id
    assert [(row.transaction_type, row.amount) for row in store.ledger] == [
        ("TOP_UP", Decimal(500_000))
    ]
    assert store.wallets[payment.user_id].balance == Decimal(500_000)


# --- A person's own money (IDOR guards) ---------------------------------------


@pytest.mark.asyncio
async def test_a_customer_cannot_read_another_persons_payment(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another person's payment does not exist for a customer (404)."""
    _patch_payment_lookup(monkeypatch, store)
    payment = _pending_top_up(store)
    stranger = build_principal(roles=frozenset({UserRole.DRIVER}), user_id=uuid4())

    with pytest.raises(PaymentNotFoundError):
        await topup_service.get_payment(DB, payment.payment_id, principal=stranger)

    owner = build_principal(roles=frozenset({UserRole.DRIVER}), user_id=payment.user_id)
    own = await topup_service.get_payment(DB, payment.payment_id, principal=owner)
    assert own.payment_id == payment.payment_id


@pytest.mark.asyncio
async def test_my_wallet_is_always_the_callers_own(store: Store) -> None:
    """``/wallets/me`` returns the caller's wallet, never another person's."""
    me = build_principal(roles=frozenset({UserRole.DRIVER}), user_id=uuid4())
    other_user_id = uuid4()
    await ledger_service.adjust_wallet(
        DB,
        other_user_id,
        WalletAdjustmentRequest(amount=900_000, reason="Other person's money"),
        principal=build_principal(is_internal=True),
    )

    wallet = await ledger_service.get_my_wallet(DB, principal=me)

    assert wallet.user_id == me.user_id
    assert wallet.balance == 0
    assert store.wallets[other_user_id].balance == Decimal(900_000)


@pytest.mark.asyncio
async def test_other_peoples_wallets_are_for_internal_billing_staff_only() -> None:
    """Customers and our own drivers cannot read or adjust another wallet."""
    internal_driver = build_principal(
        roles=frozenset({UserRole.DRIVER}), is_internal=True
    )
    customer_accountant = build_principal(roles=frozenset({UserRole.ACCOUNTANT}))
    internal_accountant = build_principal(
        roles=frozenset({UserRole.ACCOUNTANT}), is_internal=True
    )

    assert not await _is_allowed(billing_router.WALLET_STAFF, internal_driver)
    assert not await _is_allowed(billing_router.WALLET_STAFF, customer_accountant)
    assert await _is_allowed(billing_router.WALLET_STAFF, internal_accountant)


@pytest.mark.asyncio
async def test_a_customer_bill_list_stays_in_its_own_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming another payer organization does not widen a customer's bill list."""
    captured: dict[str, Any] = {}

    async def list_bills(db: object, **kwargs: Any) -> tuple[list[Any], int]:
        """Record the filters and return no rows."""
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(billing_repository, "list_bills", list_bills)
    accountant = build_principal(roles=frozenset({UserRole.ACCOUNTANT}))

    await bill_service.list_session_bills(
        DB,
        principal=accountant,
        page=1,
        page_size=20,
        organization_id=OTHER_ORGANIZATION_ID,
        started_by=None,
        status=None,
        created_from=None,
        created_to=None,
    )

    assert captured["organization_id"] == accountant.organization_id


# --- Price rules (guards) -----------------------------------------------------

# Stored time-of-use periods: Saturday night into Sunday, a Monday early window
# and a Monday late-morning peak; everything else is the normal price.
_PERIODS: list[dict[str, object]] = [
    {"days": ["SAT"], "from": "22:00", "to": "04:00", "price_per_kwh": 3200},
    {"days": ["MON"], "from": "05:00", "to": "07:00", "price_per_kwh": 5000},
    {"days": ["MON"], "from": "09:30", "to": "11:30", "price_per_kwh": 6000},
]
_NORMAL_PRICE = Decimal(4000)


def _utc(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    """Build a January 2027 moment in UTC (Vietnam time is UTC+7).

    Args:
        day: Day of January 2027 (the 4th is a Monday).
        hour: Hour in UTC.
        minute: Minute.
        second: Second.

    Returns:
        The timezone-aware moment.
    """
    return datetime(2027, 1, day, hour, minute, second, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("moment", "expected_price"),
    [
        (_utc(4, 2, 29, 59), _NORMAL_PRICE),  # Mon 09:29:59 Vietnam time
        (_utc(4, 2, 30), Decimal(6000)),  # Mon 09:30:00, the peak starts
        (_utc(4, 4, 29, 59), Decimal(6000)),  # Mon 11:29:59
        (_utc(4, 4, 30), _NORMAL_PRICE),  # Mon 11:30:00, the end is exclusive
        (_utc(9, 14, 59, 59), _NORMAL_PRICE),  # Sat 21:59:59
        (_utc(9, 15, 0), Decimal(3200)),  # Sat 22:00:00, night starts
        (_utc(9, 20, 59, 59), Decimal(3200)),  # Sun 03:59:59, still night
        (_utc(9, 21, 0), _NORMAL_PRICE),  # Sun 04:00:00, night ended
        (_utc(3, 22, 30), Decimal(5000)),  # Sunday in UTC, Mon 05:30 in Vietnam
        (_utc(4, 23, 0), _NORMAL_PRICE),  # Monday in UTC, Tue 06:00 in Vietnam
    ],
)
def test_time_of_use_boundaries_are_read_in_vietnam_time(
    moment: datetime, expected_price: Decimal
) -> None:
    """A period covers its start minute but not its end, in Asia/Ho_Chi_Minh."""
    assert pricing.price_at(_NORMAL_PRICE, _PERIODS, moment) == expected_price


@pytest.mark.parametrize(
    ("energy_wh", "price_per_kwh", "vat_percent", "expected"),
    [
        # 0.5 kWh x 3001 = 1500.5 rounds half up (not to even); VAT 150.1 -> 150.
        (Decimal(500), Decimal(3001), Decimal(10), (Decimal(1501), Decimal(150))),
        # 1005 before VAT; VAT 100.5 rounds half up to 101.
        (Decimal(1005), Decimal(1000), Decimal(10), (Decimal(1005), Decimal(101))),
        # Fractional Wh: 12.345678 kWh x 3858 = 47629.63 -> 47630; VAT 3810.4.
        (
            Decimal("12345.678"),
            Decimal(3858),
            Decimal(8),
            (Decimal(47630), Decimal(3810)),
        ),
        # 0.499 kWh x 1 = 0.499 -> 0 dong, so nothing is charged.
        (Decimal(499), Decimal(1), Decimal(10), (Decimal(0), Decimal(0))),
    ],
)
def test_bill_amounts_convert_wh_to_kwh_and_round_half_up_to_whole_dong(
    energy_wh: Decimal,
    price_per_kwh: Decimal,
    vat_percent: Decimal,
    expected: tuple[Decimal, Decimal],
) -> None:
    """Amount = Wh / 1000 x price, then VAT on the rounded amount (BL-21)."""
    amounts = pricing.calculate_bill_amounts(energy_wh, price_per_kwh, vat_percent)

    assert amounts == expected
    assert all(isinstance(value, Decimal) for value in amounts)


# --- Defects found in the review (strict xfail) -------------------------------


@pytest.mark.asyncio
async def test_simulating_a_bank_transfer_is_refused_outside_development(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the switch off (the default) nobody can mint a TOP_UP (RV-BL6, BL-26)."""
    monkeypatch.setattr(settings, "BILLING_SIMULATE_TRANSFERS_ENABLED", False)
    payment = _pending_top_up(store)

    with pytest.raises(BankProviderUnavailableError):
        await topup_service.simulate_bank_transfer(
            DB,
            transfer_code=TRANSFER_CODE,
            amount=1_000_000_000,
            bank_transaction_id="FT-REAL-UPCOMING",
        )

    assert payment.status == "PENDING"
    assert store.ledger == []


@pytest.mark.asyncio
async def test_simulating_a_bank_transfer_credits_the_wallet_when_switched_on(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A development deployment turns the switch on and the top-up is credited."""
    monkeypatch.setattr(settings, "BILLING_SIMULATE_TRANSFERS_ENABLED", True)
    payment = _pending_top_up(store)

    await topup_service.simulate_bank_transfer(
        DB, transfer_code=TRANSFER_CODE, amount=None, bank_transaction_id=None
    )

    assert payment.status == "SUCCEEDED"
    assert len(store.ledger) == 1


def test_a_webhook_amount_beyond_the_money_columns_is_refused() -> None:
    """An amount the ``numeric(14,2)`` columns cannot hold is a 400, not a 500."""
    provider = FakeBankNotificationProvider()

    with pytest.raises(PaymentInputError):
        provider.parse_notification(
            {"bank_transaction_id": "FT-HUGE", "amount": 10**13, "content": "x"}
        )


@pytest.mark.parametrize(
    "build_request",
    [
        pytest.param(
            lambda: TariffVersionPublishRequest(
                price_per_kwh=4000,
                vat_rate_percent=Decimal(100),
                change_reason="VAT the column cannot hold",
            ),
            id="vat-100-overflows-numeric-4-2",
        ),
        pytest.param(
            lambda: TariffVersionPublishRequest(
                price_per_kwh=10**10,
                vat_rate_percent=Decimal(8),
                change_reason="Price the column cannot hold",
            ),
            id="price-overflows-numeric-12-2",
        ),
        pytest.param(
            lambda: WalletAdjustmentRequest(
                amount=10**12, reason="Amount the column cannot hold"
            ),
            id="adjustment-overflows-numeric-14-2",
        ),
    ],
)
def test_requests_beyond_the_money_columns_are_refused_by_validation(
    build_request: Any,
) -> None:
    """Values the database columns cannot store fail validation (422)."""
    with pytest.raises(ValidationError):
        build_request()


@pytest.mark.asyncio
async def test_an_internal_driver_cannot_read_another_persons_payment(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only billing staff, not every internal user, see other people's payments."""
    _patch_payment_lookup(monkeypatch, store)
    payment = _pending_top_up(store)
    payment.created_at = utc_now() - timedelta(hours=1)
    internal_driver = build_principal(
        roles=frozenset({UserRole.DRIVER}), is_internal=True, user_id=uuid4()
    )

    with pytest.raises(PaymentNotFoundError):
        await topup_service.get_payment(
            DB, payment.payment_id, principal=internal_driver
        )

    assert payment.status == "PENDING"
