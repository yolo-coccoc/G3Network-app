"""Các kiểu giá trị dùng chung trong domain thiết bị Telematic."""

import enum


class TelematicStatus(str, enum.Enum):
    """Trạng thái vận hành của thiết bị Telematic."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"
