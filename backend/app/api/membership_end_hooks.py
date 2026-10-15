"""Wiring of the membership-end and role-revoked hooks across domains (DR-10).

Ending or locking a membership (``identity``) must also close the person's
driver profile and open driving session (``drivers``), and ending it closes
their fleet limits (``fleet``, hardening D6), in the same transaction.
Revoking a member's DRIVER role must likewise end their open driving session,
since check-in requires the role. ``identity`` depends on no domain and
``drivers`` and ``fleet`` depend on ``identity``, so neither side can call the
other from its own code. This module sits above the domains, in the HTTP layer
like ``vehicle_transfer.py``, and registers their functions as hooks that the
identity member service runs after the change. The choice is recorded in the
decision log (DR-15).
"""

import app.domains.drivers.service as driver_service
import app.domains.fleet.service as fleet_service
import app.domains.identity.service as identity_service


def register_membership_end_hooks() -> None:
    """Register every cross-domain reaction to a membership or role change.

    Side Effects:
        Adds the drivers and fleet domains' `handle_membership_end` to the
        identity membership-end hook list, and the drivers domain's
        `handle_role_revoked` to the role-revoked hook list; calling it again
        changes nothing.
    """
    identity_service.register_membership_end_hook(driver_service.handle_membership_end)
    identity_service.register_membership_end_hook(fleet_service.handle_membership_end)
    identity_service.register_role_revoked_hook(driver_service.handle_role_revoked)
