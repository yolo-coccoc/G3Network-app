"""Runtime side of the table change history (DM rules in ``database.md``).

A tracked table (``@tracked *`` in the DBML) has a ``<singular>_history``
table filled by a database trigger (built by ``app.libs.db.history_ddl``).
The trigger copies the whole old row and reads *who* and *why* from two
transaction-local settings. A service or repository that updates a tracked
row calls ``set_change_context`` first, in the same transaction as the
UPDATE; the settings vanish when the transaction ends, so a context never
leaks into the next request on a pooled connection.

A tracked UPDATE without a reason fails: the trigger raises (SQLSTATE
``MISSING_CHANGE_REASON_SQLSTATE``), which rolls the change back. A
process acting on its own (the OCPP gateway, a worker) passes
``changed_by=None`` with a fixed reason. (From 2026-10-02 to 2026-10-15 the
trigger recorded "Unspecified change" instead, DM-29; hardening D4 ended
that deviation once every path set a reason.)
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.common.errors import InvalidInputError

# Names of the transaction-local settings the trigger reads. They are part
# of the contract with the SQL written by ``history_ddl``.
CHANGE_USER_SETTING = "app.change_user_id"
CHANGE_REASON_SETTING = "app.change_reason"

# SQLSTATE the trigger raises when a tracked row is updated with no reason
# set: a custom class-"HC" code, so a caller can tell it from other errors.
MISSING_CHANGE_REASON_SQLSTATE = "HC001"

# Width of ``change_reason`` in every history table.
CHANGE_REASON_MAX_LENGTH = 200


async def set_change_context(
    db_session: AsyncSession,
    *,
    changed_by: UUID | None,
    change_reason: str,
) -> None:
    """Record who is changing tracked rows and why, for this transaction.

    Call it before the UPDATE of a tracked row, in the same transaction. The
    values are transaction-local (``set_config(..., true)``): they end with
    the commit or rollback.

    Args:
        db_session: Session owned by the entry boundary.
        changed_by: The acting user, or ``None`` when the system acts.
        change_reason: Why the row changes: typed by the person for an
            administrative decision, a fixed text for a routine action.

    Raises:
        InvalidInputError: If the reason is blank or longer than 200
            characters (a 400, never a 500: a reason that slipped past a
            schema is still the caller's input).

    Side Effects:
        Runs one ``SELECT set_config(...)``; does not commit or roll back.
    """
    if not change_reason.strip():
        raise InvalidInputError("A reason must not be blank")
    if len(change_reason) > CHANGE_REASON_MAX_LENGTH:
        raise InvalidInputError(
            f"A reason is longer than {CHANGE_REASON_MAX_LENGTH} characters"
        )
    await db_session.execute(
        text(
            "SELECT set_config(:user_setting, :user_id, true), "
            "set_config(:reason_setting, :reason, true)"
        ),
        {
            "user_setting": CHANGE_USER_SETTING,
            "user_id": "" if changed_by is None else str(changed_by),
            "reason_setting": CHANGE_REASON_SETTING,
            "reason": change_reason,
        },
    )
