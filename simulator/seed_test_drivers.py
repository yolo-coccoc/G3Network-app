"""Create the test drivers of a charging test day: ``make charging-test-drivers-seed``.

Decision D7 of ``docs/planners/backend-charging-test-day.md``. For each driver
``1..--count`` this script:

1. creates, **in-process** like ``make identity-bootstrap`` does for the first
   administrator (no endpoint can create an account without the invitation's
   SMS code): an ACTIVE user with a hashed password, an ACTIVE membership in
   the internal organization and the ``DRIVER`` role only. All accounts are
   created in one transaction;
2. credits the wallet **through the API**, logged in as the bootstrap
   HEAD_ADMIN: ``POST /api/v1/wallets/{user_id}/adjustments`` with the
   difference up to ``--balance-vnd``, so the ledger holds a proper
   ``ADJUSTMENT`` line (the endpoint creates the wallet when there is none).

It is idempotent. An existing phone number is left as it is (name, password,
roles untouched); it must already hold ``DRIVER`` in the internal
organization, otherwise the script stops, since that number cannot charge. A
wallet already at or above the target is not debited.

Limits: the internal organization must exist (run ``make identity-bootstrap``
first), the API must be running for the wallet step, and the tool never
deletes anything. Run it from ``backend/`` with its venv
(``uv run python ../simulator/seed_test_drivers.py``).
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from uuid import UUID

from api_client import ApiClient, ApiError, login_as_bootstrap_admin, resolve_api_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from test_day_common import (
    TEST_DRIVER_DEFAULT_PASSWORD,
    TEST_DRIVER_PASSWORD_ENV,
    TEST_DRIVER_PHONE_PREFIX,
    build_test_driver_phone,
    print_table,
    resolve_test_driver_password,
)

import app.domains.identity.repository as identity_repository
from app.api.startup import register_all_hooks
from app.domains.identity.security import hash_password, normalize_phone_number
from app.domains.identity.types import MembershipStatus, UserRole, UserStatus
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.logging import configure_logging
from app.libs.db.session import async_session_factory, close_db

DEFAULT_COUNT = 2
DEFAULT_BALANCE_VND = 10_000_000
DEFAULT_NAME_PREFIX = "Test Driver"
CREDIT_REASON = "Test-day credit"


class SeedError(Exception):
    """The drivers cannot be seeded; the message says why and what to do."""


@dataclass(frozen=True)
class SeededDriver:
    """One test driver after the identity step.

    Attributes:
        driver_number: The driver's number (1-based).
        phone_number: The login phone number, E.164.
        user_id: The user's ID.
        is_created: Whether this run created the account.
    """

    driver_number: int
    phone_number: str
    user_id: UUID
    is_created: bool


async def ensure_driver(
    db_session: AsyncSession,
    *,
    organization_id: UUID,
    driver_number: int,
    phone_number: str,
    full_name: str,
    password: str,
) -> SeededDriver:
    """Create one test driver, or check the existing account can charge.

    Args:
        db_session: Session of the seed's transaction.
        organization_id: The internal organization.
        driver_number: The driver's number.
        phone_number: The phone number, E.164.
        full_name: Name of a new account.
        password: Password of a new account (hashed with scrypt).

    Returns:
        The driver's identity and whether it was created now.

    Raises:
        SeedError: The phone belongs to a person who holds no ``DRIVER`` role
            in the internal organization.
    """
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, phone_number
    )
    if user_record is not None:
        membership_record = await identity_repository.find_live_membership(
            db_session, organization_id=organization_id, user_id=user_record.user_id
        )
        if membership_record is None or (
            await identity_repository.find_active_role_assignment(
                db_session,
                membership_id=membership_record.membership_id,
                role=UserRole.DRIVER.value,
            )
            is None
        ):
            raise SeedError(
                f"{phone_number} already exists without the DRIVER role in the "
                "internal organization; pick another --phone-prefix"
            )
        return SeededDriver(driver_number, phone_number, user_record.user_id, False)

    user_record = await identity_repository.insert_user(
        db_session,
        {
            "phone_number": phone_number,
            "full_name": full_name,
            "status": UserStatus.ACTIVE.value,
        },
    )
    # scrypt is CPU-heavy: keep it off the event loop, as the bootstrap does.
    secret_hash = await asyncio.to_thread(hash_password, password)
    await identity_repository.insert_credential(
        db_session, user_id=user_record.user_id, secret_hash=secret_hash
    )
    membership_record = await identity_repository.insert_membership(
        db_session,
        {
            "organization_id": organization_id,
            "user_id": user_record.user_id,
            "status": MembershipStatus.ACTIVE.value,
            "joined_at": utc_now(),
        },
    )
    await identity_repository.insert_role_assignment(
        db_session,
        membership_record=membership_record,
        role=UserRole.DRIVER.value,
        granted_by=None,
    )
    return SeededDriver(driver_number, phone_number, user_record.user_id, True)


async def seed_identities(
    phone_numbers: list[str], name_prefix: str, password: str
) -> list[SeededDriver]:
    """Create or check every driver account in one transaction.

    Args:
        phone_numbers: Phone numbers in driver order (driver 1 first).
        name_prefix: Name of a new account is ``<prefix> <number>``.
        password: Password of new accounts.

    Returns:
        The drivers in order.

    Raises:
        SeedError: The internal organization is missing, or a phone belongs
            to a non-driver (nothing is committed then).

    Side Effects:
        Commits the new accounts; closes the shared database engine.
    """
    try:
        async with async_session_factory.begin() as db_session:
            organization_record = (
                await identity_repository.find_internal_organization_by_name(
                    db_session, settings.IDENTITY_BOOTSTRAP_ORGANIZATION_NAME
                )
            )
            if organization_record is None:
                raise SeedError(
                    "The internal organization "
                    f"{settings.IDENTITY_BOOTSTRAP_ORGANIZATION_NAME!r} does not "
                    "exist; run `make identity-bootstrap` first"
                )
            drivers = []
            for index, phone_number in enumerate(phone_numbers, start=1):
                drivers.append(
                    await ensure_driver(
                        db_session,
                        organization_id=organization_record.organization_id,
                        driver_number=index,
                        phone_number=phone_number,
                        full_name=f"{name_prefix} {index}",
                        password=password,
                    )
                )
            return drivers
    finally:
        await close_db()


def read_balance(client: ApiClient, user_id: UUID) -> int:
    """Read a person's wallet balance as billing staff.

    Args:
        client: Client logged in as the bootstrap HEAD_ADMIN.
        user_id: The person.

    Returns:
        The balance in dong; ``0`` when the person has no wallet yet (404).

    Raises:
        ApiError: Any other HTTP error.
    """
    try:
        wallet = client.get(f"/api/v1/wallets/{user_id}")
    except ApiError as error:
        if error.status_code == 404:
            return 0
        raise
    return int(wallet["balance"])


def credit_wallet(client: ApiClient, user_id: UUID, target_vnd: int) -> int:
    """Top a wallet up to the target with one ``ADJUSTMENT`` line.

    Args:
        client: Client logged in as the bootstrap HEAD_ADMIN.
        user_id: The person.
        target_vnd: Balance to reach; a higher balance is left as it is.

    Returns:
        The balance after the run.

    Raises:
        ApiError: The backend refused the read or the adjustment.
    """
    balance = read_balance(client, user_id)
    if balance >= target_vnd:
        return balance
    transaction = client.post(
        f"/api/v1/wallets/{user_id}/adjustments",
        {"amount": target_vnd - balance, "reason": CREDIT_REASON},
    )
    return int(transaction["balance_after"])


def positive_int(value: str) -> int:
    """Parse a positive integer CLI value.

    Args:
        value: Numeric string received from the CLI.

    Returns:
        Positive integer.

    Raises:
        argparse.ArgumentTypeError: If value is not a positive integer.
    """
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args() -> argparse.Namespace:
    """Read the CLI parameters.

    Returns:
        Namespace with the count, phone prefix, password, name prefix, target
        balance and API URL.
    """
    parser = argparse.ArgumentParser(
        description="Create N test drivers (DRIVER role, internal organization) "
        "and credit their wallets through the API."
    )
    parser.add_argument("--count", type=positive_int, default=DEFAULT_COUNT)
    parser.add_argument(
        "--phone-prefix",
        default=TEST_DRIVER_PHONE_PREFIX,
        help="Driver N's phone is this prefix plus N on two digits "
        f"(default {TEST_DRIVER_PHONE_PREFIX} -> {TEST_DRIVER_PHONE_PREFIX}01).",
    )
    parser.add_argument(
        "--password",
        default=None,
        help=f"Password of new accounts; default: ${TEST_DRIVER_PASSWORD_ENV}, "
        "else the documented test default.",
    )
    parser.add_argument("--name-prefix", default=DEFAULT_NAME_PREFIX)
    parser.add_argument(
        "--balance-vnd",
        type=positive_int,
        default=DEFAULT_BALANCE_VND,
        help="Wallet balance each driver is topped up to (dong).",
    )
    parser.add_argument(
        "--api-url", default=None, help="Backend URL; default $G3_API_URL or localhost."
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    """Seed the identities, credit the wallets and print the table.

    Args:
        args: Parsed CLI arguments.

    Raises:
        SeedError: Invalid phone numbers or password, or see `seed_identities`.
        ApiError: The backend refused the admin login or a wallet call.
        RuntimeError: The backend is unreachable.
    """
    password = resolve_test_driver_password(args.password)
    if password == TEST_DRIVER_DEFAULT_PASSWORD:
        # The default is public (it is in the source): fine for a scratch or
        # test database, not for accounts on a server others can reach.
        print(
            "WARNING: the drivers get the documented default password; set "
            f"{TEST_DRIVER_PASSWORD_ENV} or --password for a shared server"
        )
    if len(password) < settings.IDENTITY_PASSWORD_MIN_LENGTH or not password.strip():
        raise SeedError(
            "The password needs at least "
            f"{settings.IDENTITY_PASSWORD_MIN_LENGTH} characters"
        )
    try:
        phone_numbers = [
            normalize_phone_number(build_test_driver_phone(number, args.phone_prefix))
            for number in range(1, args.count + 1)
        ]
    except ValueError as error:
        raise SeedError(f"Invalid phone number from --phone-prefix: {error}") from error

    drivers = asyncio.run(seed_identities(phone_numbers, args.name_prefix, password))
    client = login_as_bootstrap_admin(resolve_api_url(args.api_url))
    rows = []
    for driver in drivers:
        balance = credit_wallet(client, driver.user_id, args.balance_vnd)
        rows.append(
            [
                driver.driver_number,
                driver.phone_number,
                driver.user_id,
                f"{balance:,}",
                "created" if driver.is_created else "existing",
            ]
        )
    print_table(["driver", "phone", "user_id", "balance_vnd", "account"], rows)


def main() -> None:
    """Entry point; exits 1 with a one-line message on any failure."""
    configure_logging()
    # Every cross-domain hook, the same in every process (CV-21).
    register_all_hooks()
    args = parse_args()
    try:
        run(args)
    except ApiError as error:
        sys.exit(
            f"error: {error.method} {error.path} -> HTTP {error.status_code}: "
            f"{error.detail}"
        )
    except (SeedError, RuntimeError) as error:
        sys.exit(f"error: {error}")
    except (SQLAlchemyError, OSError) as error:
        # The database is down or refused the rows: nothing was committed.
        sys.exit(f"error: database: {error}")


if __name__ == "__main__":
    main()
