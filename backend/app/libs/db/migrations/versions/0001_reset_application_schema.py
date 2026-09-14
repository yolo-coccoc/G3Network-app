"""Xóa schema nghiệp vụ cũ trước khi dựng lại database MVP.

Migration này chỉ phục vụ giai đoạn khởi tạo local. Nó xóa các bảng và enum
do các migration cũ của ứng dụng tạo ra, nhưng giữ lại database system,
extension PostGIS/TimescaleDB và bảng ``alembic_version``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_reset_application_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APPLICATION_TABLES = (
    "charging_station_status_events",
    "charging_session_meter_values",
    "charging_session_events",
    "charging_sessions",
    "charging_connectors",
    "charging_evses",
    "charging_stations",
    "vehicle_telemetry",
    "telemetry_alerts",
    "telematics",
    "vehicles",
)

_APPLICATION_ENUMS = (
    "vehiclestatus",
    "telematicstatus",
    "chargingstationadministrativestatus",
    "chargingstationconnectionstatus",
    "chargingevseadministrativestatus",
    "chargingconnectoradministrativestatus",
    "chargingtechnicalstatus",
    "chargingsessionstatus",
    "chargingsessioneventtype",
    "chargingreconciliationstatus",
    "chargingendreason",
    "chargingstate",
    "chargingstationsourceaction",
    "telemetryalertstatus",
    "telemetryalerttype",
)


def upgrade() -> None:
    """Xóa schema nghiệp vụ cũ để chuẩn bị cho baseline mới."""
    # CASCADE cần thiết vì schema cũ có foreign key, hypertable và constraint
    # không còn nằm trong contract mới. Danh sách bảng được cố định trong code.
    for table_name in _APPLICATION_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')

    # Xóa enum sau bảng để không còn cột nào tham chiếu tới type cũ.
    for enum_name in _APPLICATION_ENUMS:
        op.execute(f'DROP TYPE IF EXISTS "{enum_name}" CASCADE')


def downgrade() -> None:
    """Không khôi phục schema cũ sau khi reset database khởi tạo."""
    # Reset là điểm bắt đầu của graph mới; các migration sau chịu trách nhiệm
    # tạo và xóa schema hiện tại theo từng bounded context.
