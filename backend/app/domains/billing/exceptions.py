"""Business exceptions of the billing domain."""

from app.libs.common.errors import ConflictError, PermissionDeniedError


class WalletBlockedError(PermissionDeniedError):
    """The person's wallet is ``BLOCKED``, so no charge may start (BL-13)."""


class InsufficientBalanceError(ConflictError):
    """The wallet is below the minimum balance a charge needs (BL-14).

    The message starts with the code ``INSUFFICIENT_BALANCE`` and names the
    amount to top up, so the app can show it.
    """
