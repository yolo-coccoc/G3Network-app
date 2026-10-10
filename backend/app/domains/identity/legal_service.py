"""Legal documents and recorded acceptance (ACC-17, ID-38, ID-39).

Our HEAD_ADMIN / CO_ADMIN publish final legal texts (a row is never edited);
people and companies accept the current version of the texts that apply to
them, and every acceptance is an append-only proof with IP address and device.

Limitations: accepting is not enforced as a condition of use (no endpoint
refuses a call until the pending texts are accepted); `GET /legal-documents/pending`
tells the client what is missing. Withdrawal means leaving the service and is
recorded on the account, not here (ID-38).
"""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.repository as identity_repository
from app.domains.identity.exceptions import (
    AccessDeniedError,
    LegalDocumentConflictError,
    LegalDocumentNotFoundError,
)
from app.domains.identity.models import LegalDocumentModel
from app.domains.identity.schemas import (
    ConsentCreateRequest,
    ConsentResponse,
    LegalDocumentCreateRequest,
    LegalDocumentResponse,
    LegalDocumentSummaryResponse,
)
from app.domains.identity.types import (
    ClientContext,
    LegalDocumentPurpose,
    OrganizationLegalForm,
    Principal,
    UserRole,
)
from app.libs.common.clock import utc_now


async def publish_legal_document(
    db_session: AsyncSession,
    principal: Principal,
    create_request: LegalDocumentCreateRequest,
) -> LegalDocumentResponse:
    """Publish a final legal text; it takes effect now (ID-39).

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller (internal HEAD_ADMIN / CO_ADMIN).
        create_request: Purpose, version, title and full text.

    Returns:
        The published document.

    Raises:
        AccessDeniedError: The caller is not an internal administrator.
        LegalDocumentConflictError: The purpose and version already exist.
    """
    if not (
        principal.is_internal
        and principal.has_any_role(UserRole.HEAD_ADMIN, UserRole.CO_ADMIN)
    ):
        raise AccessDeniedError("Only our administrators publish legal documents")
    if (
        await identity_repository.find_legal_document_by_version(
            db_session,
            purpose=create_request.purpose.value,
            version=create_request.version,
        )
        is not None
    ):
        raise LegalDocumentConflictError("This version was already published")
    try:
        document_record = await identity_repository.insert_legal_document(
            db_session,
            {
                "purpose": create_request.purpose.value,
                "version": create_request.version,
                "title": create_request.title,
                "content": create_request.content,
                "created_by": principal.user_id,
            },
        )
    except IntegrityError as error:
        raise LegalDocumentConflictError(
            "This version was already published"
        ) from error
    return LegalDocumentResponse.model_validate(document_record)


async def list_legal_documents(
    db_session: AsyncSession, purpose: LegalDocumentPurpose | None
) -> list[LegalDocumentSummaryResponse]:
    """List published versions without their text, newest first.

    Args:
        db_session: Current database session.
        purpose: Only this purpose.

    Returns:
        The matching documents.
    """
    document_records = await identity_repository.list_legal_documents(
        db_session, None if purpose is None else purpose.value
    )
    return [
        LegalDocumentSummaryResponse.model_validate(record)
        for record in document_records
    ]


async def list_current_legal_documents(
    db_session: AsyncSession,
) -> list[LegalDocumentResponse]:
    """Return the newest version, with its text, of every purpose.

    Public: a person reads the texts before an account exists (sign-up).

    Args:
        db_session: Current database session.

    Returns:
        One document per purpose that has been published.
    """
    current_documents = []
    for purpose in LegalDocumentPurpose:
        document_record = await identity_repository.find_current_legal_document(
            db_session, purpose.value
        )
        if document_record is not None:
            current_documents.append(
                LegalDocumentResponse.model_validate(document_record)
            )
    return current_documents


async def get_legal_document(
    db_session: AsyncSession, legal_document_id: UUID
) -> LegalDocumentResponse:
    """Read one version with its text.

    Args:
        db_session: Current database session.
        legal_document_id: The document.

    Returns:
        The document.

    Raises:
        LegalDocumentNotFoundError: It does not exist.
    """
    document_record = await identity_repository.get_legal_document(
        db_session, legal_document_id
    )
    if document_record is None:
        raise LegalDocumentNotFoundError("The legal document does not exist")
    return LegalDocumentResponse.model_validate(document_record)


async def _applicable_purposes(
    db_session: AsyncSession, principal: Principal
) -> list[LegalDocumentPurpose]:
    """Return the texts that apply to the caller in their organization (ID-38).

    Everyone accepts the terms and the privacy policy; a driver acknowledges
    the privacy notice; a company's ORG_ADMIN accepts the data processing
    agreement; an individual customer consents to location tracking.
    """
    organization_record = await identity_repository.get_organization(
        db_session, principal.organization_id
    )
    purposes = [
        LegalDocumentPurpose.TERMS_OF_SERVICE,
        LegalDocumentPurpose.PRIVACY_POLICY,
    ]
    if principal.has_any_role(UserRole.DRIVER):
        purposes.append(LegalDocumentPurpose.PRIVACY_NOTICE)
    if organization_record is not None and not principal.is_internal:
        if (
            organization_record.legal_form == OrganizationLegalForm.COMPANY.value
            and principal.has_any_role(UserRole.ORG_ADMIN)
        ):
            purposes.append(LegalDocumentPurpose.DATA_PROCESSING_AGREEMENT)
        if organization_record.legal_form == OrganizationLegalForm.INDIVIDUAL.value:
            purposes.append(LegalDocumentPurpose.LOCATION_TRACKING)
    return purposes


