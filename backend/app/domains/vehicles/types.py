"""Các kiểu dữ liệu và DTO nội bộ dùng chung trong domain vehicles."""

import enum
from dataclasses import dataclass
from uuid import UUID


class VehicleStatus(str, enum.Enum):
    """Các trạng thái vòng đời được hỗ trợ của xe."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"
    DECOMMISSIONED = "DECOMMISSIONED"


@dataclass(frozen=True)
class VehicleReference:
    """Thông tin tối thiểu để domain khác tham chiếu đến một xe.

    Attributes:
        vehicle_id: ID nội bộ của xe.
        vin: Số khung dùng để nhận diện xe trong nghiệp vụ.
    """

    vehicle_id: UUID
    vin: str
