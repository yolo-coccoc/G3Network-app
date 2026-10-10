"""Start-up wiring shared by every process that runs backend code (BL-19, CV-21).

Some reactions cross domains that may not import each other, so they are
registered as hooks from above the domains (``membership_end_hooks``,
``fleet_visibility``, ``billing_hooks``, ``notification_hooks``). A hook lives
in a module-level variable, so each process starts with every slot empty and
an empty slot is skipped silently: a session ended there would go unbilled, an
alert would skip the checked-in driver and the fleet limit.

So there is one function, `register_all_hooks`, and every process calls it at
start-up: the API (``app/api/main.py``), each ``entrypoint.py`` and the
identity bootstrap. No process picks the hooks it thinks it needs; a hook a
process never fires costs nothing. ``tests/test_startup_hooks_smoke.py`` fails
when a process module does not call it or a slot stays empty.

Registering the hooks imports the services of most domains, whose models
reference each other's tables, so the full model registry is loaded here for
every process (CS-27).
"""

import app.api.billing_hooks as billing_hooks
import app.api.fleet_visibility as fleet_visibility
import app.api.membership_end_hooks as membership_end_hooks
import app.api.notification_hooks as notification_hooks
import app.libs.db.model_registry  # noqa: F401


def register_all_hooks() -> None:
    """Register every cross-domain hook in this process.

    Fills the identity membership-end hooks (DR-10), the fleet visibility
    resolver (FL-13), the notification routing hooks (NT-15) and the
    session-ended hooks (BL-19).

    Side Effects:
        Sets the process-wide hook slots of the identity, notifications and
        charging_sessions services; idempotent, so calling it twice changes
        nothing.
    """
    membership_end_hooks.register_membership_end_hooks()
    fleet_visibility.register_fleet_visibility()
    notification_hooks.register_notification_hooks()
    billing_hooks.register_session_billing_hooks()