async def list_pending_legal_documents(
    db_session: AsyncSession, principal: Principal
) -> list[LegalDocumentResponse]:
    """List the current texts the caller still has to accept.

    Args:
        db_session: Current database session.
        principal: The caller.

    Returns:
        Current versions that apply to the caller and are not yet accepted
        (by the person, or by their organization for the data processing
        agreement).
    """
    pending: list[LegalDocumentResponse] = []
    for purpose in await _applicable_purposes(db_session, principal):
        document_record = await identity_repository.find_current_legal_document(
            db_session, purpose.value
        )
        if document_record is None:
            continue
        on_behalf = purpose is LegalDocumentPurpose.DATA_PROCESSING_AGREEMENT
        consent_record = await identity_repository.find_consent(
            db_session,
            user_id=principal.user_id,
            legal_document_id=document_record.legal_document_id,
            organization_id=principal.organization_id if on_behalf else None,
        )
        if consent_record is None:
            pending.append(LegalDocumentResponse.model_validate(document_record))
    return pending


async def accept_legal_document(
    db_session: AsyncSession,
    principal: Principal,
    consent_request: ConsentCreateRequest,
    device_label: str | None,
    client_context: ClientContext | None,
) -> ConsentResponse:
    """Record the caller's acceptance of the current version of a text (ACC-17).

    Idempotent: accepting a version again returns the first record.

    Args:
        db_session: Session owned by the entry boundary.
        principal: The caller.
        consent_request: The document, and whether it is accepted for the
            organization.
        device_label: Readable device name to keep as proof.
        client_context: IP address and user agent of the request.

    Returns:
        The consent record.

    Raises:
        LegalDocumentNotFoundError: The document does not exist.
        LegalDocumentConflictError: The version is no longer the current one,
            or it is accepted on the wrong level.
        AccessDeniedError: A data processing agreement is accepted by someone
            other than the organization's ORG_ADMIN.
    """
    document_record: (
        LegalDocumentModel | None
    ) = await identity_repository.get_legal_document(
        db_session, consent_request.legal_document_id
    )
    if document_record is None:
        raise LegalDocumentNotFoundError("The legal document does not exist")
    current_record = await identity_repository.find_current_legal_document(
        db_session, document_record.purpose
    )
    if (
        current_record is None
        or current_record.legal_document_id != document_record.legal_document_id
    ):
        raise LegalDocumentConflictError("A newer version of this text is in force")
    is_agreement = (
        document_record.purpose == LegalDocumentPurpose.DATA_PROCESSING_AGREEMENT.value
    )
    if is_agreement != consent_request.on_behalf_of_organization:
        raise LegalDocumentConflictError(
            "Only the data processing agreement is accepted for an organization"
        )
    if is_agreement and not principal.has_any_role(UserRole.ORG_ADMIN):
        raise AccessDeniedError("Only the ORG_ADMIN accepts for the organization")
    organization_id = principal.organization_id if is_agreement else None
    existing = await identity_repository.find_consent(
        db_session,
        user_id=principal.user_id,
        legal_document_id=document_record.legal_document_id,
        organization_id=organization_id,
    )
    if existing is not None:
        return ConsentResponse.model_validate(existing)
    # The proof of where it was accepted: the app's device label, else the
    # request's user agent, cut to the column width.
    user_agent = None if client_context is None else client_context.user_agent
    proof_device_label = (device_label or user_agent or "")[:100] or None
    try:
        # A savepoint, so a lost race does not poison the request's
        # transaction: the same consent accepted twice at once is one row.
        async with db_session.begin_nested():
            consent_record = await identity_repository.insert_consent(
                db_session,
                {
                    "user_id": principal.user_id,
                    "organization_id": organization_id,
                    "legal_document_id": document_record.legal_document_id,
                    "ip_address": None
                    if client_context is None
                    else client_context.ip_address,
                    "device_label": proof_device_label,
                    "accepted_at": utc_now(),
                },
            )
    except IntegrityError:
        existing = await identity_repository.find_consent(
            db_session,
            user_id=principal.user_id,
            legal_document_id=document_record.legal_document_id,
            organization_id=organization_id,
        )
        if existing is None:
            raise
        return ConsentResponse.model_validate(existing)
    return ConsentResponse.model_validate(consent_record)


async def list_my_consents(
    db_session: AsyncSession, principal: Principal
) -> list[ConsentResponse]:
    """List what the caller accepted, personally or for an organization.

    Args:
        db_session: Current database session.
        principal: The caller.

    Returns:
        The caller's consent records, newest first.
    """
    consent_records = await identity_repository.list_consents_by_user(
        db_session, principal.user_id
    )
    return [ConsentResponse.model_validate(record) for record in consent_records]
