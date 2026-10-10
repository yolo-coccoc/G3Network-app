"""Smoke tests for the billing services with an in-memory repository (WP9).

The repository functions are replaced by a dictionary-backed store, so these
tests run the real tariff, ledger, bill and top-up rules without a database;
the PostgreSQL integration test runs the same flow against real tables.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

import app.domains.billing.bill_service as bill_service
import app.domains.billing.ledger_service as ledger_service
import app.domains.billing.repository as billing_repository
import app.domains.billing.tariff_service as tariff_service
import app.domains.billing.topup_service as topup_service
import app.domains.charging_stations.service as stations_service
from app.domains.billing.exceptions import (
    BillStateError,
    NoTariffInForceError,
    TariffConflictError,
    TariffInputError,
    WalletInputError,
)
from app.domains.billing.models import (
    ChargingSessionBillModel,
    PaymentModel,
    TariffModel,
    TariffVersionModel,
    WalletModel,
    WalletTransactionModel,
)
from app.domains.billing.schemas import (
    TariffVersionPublishRequest,
    WalletAdjustmentRequest,
)
from app.domains.billing.types import (
    BankNotification,
    BankNotificationResult,
    ChargingSessionBillStatus,
    TariffQuote,
)
from app.domains.charging_stations.types import StationLocationReference
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from tests.principals import build_internal_principal, build_principal


class FakeDb:
    """The few session methods the billing code calls, doing nothing."""

    async def flush(self) -> None:
        """Pretend to flush."""

    async def refresh(self, _record: object) -> None:
        """Pretend to refresh."""

    def begin_nested(self) -> "FakeDb":
        """Open a savepoint that never fails."""
        return self

    async def __aenter__(self) -> "FakeDb":
        """Enter the savepoint."""
        return self

    async def __aexit__(self, *_: object) -> bool:
        """Leave the savepoint without hiding errors."""
        return False


class Store:
    """In-memory rows behind the patched repository functions.

    Attributes:
        tariffs: Tariffs by ID.
        versions: Tariff versions.
        bills: Bills by session ID.
        wallets: Wallets by user ID.
        ledger: Ledger rows in order.
        payments: Payments by ID.
        payers: Payer (organization, user) by session ID.
        change_reasons: Reasons given to ``set_change_context``.
    """

    def __init__(self) -> None:
        """Start empty."""
        self.tariffs: dict[UUID, TariffModel] = {}
        self.versions: list[TariffVersionModel] = []
        self.bills: dict[UUID, ChargingSessionBillModel] = {}
        self.wallets: dict[UUID, WalletModel] = {}
        self.ledger: list[WalletTransactionModel] = []
        self.payments: dict[UUID, PaymentModel] = {}
        self.payers: dict[UUID, tuple[UUID, UUID]] = {}
        self.change_reasons: list[str] = []


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    """Replace the billing repository and the change context by a `Store`."""
    data = Store()
    repo: Any = billing_repository

    async def find_active_tariff(
        db: object, organization_id: UUID, location_id: UUID | None
    ) -> TariffModel | None:
        return next(
            (
                t
                for t in data.tariffs.values()
                if t.organization_id == organization_id
                and t.location_id == location_id
                and t.status == "ACTIVE"
            ),
            None,
        )

    async def get_tariff(
        db: object, tariff_id: UUID, *, organization_id: UUID | None = None, **_: Any
    ) -> TariffModel | None:
        tariff = data.tariffs.get(tariff_id)
        if tariff and organization_id and tariff.organization_id != organization_id:
            return None
        return tariff

    async def latest_version(db: object, tariff_id: UUID) -> Any:
        mine = [v for v in data.versions if v.tariff_id == tariff_id]
        return max(mine, key=lambda v: v.version_no, default=None)

    async def insert_version(db: object, values: dict[str, Any]) -> Any:
        version = TariffVersionModel(
            tariff_version_id=uuid4(), created_at=utc_now(), **values
        )
        data.versions.append(version)
        return version

    async def version_in_force(db: object, tariff_id: UUID, at: datetime) -> Any:
        mine = [
            v
            for v in data.versions
            if v.tariff_id == tariff_id and v.effective_from <= at
        ]
        return max(mine, key=lambda v: (v.effective_from, v.version_no), default=None)

    async def bill_by_session(db: object, session_id: UUID, **_: Any) -> Any:
        return data.bills.get(session_id)

    async def bill_by_id(db: object, bill_id: UUID, **_: Any) -> Any:
        return next(
            (b for b in data.bills.values() if b.charging_session_bill_id == bill_id),
            None,
        )

    async def payer(db: object, session_id: UUID) -> Any:
        return data.payers.get(session_id)

    async def wallet_by_user(db: object, user_id: UUID, **_: Any) -> Any:
        return data.wallets.get(user_id)

    async def insert_wallet(db: object, values: dict[str, Any]) -> WalletModel:
        wallet = WalletModel(wallet_id=uuid4(), **values)
        data.wallets[wallet.user_id] = wallet
        return wallet

    async def update_balance(db: object, wallet_id: UUID, new_balance: Decimal) -> None:
        for wallet in data.wallets.values():
            if wallet.wallet_id == wallet_id:
                wallet.balance = new_balance

    async def insert_transaction(db: object, values: dict[str, Any]) -> Any:
        row = WalletTransactionModel(wallet_transaction_id=uuid4(), **values)
        data.ledger.append(row)
        return row

    async def payment_by_code(db: object, code: str, **_: Any) -> Any:
        return next(
            (p for p in data.payments.values() if p.transfer_code == code), None
        )

    async def payment_by_reference(db: object, method: str, reference: str) -> Any:
        return next(
            (p for p in data.payments.values() if p.gateway_reference == reference),
            None,
        )

    async def is_bill_paid(db: object, bill_id: UUID) -> bool:
        return any(row.charging_session_bill_id == bill_id for row in data.ledger)

    async def set_context(
        db: object, *, changed_by: object, change_reason: str
    ) -> None:
        data.change_reasons.append(change_reason)

    for name, function in {
        "find_active_tariff": find_active_tariff,
        "get_tariff_by_id": get_tariff,
        "find_latest_tariff_version": latest_version,
        "insert_tariff_version": insert_version,
        "find_tariff_version_in_force": version_in_force,
        "find_bill_by_session_id": bill_by_session,
        "get_bill_by_id": bill_by_id,
        "find_session_payer": payer,
        "find_wallet_by_user_id": wallet_by_user,
        "insert_wallet": insert_wallet,
        "update_wallet_balance": update_balance,
        "insert_wallet_transaction": insert_transaction,
        "find_payment_by_transfer_code": payment_by_code,
        "find_payment_by_gateway_reference": payment_by_reference,
        "is_bill_paid": is_bill_paid,
    }.items():
        monkeypatch.setattr(repo, name, function)
    monkeypatch.setattr(bill_service, "set_change_context", set_context)
    monkeypatch.setattr(tariff_service, "set_change_context", set_context)
    return data


DB: Any = FakeDb()
OWNER_ID, LOCATION_ID, STATION_ID = uuid4(), uuid4(), uuid4()


def _add_tariff(
    store: Store, *, location_id: UUID | None, status: str = "ACTIVE"
) -> TariffModel:
    tariff = TariffModel(
        tariff_id=uuid4(),
        organization_id=OWNER_ID,
        location_id=location_id,
        name="Tariff",
        currency="VND",
        status=status,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    store.tariffs[tariff.tariff_id] = tariff
    return tariff


def _add_version(
    store: Store,
    tariff: TariffModel,
    *,
    version_no: int,
    price: int,
    starts: timedelta = timedelta(hours=-1),
) -> TariffVersionModel:
    version = TariffVersionModel(
        tariff_version_id=uuid4(),
        tariff_id=tariff.tariff_id,
        version_no=version_no,
        effective_from=utc_now() + starts,
        price_per_kwh=Decimal(price),
        time_periods=None,
        vat_rate_percent=Decimal(10),
        change_reason="test",
        created_by=uuid4(),
        created_at=utc_now(),
    )
    store.versions.append(version)
    return version


# --- Tariffs (PAY-09) --------------------------------------------------------


@pytest.mark.asyncio
async def test_publishing_adds_a_new_version_and_never_edits_an_old_one(
    store: Store,
) -> None:
    """Version numbers count up; the earlier version keeps its price."""
    tariff = _add_tariff(store, location_id=None)
    first = _add_version(store, tariff, version_no=1, price=4000)
    principal = build_principal()
    tariff.organization_id = principal.organization_id

    version_two = await tariff_service.publish_tariff_version(
        DB,
        tariff.tariff_id,
        TariffVersionPublishRequest(
            price_per_kwh=5000,
            vat_rate_percent=Decimal(10),
            change_reason="New season",
        ),
        principal=principal,
    )

    assert version_two.version_no == 2
    assert version_two.price_per_kwh == 5000
    assert first.price_per_kwh == Decimal(4000)
    assert [v.version_no for v in store.versions] == [1, 2]


@pytest.mark.asyncio
async def test_a_version_cannot_start_in_the_past_or_before_the_previous_one(
    store: Store,
) -> None:
    """effective_from is now or later and later than the previous start."""
    principal = build_principal()
    tariff = _add_tariff(store, location_id=None)
    tariff.organization_id = principal.organization_id
    _add_version(store, tariff, version_no=1, price=4000, starts=timedelta(days=2))

    def request(starts: timedelta) -> TariffVersionPublishRequest:
        return TariffVersionPublishRequest(
            price_per_kwh=5000,
            vat_rate_percent=Decimal(10),
            change_reason="x",
            effective_from=utc_now() + starts,
        )

    with pytest.raises(TariffInputError):
        await tariff_service.publish_tariff_version(
            DB, tariff.tariff_id, request(timedelta(hours=-2)), principal=principal
        )
    with pytest.raises(TariffInputError):  # before the scheduled version 1
        await tariff_service.publish_tariff_version(
            DB, tariff.tariff_id, request(timedelta(days=1)), principal=principal
        )
    published = await tariff_service.publish_tariff_version(
        DB, tariff.tariff_id, request(timedelta(days=3)), principal=principal
    )
    assert published.version_no == 2


@pytest.mark.asyncio
async def test_a_retired_tariff_takes_no_version_and_another_owner_cannot_publish(
    store: Store,
) -> None:
    """Retired is a conflict; another organization's tariff does not exist."""
    principal = build_principal()
    tariff = _add_tariff(store, location_id=None, status="INACTIVE")
    tariff.organization_id = principal.organization_id
    request = TariffVersionPublishRequest(
        price_per_kwh=1, vat_rate_percent=Decimal(0), change_reason="x"
    )

    with pytest.raises(TariffConflictError):
        await tariff_service.publish_tariff_version(
            DB, tariff.tariff_id, request, principal=principal
        )
    other = build_principal(organization_id=uuid4())
    with pytest.raises(Exception, match="not found"):
        await tariff_service.publish_tariff_version(
            DB, tariff.tariff_id, request, principal=other
        )


