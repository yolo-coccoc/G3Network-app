"""Ngoại lệ nghiệp vụ của domain charging_stations."""


class ChargingStationNotFoundError(Exception):
    """Không tìm thấy station đang được tham chiếu."""


class ChargingTopologyConflictError(Exception):
    """Topology station, EVSE hoặc connector vi phạm identity đã tồn tại."""
