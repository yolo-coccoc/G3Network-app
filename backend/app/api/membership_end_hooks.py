"""Wiring of the membership-end hooks across domains (DR-10).

Ending or locking a membership (``identity``) must also close the person's
driver profile and open driving session (``drivers``), in the same
transaction. ``identity`` depends on no domain and ``drivers`` depends on
``identity``, so neither can call the other from its own code. This module
sits above the domains, in the HTTP layer like ``vehicle_transfer.py``, and
registers the drivers function as a hook that the identity member service
runs after the change. The choice is recorded in the decision log (DR-15).
"""

import app.domains.drivers.service as driver_service
import app.domains.identity.service as identity_service


def register_membership_end_hooks() -> None:
    """Register every cross-domain reaction to the end or lock of a membership.

    Side Effects:
        Adds the drivers domain's `handle_membership_end` to the identity
        hook list; calling it again changes nothing.
    """
    identity_service.register_membership_end_hook(driver_service.handle_membership_end)
