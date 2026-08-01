"""Tạo topology trạm sạc, technical history và dữ liệu phiên sạc MVP."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography

revision: str = "b2c7d4e8f901"
down_revision: str | None = "5e7b1c9d2a44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    """Tạo PostgreSQL enum dùng chung trong migration.

    Args:
        name: Tên type PostgreSQL.
        values: Các label đã chốt trong contract.

    Returns:
        SQLAlchemy enum type.
    """
    return sa.Enum(*values, name=name)


def upgrade() -> None:
    """Tạo toàn bộ bảng charging và chuyển history sang hypertable.

    Topology được tạo trước history/session để foreign key luôn tham chiếu tới
    bảng đã tồn tại. Mỗi unique index của hypertable đều bao gồm partition key,
    vì TimescaleDB yêu cầu điều kiện này để bảo toàn uniqueness theo chunk.
    """
    # Hạ tầng dev đã bật extension trong infra/db/init; IF NOT EXISTS giúp
    # migration vẫn tự mô tả đầy đủ yêu cầu khi chạy trên database đã tồn tại.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

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
                "chargingstationadministrativestatus",
                "active",
                "inactive",
                "maintenance",
            ),
            nullable=False,
        ),
        sa.Column(
            "connection_status",
            _enum("chargingstationconnectionstatus", "unknown", "connected", "offline"),
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
            _enum("chargingevseadministrativestatus", "active", "inactive"),
            nullable=False,
        ),
        sa.Column(
            "technical_status",
            _enum(
                "chargingtechnicalstatus",
                "unknown",
                "available",
                "occupied",
                "unavailable",
                "faulted",
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
            _enum("chargingconnectoradministrativestatus", "active", "inactive"),
            nullable=False,
        ),
        sa.Column(
            "technical_status",
            _enum(
                "chargingtechnicalstatus",
                "unknown",
                "available",
                "occupied",
                "unavailable",
                "faulted",
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

    op.create_table(
        "charging_station_status_events",
        sa.Column("status_event_id", sa.UUID(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=True),
        sa.Column("connector_id", sa.UUID(), nullable=True),
        sa.Column(
            "source_action",
            _enum(
                "chargingstationsourceaction",
                "BootNotification",
                "Heartbeat",
                "StatusNotification",
                "NotifyEvent",
            ),
            nullable=False,
        ),
        sa.Column("technical_status", sa.String(length=50), nullable=True),
        sa.Column("event_code", sa.String(length=100), nullable=True),
        sa.Column("ocpp_message_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "sanitized_raw_payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.CheckConstraint(
            "connector_id IS NULL OR evse_id IS NOT NULL",
            name="ck_charging_status_connector_requires_evse",
        ),
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
        sa.PrimaryKeyConstraint("status_event_id", "recorded_at"),
        sa.UniqueConstraint(
            "station_id",
            "idempotency_key",
            "recorded_at",
            name="uq_charging_status_station_idempotency_recorded",
        ),
    )
    _create_status_history_indexes()

    op.create_table(
        "charging_sessions",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("connector_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_transaction_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            _enum(
                "chargingsessionstatus",
                "pending",
                "active",
                "ending",
                "completed",
                "interrupted",
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_meter_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_transaction_seq_no", sa.Integer(), nullable=True),
        sa.Column("meter_start_wh", sa.Numeric(precision=24, scale=3), nullable=True),
        sa.Column("meter_end_wh", sa.Numeric(precision=24, scale=3), nullable=True),
        sa.Column(
            "energy_delivered_wh", sa.Numeric(precision=24, scale=3), nullable=True
        ),
        sa.Column(
            "reconciliation_status",
            _enum(
                "chargingreconciliationstatus",
                "pending",
                "reconciled",
                "inconsistent",
                "unavailable",
            ),
            nullable=False,
        ),
        sa.Column("reconciliation_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "last_transaction_seq_no IS NULL OR last_transaction_seq_no >= 0",
            name="ck_charging_sessions_seq_non_negative",
        ),
        sa.CheckConstraint(
            "meter_start_wh IS NULL OR meter_start_wh >= 0",
            name="ck_charging_sessions_meter_start_non_negative",
        ),
        sa.CheckConstraint(
            "meter_end_wh IS NULL OR meter_end_wh >= 0",
            name="ck_charging_sessions_meter_end_non_negative",
        ),
        sa.CheckConstraint(
            "energy_delivered_wh IS NULL OR energy_delivered_wh >= 0",
            name="ck_charging_sessions_energy_non_negative",
        ),
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
    _create_session_indexes()

    op.create_table(
        "charging_session_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column(
            "event_type",
            _enum(
                "chargingsessioneventtype", "Started", "Updated", "Ended", "Interrupted"
            ),
            nullable=False,
        ),
        sa.Column("seq_no", sa.Integer(), nullable=False),
        sa.Column(
            "end_reason",
            _enum("chargingendreason", "normal", "abnormal", "offline", "unknown"),
            nullable=True,
        ),
        sa.Column(
            "charging_state",
            _enum("chargingstate", "pending", "active"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "sanitized_raw_payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.CheckConstraint(
            "seq_no >= 0", name="ck_charging_session_events_seq_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["charging_sessions.session_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", "event_occurred_at"),
        sa.UniqueConstraint(
            "session_id",
            "seq_no",
            "event_occurred_at",
            name="uq_charging_session_events_session_seq_time",
        ),
    )
    op.create_index(
        "ix_charging_session_events_session_time",
        "charging_session_events",
        ["session_id", "event_occurred_at", "event_id"],
    )
    op.create_index(
        "ix_charging_session_events_session_seq_time",
        "charging_session_events",
        ["session_id", "seq_no", "event_occurred_at"],
    )

    op.create_table(
        "charging_session_meter_values",
        sa.Column("meter_value_id", sa.UUID(), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("measurand", sa.String(length=100), nullable=False),
        sa.Column("phase", sa.String(length=50), nullable=True),
        sa.Column("context", sa.String(length=50), nullable=True),
        sa.Column("source_value", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("source_unit", sa.String(length=20), nullable=False),
        sa.Column("value_wh", sa.Numeric(precision=24, scale=3), nullable=False),
        sa.Column("seq_no", sa.Integer(), nullable=True),
        sa.Column("sample_idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "sanitized_raw_payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.CheckConstraint(
            "value_wh >= 0", name="ck_charging_meter_value_wh_non_negative"
        ),
        sa.CheckConstraint(
            "seq_no IS NULL OR seq_no >= 0",
            name="ck_charging_meter_seq_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["charging_sessions.session_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("meter_value_id", "sampled_at"),
        sa.UniqueConstraint(
            "session_id",
            "sample_idempotency_key",
            "sampled_at",
            name="uq_charging_meter_session_sample_time",
        ),
    )
    op.create_index(
        "ix_charging_meter_session_sampled",
        "charging_session_meter_values",
        ["session_id", "sampled_at", "meter_value_id"],
    )
    op.create_index(
        "ix_charging_meter_session_measurand_sampled",
        "charging_session_meter_values",
        ["session_id", "measurand", "sampled_at"],
    )

    # Chỉ các bảng có cột thời gian partition được chuyển thành hypertable;
    # aggregate charging_sessions vẫn là bảng quan hệ thông thường.
    for table_name, time_column in (
        ("charging_station_status_events", "recorded_at"),
        ("charging_session_events", "event_occurred_at"),
        ("charging_session_meter_values", "sampled_at"),
    ):
        op.execute(
            "SELECT create_hypertable("  # noqa: S608 - tên cố định trong migration
            f"'{table_name}', '{time_column}', "
            "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);"
        )


def _create_status_history_indexes() -> None:
    """Tạo index phục vụ truy vấn technical history theo topology và thời gian."""
    op.create_index(
        "ix_charging_status_station_recorded",
        "charging_station_status_events",
        ["station_id", sa.text("recorded_at DESC")],
    )
    op.create_index(
        "ix_charging_status_evse_recorded",
        "charging_station_status_events",
        ["evse_id", sa.text("recorded_at DESC")],
    )
    op.create_index(
        "ix_charging_status_connector_recorded",
        "charging_station_status_events",
        ["connector_id", sa.text("recorded_at DESC")],
    )
    op.create_index(
        "ix_charging_status_action_recorded",
        "charging_station_status_events",
        ["source_action", sa.text("recorded_at DESC")],
    )


def _create_session_indexes() -> None:
    """Tạo index phục vụ list/filter session monitoring."""
    op.create_index(
        "ix_charging_sessions_status_updated",
        "charging_sessions",
        ["status", sa.text("updated_at DESC")],
    )
    op.create_index(
        "ix_charging_sessions_station_status",
        "charging_sessions",
        ["station_id", "status"],
    )
    op.create_index(
        "ix_charging_sessions_evse_status", "charging_sessions", ["evse_id", "status"]
    )
    op.create_index(
        "ix_charging_sessions_connector_status",
        "charging_sessions",
        ["connector_id", "status"],
    )
    op.create_index(
        "ix_charging_sessions_started_at", "charging_sessions", ["started_at"]
    )
    op.create_index("ix_charging_sessions_ended_at", "charging_sessions", ["ended_at"])


def downgrade() -> None:
    """Gỡ charging schema theo thứ tự ngược và xóa các PostgreSQL enum type."""
    op.drop_index(
        "ix_charging_meter_session_measurand_sampled",
        table_name="charging_session_meter_values",
    )
    op.drop_index(
        "ix_charging_meter_session_sampled", table_name="charging_session_meter_values"
    )
    op.drop_table("charging_session_meter_values")

    op.drop_index(
        "ix_charging_session_events_session_seq_time",
        table_name="charging_session_events",
    )
    op.drop_index(
        "ix_charging_session_events_session_time",
        table_name="charging_session_events",
    )
    op.drop_table("charging_session_events")

    for index_name in (
        "ix_charging_sessions_ended_at",
        "ix_charging_sessions_started_at",
        "ix_charging_sessions_connector_status",
        "ix_charging_sessions_evse_status",
        "ix_charging_sessions_station_status",
        "ix_charging_sessions_status_updated",
    ):
        op.drop_index(index_name, table_name="charging_sessions")
    op.drop_table("charging_sessions")

    for index_name in (
        "ix_charging_status_action_recorded",
        "ix_charging_status_connector_recorded",
        "ix_charging_status_evse_recorded",
        "ix_charging_status_station_recorded",
    ):
        op.drop_index(index_name, table_name="charging_station_status_events")
    op.drop_table("charging_station_status_events")

    for table_name, indexes in (
        (
            "charging_connectors",
            (
                "ix_charging_connectors_technical_evse",
                "ix_charging_connectors_evse_deleted",
            ),
        ),
        (
            "charging_evses",
            (
                "ix_charging_evses_technical_station",
                "ix_charging_evses_station_deleted",
            ),
        ),
        (
            "charging_stations",
            (
                "ix_charging_stations_location_gist",
                "ix_charging_stations_deleted_at",
                "ix_charging_stations_connection_last_seen",
                "ix_charging_stations_admin_deleted",
            ),
        ),
    ):
        for index_name in indexes:
            op.drop_index(index_name, table_name=table_name)
        op.drop_table(table_name)

    for enum_name in (
        "chargingstate",
        "chargingendreason",
        "chargingsessioneventtype",
        "chargingreconciliationstatus",
        "chargingsessionstatus",
        "chargingstationsourceaction",
        "chargingconnectoradministrativestatus",
        "chargingtechnicalstatus",
        "chargingevseadministrativestatus",
        "chargingstationconnectionstatus",
        "chargingstationadministrativestatus",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
