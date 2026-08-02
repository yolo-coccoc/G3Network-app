"""Ngoại lệ nghiệp vụ của domain charging_sessions."""


class ChargingSessionNotFoundError(Exception):
    """Không tìm thấy aggregate phiên sạc được tham chiếu."""


class ChargingSessionInputError(Exception):
    """Dữ liệu chuẩn hóa từ adapter vi phạm contract ingestion."""


# Legacy exception intentionally disabled: ideal MVP không có duplicate,
# idempotency hoặc payload conflict. Khôi phục cùng future.md mục 27.
# class ChargingSessionConflictError(Exception):
#     """Event hoặc meter có cùng logical identity nhưng payload mâu thuẫn."""

# ---------------------------------------------------------------------------
# LEGACY COMPONENT (COMMENTED OUT FOR THE IDEAL MVP)
# ---------------------------------------------------------------------------
# class ChargingSessionConflictError(Exception):
#     """Event hoặc meter có cùng logical identity nhưng payload mâu thuẫn."""
#
#
# class ChargingSessionInputError(Exception):
#     """Dữ liệu chuẩn hóa từ adapter vi phạm contract ingestion."""
#
