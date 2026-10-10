"""Business exceptions of the billing domain."""

from app.libs.common.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)


class WalletBlockedError(PermissionDeniedError):
    """The person's wallet is ``BLOCKED``, so no charge may start (BL-13)."""


class InsufficientBalanceError(ConflictError):
    """The wallet is below the minimum balance a charge needs (BL-14).

    The message starts with the code ``INSUFFICIENT_BALANCE`` and names the
    amount to top up, so the app can show it.
    """


class TariffNotFoundError(NotFoundError):
    """No tariff (or tariff version) with this ID is in the caller's reach."""


class TariffStationNotFoundError(NotFoundError):
    """The charger a price was asked for does not exist."""


class NoTariffInForceError(ConflictError):
    """No tariff prices this charger now, so a charge cannot be quoted (BL-08).

    The message starts with the code ``NO_TARIFF``.
    """


class TariffConflictError(ConflictError):
    """A second ACTIVE tariff for the same owner and location, or a state clash."""


class TariffInputError(InvalidInputError):
    """A tariff or version breaks a business rule (location, periods, dates)."""


class BillNotFoundError(NotFoundError):
    """No session bill with this ID or session is in the caller's reach."""


class BillStateError(ConflictError):
    """The bill is not in a status that allows this action (BL-10)."""


class WalletNotFoundError(NotFoundError):
    """The person has no wallet yet."""


class WalletConflictError(ConflictError):
    """The wallet is already in the status asked for."""


class WalletInputError(InvalidInputError):
    """A wallet action breaks a business rule (amount, reason)."""


class PaymentNotFoundError(NotFoundError):
    """No payment with this ID is in the caller's reach."""


class PaymentInputError(InvalidInputError):
    """A top-up amount or a bank notification is unusable."""


class WebhookAuthenticationError(UnauthenticatedError):
    """The bank-notification call did not carry the shared secret (BL-15)."""


class BankProviderUnavailableError(ConflictError):
    """The configured bank-notification provider cannot do this (simulation)."""
