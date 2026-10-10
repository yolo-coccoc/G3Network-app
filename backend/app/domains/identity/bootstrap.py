"""Seed command for the first administrator: ``make identity-bootstrap``.

No endpoint can create the very first administrator (every endpoint needs a
logged-in HEAD_ADMIN / CO_ADMIN), so this process creates, from the
`IDENTITY_BOOTSTRAP_*` settings:

- the internal organization (``is_internal``) with its default settings;
- the first HEAD_ADMIN (also the organization's ORG_ADMIN) with a password;
- optionally the sealed emergency HEAD_ADMIN of ACC-20, when
  `IDENTITY_EMERGENCY_ADMIN_PHONE` and
  `IDENTITY_BOOTSTRAP_EMERGENCY_ADMIN_PASSWORD` are set.

It is idempotent: anything that already exists (matched by organization name
and phone number) is left as it is, including its password. Run it once per
database after `make db-reset`.
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.organization_service as organization_service
import app.domains.identity.repository as identity_repository
from app.api.startup import register_all_hooks
from app.domains.identity.models import OrganizationModel, UserModel
from app.domains.identity.security import hash_password, normalize_phone_number
from app.domains.identity.types import (
    MembershipStatus,
    OrganizationLegalForm,
    OrganizationStatus,
    UserRole,
    UserStatus,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.logging import configure_logging
from app.libs.db.session import async_session_factory, close_db

logger = logging.getLogger(__name__)


class BootstrapConfigError(Exception):
    """A required `IDENTITY_BOOTSTRAP_*` setting is missing or invalid."""


@dataclass(frozen=True)
class BootstrapResult:
    """What a bootstrap run created or found.

    Attributes:
        organization_id: The internal organization.
        head_admin_user_id: The first HEAD_ADMIN.
        emergency_admin_user_id: The emergency HEAD_ADMIN; `None` when not
            configured.
        created: Names of the things this run created (empty on a re-run).
    """

    organization_id: UUID
    head_admin_user_id: UUID
    emergency_admin_user_id: UUID | None
    created: list[str]


async def _ensure_internal_organization(
    db_session: AsyncSession, created: list[str]
) -> OrganizationModel:
    """Find or create the internal organization named by the settings.

    Args:
        db_session: Session owned by the entry boundary.
        created: Names of created things; extended when a row is created.

    Returns:
        The internal organization.
    """
    organization_record = await identity_repository.find_internal_organization_by_name(
        db_session, settings.IDENTITY_BOOTSTRAP_ORGANIZATION_NAME
    )
    if organization_record is not None:
        return organization_record
    tax_code = settings.IDENTITY_BOOTSTRAP_ORGANIZATION_TAX_CODE
    organization_record = await identity_repository.insert_organization(
        db_session,
        {
            "is_internal": True,
            "legal_form": OrganizationLegalForm.COMPANY.value,
            "display_name": settings.IDENTITY_BOOTSTRAP_ORGANIZATION_NAME,
            "legal_name": settings.IDENTITY_BOOTSTRAP_ORGANIZATION_LEGAL_NAME,
            "tax_code": None
            if not tax_code
            else organization_service.normalize_tax_code(
                OrganizationLegalForm.COMPANY, tax_code
            ),
            "status": OrganizationStatus.ACTIVE.value,
        },
    )
    await identity_repository.insert_organization_settings(
        db_session, organization_record.organization_id
    )
    created.append("organization")
    return organization_record


async def _ensure_administrator(
    db_session: AsyncSession,
    *,
    organization_record: OrganizationModel,
    phone_number: str,
    full_name: str,
    password: str,
    roles: list[UserRole],
    created: list[str],
) -> UserModel:
    """Find or create an active administrator and make sure the roles are held.

    An existing user keeps their password; only a missing membership or role
    is added.

    Args:
        db_session: Session owned by the entry boundary.
        organization_record: The internal organization.
        phone_number: The administrator's phone number, E.164.
        full_name: Name used when the account is created.
        password: Password used when the account is created.
        roles: Roles the membership must hold.
        created: Names of created things; extended for each created row.

    Returns:
        The administrator's user.
    """
    user_record = await identity_repository.find_live_user_by_phone(
        db_session, phone_number
    )
    if user_record is None:
        user_record = await identity_repository.insert_user(
            db_session,
            {
                "phone_number": phone_number,
                "full_name": full_name,
                "status": UserStatus.ACTIVE.value,
            },
        )
        secret_hash = await asyncio.to_thread(hash_password, password)
        await identity_repository.insert_credential(
            db_session, user_id=user_record.user_id, secret_hash=secret_hash
        )
        created.append(f"user {phone_number}")
    membership_record = await identity_repository.find_live_membership(
        db_session,
        organization_id=organization_record.organization_id,
        user_id=user_record.user_id,
    )
    if membership_record is None:
        membership_record = await identity_repository.insert_membership(
            db_session,
            {
                "organization_id": organization_record.organization_id,
                "user_id": user_record.user_id,
                "status": MembershipStatus.ACTIVE.value,
                "joined_at": utc_now(),
            },
        )
        created.append(f"membership {phone_number}")
    for role in roles:
        if (
            await identity_repository.find_active_role_assignment(
                db_session,
                membership_id=membership_record.membership_id,
                role=role.value,
            )
            is None
        ):
            await identity_repository.insert_role_assignment(
                db_session,
                membership_record=membership_record,
                role=role.value,
                granted_by=None,
            )
            created.append(f"role {role.value} for {phone_number}")
    return user_record


async def bootstrap_identity(db_session: AsyncSession) -> BootstrapResult:
    """Create the internal organization and the administrators from the settings.

    Args:
        db_session: Session owned by the entry boundary (the caller commits).

    Returns:
        The IDs of the organization and administrators and what was created.

    Raises:
        BootstrapConfigError: The first administrator's phone number or
            password is not configured, or a phone number is invalid.
    """
    if not settings.IDENTITY_BOOTSTRAP_ADMIN_PHONE or not (
        settings.IDENTITY_BOOTSTRAP_ADMIN_PASSWORD
    ):
        raise BootstrapConfigError(
            "Set IDENTITY_BOOTSTRAP_ADMIN_PHONE and IDENTITY_BOOTSTRAP_ADMIN_PASSWORD"
        )
    try:
        admin_phone = normalize_phone_number(settings.IDENTITY_BOOTSTRAP_ADMIN_PHONE)
        emergency_phone = (
            normalize_phone_number(settings.IDENTITY_EMERGENCY_ADMIN_PHONE)
            if settings.IDENTITY_EMERGENCY_ADMIN_PHONE
            else None
        )
    except ValueError as error:
        raise BootstrapConfigError(f"Invalid phone number: {error}") from error
    created: list[str] = []
    organization_record = await _ensure_internal_organization(db_session, created)
    head_admin = await _ensure_administrator(
        db_session,
        organization_record=organization_record,
        phone_number=admin_phone,
        full_name=settings.IDENTITY_BOOTSTRAP_ADMIN_NAME,
        password=settings.IDENTITY_BOOTSTRAP_ADMIN_PASSWORD,
        roles=[UserRole.HEAD_ADMIN, UserRole.ORG_ADMIN],
        created=created,
    )
    emergency_admin_id: UUID | None = None
    if emergency_phone and settings.IDENTITY_BOOTSTRAP_EMERGENCY_ADMIN_PASSWORD:
        emergency_admin = await _ensure_administrator(
            db_session,
            organization_record=organization_record,
            phone_number=emergency_phone,
            full_name="Emergency Administrator",
            password=settings.IDENTITY_BOOTSTRAP_EMERGENCY_ADMIN_PASSWORD,
            roles=[UserRole.HEAD_ADMIN],
            created=created,
        )
        emergency_admin_id = emergency_admin.user_id
    return BootstrapResult(
        organization_id=organization_record.organization_id,
        head_admin_user_id=head_admin.user_id,
        emergency_admin_user_id=emergency_admin_id,
        created=created,
    )


async def run() -> BootstrapResult:
    """Run the bootstrap in one transaction and close the engine.

    Returns:
        The bootstrap result.

    Raises:
        BootstrapConfigError: See `bootstrap_identity`.

    Side Effects:
        Commits the created rows; closes the shared database engine.
    """
    try:
        async with async_session_factory.begin() as db_session:
            return await bootstrap_identity(db_session)
    finally:
        await close_db()


def main() -> None:
    """Entry point of ``python -m app.domains.identity.bootstrap``.

    Raises:
        SystemExit: With code 1 when the configuration is missing or invalid.
    """
    configure_logging()
    # Every cross-domain hook, the same in every process (CV-21).
    register_all_hooks()
    try:
        result = asyncio.run(run())
    except BootstrapConfigError as error:
        logger.error("identity_bootstrap_failed", extra={"reason": str(error)})
        sys.exit(1)
    logger.info(
        "identity_bootstrap_done",
        extra={
            "organization_id": str(result.organization_id),
            "head_admin_user_id": str(result.head_admin_user_id),
            "created": result.created,
        },
    )


if __name__ == "__main__":
    main()
