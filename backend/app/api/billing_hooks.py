"""Wiring of billing to the end of a charging session (PAY-10, BL-10, BL-19).

When a session ends the bill must be computed (or voided) and the payer's
wallet debited, in the same transaction as the end itself. The OCPP gateway's
stop handlers call ``charging_sessions.complete_session`` from a different
process than the API, and ``charging_sessions`` may not import ``billing`` (the
edge between them stays one-way), so it exposes a hook list
(``register_session_ended_hook``) and this module, above the domains like
``membership_end_hooks.py``, registers the billing function on it. Every
process registers it through `app.api.startup.register_all_hooks` (CV-21).
"""

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.billing.service as billing_service
import app.domains.charging_sessions.service as charging_session_service
from app.domains.charging_sessions.types import SessionEndedEvent, SessionStatus


async def bill_ended_session(
    db_session: AsyncSession, event: SessionEndedEvent
) -> None:
    """Settle the bill of a completed session, or void that of an abandoned one.

    Args:
        db_session: The session-ending transaction.
        event: What ended and the readings needed to bill it.

    Side Effects:
        Updates the session's bill and, for a completed one, debits the
        scanning user's wallet.
    """
    if event.status is SessionStatus.COMPLETED and event.started_by is not None:
        await billing_service.settle_session_bill(
            db_session,
            session_id=event.session_id,
            payer_user_id=event.started_by,
            meter_start_wh=event.meter_start_wh,
            meter_stop_wh=event.meter_stop_wh,
            last_measured_wh=event.last_measured_wh,
        )
    elif event.status is SessionStatus.ABANDONED:
        await billing_service.void_session_bill(db_session, event.session_id)


def register_session_billing_hooks() -> None:
    """Register billing on the session-ended hook list.

    Side Effects:
        Adds `bill_ended_session` to the charging_sessions hook list; calling
        it again changes nothing.
    """
    charging_session_service.register_session_ended_hook(bill_ended_session)
