"""Bank-notification providers behind one interface (PR-15, BL-15).

Top-ups are bank transfers; a bank-notification service tells us when money
arrives. Real services differ in the shape of their webhook, so the billing
code talks to this interface only:

* ``parse_notification`` turns a provider's webhook body into a
  `BankNotification`;
* ``announce_expected_transfer`` lets a provider that needs to know which
  transfer to watch for (some services want the code in advance) be told when
  a QR code is issued;
* ``build_simulated_notification`` lets a development provider fake an
  incoming transfer.

Only the logging fake exists (selected by ``BILLING_BANK_PROVIDER``); no real
bank integration is built (PR-15). Never run the fake in production: anyone
who knows the webhook secret can credit a wallet.
"""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from app.domains.billing.exceptions import PaymentInputError
from app.domains.billing.types import BankNotification
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

_MAX_BANK_TRANSACTION_ID_LENGTH = 100


class BankNotificationProvider(Protocol):
    """What billing needs from a bank-notification service.

    Attributes:
        name: The provider's name, as set in ``BILLING_BANK_PROVIDER``.
        supports_simulation: Whether `build_simulated_notification` works.
    """

    name: str
    supports_simulation: bool

    def parse_notification(self, payload: Mapping[str, object]) -> BankNotification:
        """Read a webhook body into a bank notification."""
        ...

    def build_simulated_notification(
        self, *, bank_transaction_id: str, amount: int, content: str
    ) -> BankNotification:
        """Make an incoming transfer up, for development."""
        ...

    async def announce_expected_transfer(
        self, *, transfer_code: str, amount: int, expires_at: datetime
    ) -> None:
        """Tell the provider a QR code was issued for this transfer."""
        ...


class FakeBankNotificationProvider:
    """A provider that only logs (PR-15).

    Its webhook shape is ours: ``{bank_transaction_id, amount, content,
    account_number?}``. It can simulate a transfer, and it writes a log line
    when a QR code is announced.

    Attributes:
        name: ``fake``.
        supports_simulation: ``True``.
    """

    name = "fake"
    supports_simulation = True

    def parse_notification(self, payload: Mapping[str, object]) -> BankNotification:
        """Read a webhook body of the fake provider's shape.

        Args:
            payload: The decoded JSON body.

        Returns:
            The notification.

        Raises:
            PaymentInputError: A field is missing or of the wrong kind.
        """
        bank_transaction_id = payload.get("bank_transaction_id")
        amount = payload.get("amount")
        content = payload.get("content")
        account_number = payload.get("account_number")
        if (
            not isinstance(bank_transaction_id, str)
            or not bank_transaction_id.strip()
            or len(bank_transaction_id) > _MAX_BANK_TRANSACTION_ID_LENGTH
        ):
            raise PaymentInputError("bank_transaction_id is missing or too long")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise PaymentInputError("amount must be a whole number above zero")
        if not isinstance(content, str):
            raise PaymentInputError("content must be a text")
        return BankNotification(
            bank_transaction_id=bank_transaction_id.strip(),
            amount=amount,
            content=content,
            account_number=account_number if isinstance(account_number, str) else None,
        )

    def build_simulated_notification(
        self, *, bank_transaction_id: str, amount: int, content: str
    ) -> BankNotification:
        """Make an incoming transfer up.

        Args:
            bank_transaction_id: The bank reference to use.
            amount: Amount in whole dong.
            content: The transfer content.

        Returns:
            The notification, as `parse_notification` would read it.
        """
        return self.parse_notification(
            {
                "bank_transaction_id": bank_transaction_id,
                "amount": amount,
                "content": content,
                "account_number": settings.BILLING_VIETQR_ACCOUNT_NUMBER,
            }
        )

    async def announce_expected_transfer(
        self, *, transfer_code: str, amount: int, expires_at: datetime
    ) -> None:
        """Log that a QR code was issued (the fake has nothing to register).

        Args:
            transfer_code: The unique content of the expected transfer.
            amount: The amount the payer asked to top up.
            expires_at: When the code stops waiting.
        """
        logger.info(
            "Expecting a bank transfer",
            extra={
                "transfer_code": transfer_code,
                "amount_vnd": amount,
                "expires_at": expires_at.isoformat(),
            },
        )


def get_bank_notification_provider() -> BankNotificationProvider:
    """Return the provider selected by ``BILLING_BANK_PROVIDER``.

    Returns:
        The provider; today always the logging fake.
    """
    return FakeBankNotificationProvider()
