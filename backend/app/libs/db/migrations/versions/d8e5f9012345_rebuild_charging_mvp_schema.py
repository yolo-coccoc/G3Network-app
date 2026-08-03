"""Rebuild schema charging theo contract MVP lý tưởng.

Migration này giữ nguyên các migration charging trước đó và dùng một lần
rebuild có chủ đích để loại toàn bộ cột/table technical status, metadata thiết
bị và reliability khỏi database local. Dữ liệu của sáu bảng charging bị xóa
trong quá trình upgrade; đây là phạm vi mất dữ liệu đã được xác nhận cho MVP.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

revision: str = "d8e5f9012345"
down_revision: str | None = "c7d4e8f90123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_TABLES = (
    "charging_session_meter_values",
    "charging_session_events",
    "charging_sessions",
    "charging_connectors",
    "charging_evses",
    "charging_stations",
)

_CHARGING_ENUMS = (
    "chargingsessioneventtype",
    "chargingsessionstatus",
    "chargingconnectoradministrativestatus",
    "chargingtechnicalstatus",
    "chargingevseadministrativestatus",
    "chargingstationconnectionstatus",
    "chargingstationadministrativestatus",
)


def _enum(*values: str, name: str) -> PGEnum:
    """Tạo tham chiếu enum không tự phát sinh ``CREATE TYPE`` lần nữa.

    Enum type đã được tạo bằng SQL tường minh trong migration để hành vi
    online và offline nhất quán; ``create_type=False`` ngăn SQLAlchemy tạo
    trùng type khi dựng bảng.

    Args:
        values: Các label của enum dùng cho DDL offline.
        name: Tên PostgreSQL enum đã được tạo trước đó.

    Returns:
        PostgreSQL enum type chỉ dùng làm kiểu cột.
    """
    return PGEnum(*values, name=name, create_type=False)


def upgrade() -> None:
    """Thay schema hiện có bằng đúng sáu bảng active của MVP.

    Việc drop/recreate giữ migration ngắn và loại dứt điểm các cột legacy còn
    sót lại sau migration c7. Topology được tạo trước aggregate; hai bảng
    history được chuyển thành hypertable sau khi tạo khóa và foreign key.
    """
    _drop_charging_schema()
    _create_active_schema()


def downgrade() -> None:
    """Khôi phục schema ngay trước migration này để rollback local đối xứng.

    Downgrade phục hồi trạng thái sau c7, gồm metadata/status của topology và
    các cột reliability đã bị c7 loại khỏi session history. Payload cũ không
    thể khôi phục vì upgrade chủ ý đã xóa toàn bộ dữ liệu charging.
    """
    _drop_charging_schema()
    _create_c7_schema()


def _drop_charging_schema() -> None:
    """Xóa sáu bảng charging và PostgreSQL enum type phụ thuộc."""
    # Drop từ bảng con lên bảng cha để không phụ thuộc vào CASCADE và giữ rõ
    # ranh giới dữ liệu bị xóa trong migration.
    for table_name in _ACTIVE_TABLES:
        op.drop_table(table_name)

    for enum_name in _CHARGING_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")


def _create_active_schema() -> None:
    """Tạo sáu bảng active và các constraint/index tương ứng metadata."""
    _create_enum(
        "chargingsessionstatus",
        "active",
        "completed",
    )
    _create_enum(
        "chargingsessioneventtype",
        "Started",
        "Updated",
        "Ended",
    )
    op.create_table(
        "charging_stations",
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_identity", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("station_id"),
        sa.UniqueConstraint("ocpp_identity", name="uq_charging_stations_ocpp_identity"),
    )
    op.create_index(
        "ix_charging_stations_deleted_at", "charging_stations", ["deleted_at"]
    )

    op.create_table(
        "charging_evses",
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_evse_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ocpp_evse_id > 0", name="ck_charging_evses_ocpp_id_positive"
        ),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["charging_stations.station_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("evse_id"),
        sa.UniqueConstraint(
            "station_id", "ocpp_evse_id", name="uq_charging_evses_station_ocpp_id"
        ),
    )
    op.create_index(
        "ix_charging_evses_station_deleted",
        "charging_evses",
        ["station_id", "deleted_at"],
    )

    op.create_table(
        "charging_connectors",
        sa.Column("connector_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_connector_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ocpp_connector_id > 0",
            name="ck_charging_connectors_ocpp_id_positive",
        ),
        sa.ForeignKeyConstraint(
            ["evse_id"], ["charging_evses.evse_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("connector_id"),
        sa.UniqueConstraint(
            "evse_id",
            "ocpp_connector_id",
            name="uq_charging_connectors_evse_ocpp_id",
        ),
    )
    op.create_index(
        "ix_charging_connectors_evse_deleted",
        "charging_connectors",
        ["evse_id", "deleted_at"],
    )

    _create_active_session_tables()


def _create_active_session_tables() -> None:
    """Tạo aggregate, event history và meter history của active path."""
    op.create_table(
        "charging_sessions",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("connector_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_transaction_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            _enum("active", "completed", name="chargingsessionstatus"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meter_start_wh", sa.Numeric(precision=24, scale=3), nullable=True),
        sa.Column("meter_end_wh", sa.Numeric(precision=24, scale=3), nullable=True),
        sa.Column(
            "energy_delivered_wh",
            sa.Numeric(precision=24, scale=3),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["charging_stations.station_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evse_id"], ["charging_evses.evse_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["charging_connectors.connector_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint(
            "station_id",
            "ocpp_transaction_id",
            name="uq_charging_sessions_station_transaction",
        ),
    )
    for index_name, columns in (
        ("ix_charging_sessions_status_updated", ["status", "updated_at"]),
        ("ix_charging_sessions_station_status", ["station_id", "status"]),
        ("ix_charging_sessions_evse_status", ["evse_id", "status"]),
        ("ix_charging_sessions_connector_status", ["connector_id", "status"]),
        ("ix_charging_sessions_started_at", ["started_at"]),
        ("ix_charging_sessions_ended_at", ["ended_at"]),
    ):
        op.create_index(index_name, "charging_sessions", columns)

    op.create_table(
        "charging_session_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column(
            "event_type",
            _enum("Started", "Updated", "Ended", name="chargingsessioneventtype"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["charging_sessions.session_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", "event_occurred_at"),
    )
    op.create_index(
        "ix_charging_session_events_session_time",
        "charging_session_events",
        ["session_id", "event_occurred_at", "event_id"],
    )
    op.create_table(
        "charging_session_meter_values",
        sa.Column("meter_value_id", sa.UUID(), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("value_wh", sa.Numeric(precision=24, scale=3), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["charging_sessions.session_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("meter_value_id", "sampled_at"),
    )
    op.create_index(
        "ix_charging_meter_session_sampled",
        "charging_session_meter_values",
        ["session_id", "sampled_at", "meter_value_id"],
    )

    # Chỉ bảng event và sample là time-series; session aggregate vẫn là bảng
    # quan hệ để giữ lifecycle và foreign key topology đơn giản.
    for table_name, time_column in (
        ("charging_session_events", "event_occurred_at"),
        ("charging_session_meter_values", "sampled_at"),
    ):
        op.execute(
            "SELECT create_hypertable("
            f"'{table_name}', '{time_column}', "
            "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);"
        )


def _create_c7_schema() -> None:
    """Khôi phục schema topology/session sau migration c7 để downgrade đối xứng."""
    _create_c7_topology_tables()
    _create_c7_session_tables()


def _create_enum(name: str, *values: str) -> None:
    """Tạo PostgreSQL enum trước khi tạo cột tham chiếu tới enum đó.

    Args:
        name: Tên type PostgreSQL cố định trong contract migration.
        values: Các label theo thứ tự public của enum.
    """
    quoted_values = ", ".join(f"'{value}'" for value in values)
    op.execute(f"CREATE TYPE {name} AS ENUM ({quoted_values})")


def _create_c7_topology_tables() -> None:
    """Tạo topology có metadata và technical status của schema c7."""
    op.create_table(
        "charging_stations",
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_identity", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("manufacturer", sa.String(length=200), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("serial_number", sa.String(length=200), nullable=True),
        sa.Column("firmware_version", sa.String(length=100), nullable=True),
        sa.Column(
            "location",
            Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column(
            "administrative_status",
            _enum(
                "active",
                "inactive",
                "maintenance",
                name="chargingstationadministrativestatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "connection_status",
            _enum(
                "unknown",
                "connected",
                "offline",
                name="chargingstationconnectionstatus",
            ),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_boot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("station_id"),
        sa.UniqueConstraint("ocpp_identity", name="uq_charging_stations_ocpp_identity"),
    )
    op.create_index(
        "ix_charging_stations_admin_deleted",
        "charging_stations",
        ["administrative_status", "deleted_at"],
    )
    op.create_index(
        "ix_charging_stations_connection_last_seen",
        "charging_stations",
        ["connection_status", sa.text("last_seen_at DESC")],
    )
    op.create_index(
        "ix_charging_stations_deleted_at", "charging_stations", ["deleted_at"]
    )
    op.create_index(
        "ix_charging_stations_location_gist",
        "charging_stations",
        ["location"],
        postgresql_using="gist",
    )

    op.create_table(
        "charging_evses",
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_evse_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column(
            "administrative_status",
            _enum("active", "inactive", name="chargingevseadministrativestatus"),
            nullable=False,
        ),
        sa.Column(
            "technical_status",
            _enum(
                "unknown",
                "available",
                "occupied",
                "unavailable",
                "faulted",
                name="chargingtechnicalstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "capabilities",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ocpp_evse_id > 0", name="ck_charging_evses_ocpp_id_positive"
        ),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["charging_stations.station_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("evse_id"),
        sa.UniqueConstraint(
            "station_id", "ocpp_evse_id", name="uq_charging_evses_station_ocpp_id"
        ),
    )
    op.create_index(
        "ix_charging_evses_station_deleted",
        "charging_evses",
        ["station_id", "deleted_at"],
    )
    op.create_index(
        "ix_charging_evses_technical_station",
        "charging_evses",
        ["technical_status", "station_id"],
    )

    op.create_table(
        "charging_connectors",
        sa.Column("connector_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_connector_id", sa.Integer(), nullable=False),
        sa.Column("connector_type", sa.String(length=100), nullable=True),
        sa.Column("max_power_kw", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column(
            "administrative_status",
            _enum(
                "active",
                "inactive",
                name="chargingconnectoradministrativestatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "technical_status",
            _enum(
                "unknown",
                "available",
                "occupied",
                "unavailable",
                "faulted",
                name="chargingtechnicalstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "capabilities",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ocpp_connector_id > 0",
            name="ck_charging_connectors_ocpp_id_positive",
        ),
        sa.CheckConstraint(
            "max_power_kw IS NULL OR max_power_kw > 0",
            name="ck_charging_connectors_max_power_positive",
        ),
        sa.ForeignKeyConstraint(
            ["evse_id"], ["charging_evses.evse_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("connector_id"),
        sa.UniqueConstraint(
            "evse_id",
            "ocpp_connector_id",
            name="uq_charging_connectors_evse_ocpp_id",
        ),
    )
    op.create_index(
        "ix_charging_connectors_evse_deleted",
        "charging_connectors",
        ["evse_id", "deleted_at"],
    )
    op.create_index(
        "ix_charging_connectors_technical_evse",
        "charging_connectors",
        ["technical_status", "evse_id"],
    )


def _create_c7_session_tables() -> None:
    """Tạo session/history schema c7 sau khi reliability fields đã bị bỏ."""
    op.create_table(
        "charging_sessions",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("connector_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_transaction_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            _enum("active", "completed", name="chargingsessionstatus"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meter_start_wh", sa.Numeric(precision=24, scale=3), nullable=True),
        sa.Column("meter_end_wh", sa.Numeric(precision=24, scale=3), nullable=True),
        sa.Column(
            "energy_delivered_wh",
            sa.Numeric(precision=24, scale=3),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["charging_stations.station_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evse_id"], ["charging_evses.evse_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["charging_connectors.connector_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint(
            "station_id",
            "ocpp_transaction_id",
            name="uq_charging_sessions_station_transaction",
        ),
    )
    for index_name, columns in (
        ("ix_charging_sessions_status_updated", ["status", "updated_at"]),
        ("ix_charging_sessions_station_status", ["station_id", "status"]),
        ("ix_charging_sessions_evse_status", ["evse_id", "status"]),
        ("ix_charging_sessions_connector_status", ["connector_id", "status"]),
        ("ix_charging_sessions_started_at", ["started_at"]),
        ("ix_charging_sessions_ended_at", ["ended_at"]),
    ):
        op.create_index(index_name, "charging_sessions", columns)

    op.create_table(
        "charging_session_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column(
            "event_type",
            _enum("Started", "Updated", "Ended", name="chargingsessioneventtype"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["charging_sessions.session_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", "event_occurred_at"),
    )
    op.create_index(
        "ix_charging_session_events_session_time",
        "charging_session_events",
        ["session_id", "event_occurred_at", "event_id"],
    )

    op.create_table(
        "charging_session_meter_values",
        sa.Column("meter_value_id", sa.UUID(), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("value_wh", sa.Numeric(precision=24, scale=3), nullable=False),
        sa.CheckConstraint(
            "value_wh >= 0", name="ck_charging_meter_value_wh_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["charging_sessions.session_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("meter_value_id", "sampled_at"),
    )
    op.create_index(
        "ix_charging_meter_session_sampled",
        "charging_session_meter_values",
        ["session_id", "sampled_at", "meter_value_id"],
    )
    for table_name, time_column in (
        ("charging_session_events", "event_occurred_at"),
        ("charging_session_meter_values", "sampled_at"),
    ):
        op.execute(
            "SELECT create_hypertable("
            f"'{table_name}', '{time_column}', "
            "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);"
        )
