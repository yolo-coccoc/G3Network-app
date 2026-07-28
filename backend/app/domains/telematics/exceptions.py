"""Domain exceptions cho CRUD thiết bị Telematic."""


class TelematicError(Exception):
    """Lỗi nghiệp vụ chung của domain Telematic."""


class TelematicNotFoundError(TelematicError):
    """Không tìm thấy thiết bị Telematic."""


class TelematicConflictError(TelematicError):
    """Dữ liệu thiết bị hoặc mapping xe bị trùng."""
