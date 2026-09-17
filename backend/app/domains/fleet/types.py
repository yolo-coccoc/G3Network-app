"""Shared internal data types and DTOs used within the fleet domain."""

import enum
from dataclasses import dataclass
from uuid import UUID


class FleetStatus(str, enum.Enum):
    """Supported lifecycle statuses of a fleet."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True)
class FleetReference:
    """Minimal information for other domains to reference a fleet.

    Not consumed by any caller yet - established for consistency with
    every other CRUD domain in this backend, each of which exposes a
    `resolve_*` DTO for cross-domain lookups.

    Attributes:
        fleet_id: Internal ID of the fleet.
        name: Fleet's display name.
    """

    fleet_id: UUID
    name: str
