"""Ngoại lệ nghiệp vụ của domain charging_sessions."""


class ChargingSessionNotFoundError(Exception):
    """Không tìm thấy aggregate phiên sạc được tham chiếu."""


class ChargingSessionInputError(Exception):
    """Dữ liệu chuẩn hóa từ adapter vi phạm contract ingestion."""
