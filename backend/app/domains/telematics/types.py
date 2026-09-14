"""Các kiểu dữ liệu và DTO nội bộ trong domain telematics."""

import enum
from dataclasses import dataclass
from uuid import UUID


class TelematicStatus(str, enum.Enum):
    """Trạng thái vận hành của thiết bị Telematic."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"


@dataclass(frozen=True)
class TelematicVehicleMapping:
    """Ánh xạ một thiết bị telematic đang hoạt động sang một xe.

    Attributes:
        telematic_id: ID nội bộ của thiết bị.
        vehicle_id: ID nội bộ của xe được gán.
    """

    telematic_id: UUID
    vehicle_id: UUID