@pytest.fixture
def station_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `STATION_ID` stand at `LOCATION_ID`, owned by `OWNER_ID`."""

    async def reference(db: object, station_id: UUID) -> StationLocationReference:
        return StationLocationReference(
            station_id=station_id, location_id=LOCATION_ID, organization_id=OWNER_ID
        )

    monkeypatch.setattr(
        stations_service, "resolve_station_location_reference", reference
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("station_reference")
async def test_the_location_tariff_wins_over_the_owner_default(store: Store) -> None:
    """BL-08: the price at a location is its own ACTIVE tariff, else the default."""
    default = _add_tariff(store, location_id=None)
    _add_version(store, default, version_no=1, price=4000)
    own = _add_tariff(store, location_id=LOCATION_ID)
    own_version = _add_version(store, own, version_no=1, price=3000)

    quote = await tariff_service.resolve_tariff_for_station(DB, STATION_ID, utc_now())

    assert quote.tariff_version_id == own_version.tariff_version_id
    assert quote.price_per_kwh == Decimal(3000)

    own.status = "INACTIVE"
    quote = await tariff_service.resolve_tariff_for_station(DB, STATION_ID, utc_now())
    assert quote.price_per_kwh == Decimal(4000)


@pytest.mark.asyncio
@pytest.mark.usefixtures("station_reference")
async def test_a_tariff_with_no_version_in_force_yet_counts_as_absent(
    store: Store,
) -> None:
    """A scheduled-only tariff falls back to the default; none at all is a 409."""
    default = _add_tariff(store, location_id=None)
    _add_version(store, default, version_no=1, price=4000)
    own = _add_tariff(store, location_id=LOCATION_ID)
    _add_version(store, own, version_no=1, price=3000, starts=timedelta(days=1))

    quote = await tariff_service.resolve_tariff_for_station(DB, STATION_ID, utc_now())
    assert quote.price_per_kwh == Decimal(4000)

    default.status = "INACTIVE"
    with pytest.raises(NoTariffInForceError, match="NO_TARIFF"):
        await tariff_service.resolve_tariff_for_station(DB, STATION_ID, utc_now())


@pytest.mark.asyncio
@pytest.mark.usefixtures("station_reference")
async def test_the_version_in_force_changes_with_the_moment_asked(
    store: Store,
) -> None:
    """The newest version whose start has passed applies; the old one before."""
    default = _add_tariff(store, location_id=None)
    first = _add_version(
        store, default, version_no=1, price=4000, starts=timedelta(days=-5)
    )
    second = _add_version(
        store, default, version_no=2, price=5000, starts=timedelta(days=-1)
    )

    now_quote = await tariff_service.resolve_tariff_for_station(
        DB, STATION_ID, utc_now()
    )
    past_quote = await tariff_service.resolve_tariff_for_station(
        DB, STATION_ID, utc_now() - timedelta(days=3)
    )

    assert now_quote.tariff_version_id == second.tariff_version_id
    assert past_quote.tariff_version_id == first.tariff_version_id


# --- Bills and the ledger (BL-10, BL-14) -------------------------------------


def _quoted_bill(
    store: Store, *, price: int = 4500, vat: int = 10
) -> tuple[UUID, UUID]:
    """Add a QUOTED bill; return its session ID and the payer's user ID."""
    session_id, user_id = uuid4(), uuid4()
    store.payers[session_id] = (OWNER_ID, user_id)
    store.bills[session_id] = ChargingSessionBillModel(
        charging_session_bill_id=uuid4(),
        session_id=session_id,
        tariff_version_id=uuid4(),
        price_per_kwh=Decimal(price),
        vat_rate_percent=Decimal(vat),
        status="QUOTED",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    return session_id, user_id


@pytest.mark.asyncio
async def test_settling_bills_the_session_and_debits_the_wallet_once(
    store: Store,
) -> None:
    """The frozen price rounds to whole dong; a second call changes nothing."""
    session_id, user_id = _quoted_bill(store)

    async def settle() -> ChargingSessionBillModel | None:
        return await bill_service.settle_session_bill(
            DB,
            session_id=session_id,
            payer_user_id=user_id,
            meter_start_wh=Decimal(1000),
            meter_stop_wh=Decimal(193_520),
            last_measured_wh=Decimal(193_000),
        )

    bill = await settle()
    await settle()

    assert bill is not None
    assert bill.status == "BILLED"
    assert (bill.energy_wh, bill.energy_source) == (Decimal(192_520), "METER_STOP")
    assert (bill.amount_before_vat, bill.vat_amount) == (
        Decimal(866_340),
        Decimal(86_634),
    )
    assert bill.billed_at is not None
    assert len(store.ledger) == 1
    debit = store.ledger[0]
    assert debit.transaction_type == "SESSION_BILL"
    assert debit.amount == Decimal(-952_974)
    assert debit.balance_after == Decimal(-952_974)  # may go below zero (BL-14)
    assert debit.charging_session_bill_id == bill.charging_session_bill_id
    assert store.wallets[user_id].balance == Decimal(-952_974)


@pytest.mark.asyncio
async def test_a_second_charge_keeps_the_running_balance_in_the_ledger(
    store: Store,
) -> None:
    """Each line carries the balance right after it."""
    first_session, user_id = _quoted_bill(store)
    second_session = uuid4()
    store.payers[second_session] = (OWNER_ID, user_id)
    store.bills[second_session] = store.bills[first_session].__class__(
        charging_session_bill_id=uuid4(),
        session_id=second_session,
        tariff_version_id=uuid4(),
        price_per_kwh=Decimal(4500),
        vat_rate_percent=Decimal(10),
        status="QUOTED",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    for session_id in (first_session, second_session):
        await bill_service.settle_session_bill(
            DB,
            session_id=session_id,
            payer_user_id=user_id,
            meter_start_wh=Decimal(0),
            meter_stop_wh=Decimal(10_000),
            last_measured_wh=None,
        )

    assert [row.balance_after for row in store.ledger] == [
        Decimal(-49_500),
        Decimal(-99_000),
    ]


@pytest.mark.asyncio
async def test_a_bill_that_cannot_be_trusted_is_held_and_staff_release_pays_it(
    store: Store,
) -> None:
    """ON_HOLD keeps the figures, debits nothing; release bills and debits once."""
    session_id, user_id = _quoted_bill(store)
    bill = await bill_service.settle_session_bill(
        DB,
        session_id=session_id,
        payer_user_id=user_id,
        meter_start_wh=Decimal(0),
        meter_stop_wh=Decimal(60_000),
        last_measured_wh=Decimal(40_000),
    )
    assert bill is not None
    assert bill.status == "ON_HOLD"
    assert bill.amount_before_vat == Decimal(270_000)
    assert bill.billed_at is None
    assert store.ledger == []

    staff = build_internal_principal()
    released = await bill_service.release_bill(
        DB, bill.charging_session_bill_id, "Checked the meter", principal=staff
    )

    assert released.status is ChargingSessionBillStatus.BILLED
    assert released.total_amount == 297_000
    assert released.is_paid
    assert len(store.ledger) == 1
    with pytest.raises(BillStateError):  # BILLED bills are never edited
        await bill_service.release_bill(
            DB, bill.charging_session_bill_id, "again", principal=staff
        )


@pytest.mark.asyncio
async def test_voiding_clears_the_amounts_and_an_abandoned_scan_voids_its_quote(
    store: Store,
) -> None:
    """VOID bills carry no figures; only a QUOTED bill is voided by abandonment."""
    held_session, user_id = _quoted_bill(store)
    await bill_service.settle_session_bill(
        DB,
        session_id=held_session,
        payer_user_id=user_id,
        meter_start_wh=None,
        meter_stop_wh=None,
        last_measured_wh=None,
    )
    held = store.bills[held_session]
    assert held.status == "ON_HOLD"
    voided = await bill_service.void_held_bill(
        DB,
        held.charging_session_bill_id,
        "No readings",
        principal=build_internal_principal(),
    )
    assert voided.status is ChargingSessionBillStatus.VOID
    assert voided.total_amount is None

    abandoned_session, _ = _quoted_bill(store)
    assert await bill_service.void_session_bill(DB, abandoned_session)
    assert store.bills[abandoned_session].status == "VOID"
    assert not await bill_service.void_session_bill(DB, abandoned_session)
    assert store.ledger == []


@pytest.mark.asyncio
async def test_settling_a_session_without_a_bill_does_nothing(store: Store) -> None:
    """A session created without a scan has no quote, so nothing is billed."""
    result = await bill_service.settle_session_bill(
        DB,
        session_id=uuid4(),
        payer_user_id=uuid4(),
        meter_start_wh=Decimal(0),
        meter_stop_wh=Decimal(1000),
        last_measured_wh=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_adjustments_need_a_reason_and_a_nonzero_amount(store: Store) -> None:
    """The staff correction writes an ADJUSTMENT line with who and why."""
    user_id = uuid4()
    staff = build_internal_principal()

    line = await ledger_service.adjust_wallet(
        DB,
        user_id,
        WalletAdjustmentRequest(amount=-20_000, reason="Duplicate charge refunded"),
        principal=staff,
    )

    assert line.transaction_type.value == "ADJUSTMENT"
    assert (line.amount, line.balance_after) == (-20_000, -20_000)
    assert store.ledger[0].created_by == staff.user_id
    with pytest.raises(WalletInputError):
        await ledger_service.adjust_wallet(
            DB, user_id, WalletAdjustmentRequest(amount=0, reason="x"), principal=staff
        )


# --- Top-up and the bank notification (BL-15) --------------------------------


def _pending_top_up(store: Store, *, amount: int = 500_000) -> PaymentModel:
    payment = PaymentModel(
        payment_id=uuid4(),
        user_id=uuid4(),
        purpose="TOP_UP",
        amount=Decimal(amount),
        currency="VND",
        method="BANK_TRANSFER",
        transfer_code="G3NAP7K2Q9",
        status="PENDING",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    store.payments[payment.payment_id] = payment
    return payment


def _notification(
    *, reference: str = "FT1", amount: int = 500_000, content: str = "g3nap 7k2q9"
) -> BankNotification:
    return BankNotification(
        bank_transaction_id=reference, amount=amount, content=content
    )


@pytest.mark.asyncio
async def test_a_matching_transfer_credits_the_wallet_exactly_once(
    store: Store,
) -> None:
    """The same bank transaction repeated is a duplicate and credits nothing."""
    payment = _pending_top_up(store)

    first = await topup_service.process_bank_notification(DB, _notification())
    again = await topup_service.process_bank_notification(DB, _notification())

    assert first.result is BankNotificationResult.CREDITED
    assert first.payment_id == payment.payment_id
    assert not first.amount_differs
    assert again.result is BankNotificationResult.DUPLICATE
    assert payment.status == "SUCCEEDED"
    assert payment.gateway_reference == "FT1"
    assert payment.completed_at is not None
    assert [(row.transaction_type, row.amount) for row in store.ledger] == [
        ("TOP_UP", Decimal(500_000))
    ]
    assert store.wallets[payment.user_id].balance == Decimal(500_000)


@pytest.mark.asyncio
async def test_the_amount_received_is_credited_when_it_differs_from_the_ask(
    store: Store,
) -> None:
    """BL-15: the wallet gets what the bank received, flagged as different."""
    payment = _pending_top_up(store, amount=500_000)

    response = await topup_service.process_bank_notification(
        DB, _notification(amount=300_000)
    )

    assert response.result is BankNotificationResult.CREDITED
    assert response.amount_differs
    assert payment.amount == Decimal(300_000)
    assert store.wallets[payment.user_id].balance == Decimal(300_000)


@pytest.mark.asyncio
async def test_an_unknown_code_or_an_already_paid_top_up_credits_nothing(
    store: Store,
) -> None:
    """Unmatched and second transfers are only reported, never credited."""
    payment = _pending_top_up(store)

    unmatched = await topup_service.process_bank_notification(
        DB, _notification(reference="FT9", content="rent for october")
    )
    await topup_service.process_bank_notification(DB, _notification(reference="FT1"))
    second = await topup_service.process_bank_notification(
        DB, _notification(reference="FT2")
    )

    assert unmatched.result is BankNotificationResult.UNMATCHED
    assert second.result is BankNotificationResult.ALREADY_PAID
    assert len(store.ledger) == 1
    assert store.wallets[payment.user_id].balance == Decimal(500_000)


@pytest.mark.asyncio
async def test_an_expired_top_up_is_still_credited_when_the_money_arrives(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code's expiry only changes how the payment is shown, not the money."""
    monkeypatch.setattr(settings, "BILLING_TOPUP_EXPIRY_MINUTES", 15)
    payment = _pending_top_up(store)
    payment.created_at = utc_now() - timedelta(hours=2)
    payment.status = "FAILED"

    response = await topup_service.process_bank_notification(DB, _notification())

    assert response.result is BankNotificationResult.CREDITED
    assert payment.status == "SUCCEEDED"


def test_the_fake_provider_reads_its_webhook_shape_and_refuses_junk() -> None:
    """parse_notification validates the body; the secret is checked elsewhere."""
    from app.domains.billing.exceptions import PaymentInputError
    from app.domains.billing.providers import FakeBankNotificationProvider

    provider = FakeBankNotificationProvider()
    notification = provider.parse_notification(
        {"bank_transaction_id": " FT1 ", "amount": 1000, "content": "x"}
    )

    assert notification == BankNotification("FT1", 1000, "x", None, None)
    for body in (
        {"amount": 1000, "content": "x"},
        {"bank_transaction_id": "a", "amount": 0, "content": "x"},
        {"bank_transaction_id": "a", "amount": 10.5, "content": "x"},
        {"bank_transaction_id": "a", "amount": 10, "content": 5},
    ):
        with pytest.raises(PaymentInputError):
            provider.parse_notification(body)


def test_quotes_convert_to_whole_dong_with_vat() -> None:
    """The with-VAT price shown in the app is rounded to whole dong."""
    quote = TariffQuote(
        tariff_id=uuid4(),
        tariff_version_id=uuid4(),
        version_no=1,
        tariff_name="n",
        organization_id=uuid4(),
        currency="VND",
        price_per_kwh=Decimal(3858),
        normal_price_per_kwh=Decimal(4500),
        vat_rate_percent=Decimal(8),
        time_periods=None,
        at=utc_now(),
    )

    response = tariff_service.to_tariff_quote_response(quote)

    assert (response.price_per_kwh, response.price_per_kwh_with_vat) == (3858, 4167)
    assert response.normal_price_per_kwh == 4500
