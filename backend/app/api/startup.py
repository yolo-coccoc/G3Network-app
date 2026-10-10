"""Start-up wiring shared by every process that runs backend code (BL-19).

Some reactions cross domains that may not import each other, so they are
registered as hooks from above the domains (``membership_end_hooks``,
``fleet_visibility``, ``billing_hooks``). The API registers all of them when it
is imported. The OCPP gateway ends sessions too (a charger's stop message) and
must bill them, so it registers the session hooks with the same function.
"""

import app.api.billing_hooks as billing_hooks
import app.api.fleet_visibility as fleet_visibility
import app.api.membership_end_hooks as membership_end_hooks


def register_session_hooks() -> None:
    """Register the reactions to the end of a charging session (billing).

    Side Effects:
        Fills the charging_sessions hook list of this process; idempotent.
    """
    billing_hooks.register_session_billing_hooks()


def register_api_hooks() -> None:
    """Register every cross-domain hook the HTTP API needs.

    Side Effects:
        Fills the identity membership-end hooks, the fleet visibility resolver
        and the session hooks of this process; idempotent.
    """
    membership_end_hooks.register_membership_end_hooks()
    fleet_visibility.register_fleet_visibility()
    register_session_hooks()
