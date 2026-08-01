"""Ngoại lệ nghiệp vụ của domain charging_sessions."""


class ChargingSessionNotFoundError(Exception):
    """Không tìm thấy aggregate phiên sạc được tham chiếu."""


class ChargingSessionConflictError(Exception):
    """Event hoặc meter có cùng logical identity nhưng payload mâu thuẫn."""
