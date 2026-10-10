"""Smoke test that the billing models describe the designed tables (BL-08..BL-15)."""

import app.domains.billing.models  # noqa: F401
from app.libs.db.base import Base


def test_billing_tables_carry_their_designed_constraints() -> None:
    """Each billing table exists with the unique keys and checks the design names."""
    tables = Base.metadata.tables

    assert {
        "tariffs",
        "tariff_versions",
        "charging_session_bills",
        "payments",
        "wallets",
        "wallet_transactions",
    } <= set(tables)
    assert {"invoices", "subscriptions", "subscription_plans"}.isdisjoint(tables)
    check_names = {
        constraint.name
        for table in tables.values()
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {
        "ck_tariff_versions_price_non_negative",
        "ck_charging_session_bills_billed_amounts",
        "ck_payments_refund_links_payment",
        "ck_wallet_transactions_amount_sign",
    } <= check_names
    index_names = {index.name for table in tables.values() for index in table.indexes}
    assert {
        "uq_tariffs_active_owner_location",
        "uq_wallets_user_id",
        "uq_wallet_transactions_session_bill",
    } <= index_names
