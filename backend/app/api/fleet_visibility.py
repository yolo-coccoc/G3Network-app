"""Wiring of the fleet limit (FL-10) to the vehicle endpoints.

A fleet manager or dispatcher can be limited to some fleets
(`fleet_user_assignments`). The `vehicles` domain cannot call `fleet`
(FL-01, one-way edges) and `identity` depends on no domain, so neither can
ask which trucks are visible. This module sits above the domains, in the HTTP
layer like `membership_end_hooks.py` and `vehicle_transfer.py`: it registers
the fleet function as the resolver behind the `get_visible_vehicle_ids`
request dependency that the vehicle endpoints use. The choice is recorded in
the decision log (FL-13).
"""

import app.domains.fleet.service as fleet_service
import app.domains.identity.dependencies as identity_dependencies


def register_fleet_visibility() -> None:
    """Register the fleet domain as the source of the visible truck set.

    Side Effects:
        Sets the resolver of `identity.dependencies`; calling it again changes
        nothing.
    """
    identity_dependencies.register_visible_vehicle_resolver(
        fleet_service.resolve_principal_visible_vehicle_ids
    )
