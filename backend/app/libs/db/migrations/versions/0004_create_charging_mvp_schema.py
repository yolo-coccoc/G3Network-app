"""Tạo sáu bảng charging active và hai hypertable history của MVP."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_create_charging_mvp_schema"
down_revision: str | None = "0003_create_vehicle_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Tạo topology, session aggregate và hai bảng history phiên sạc."""
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    _create_topology_tables()
    _create_session_tables()

    # Chỉ event và meter sample là dữ liệu tăng liên tục theo thời gian.
    # Aggregate charging_sessions vẫn là bảng quan hệ thông thường.
    for table_name, time_column in (
        ("charging_session_events", "event_occurred_at"),
        ("charging_session_meter_values", "sampled_at"),
    ):
        op.execute(
            "SELECT create_hypertable("
            f"'{table_name}', '{time_column}', "
            "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
        )


def downgrade() -> None:
    """Xóa sáu bảng charging active và hai enum lifecycle."""
    op.drop_table("charging_session_meter_values")
    op.drop_table("charging_session_events")
    op.drop_table("charging_sessions")
    op.drop_table("charging_connectors")
    op.drop_table("charging_evses")
    op.drop_table("charging_stations")
    op.execute('DROP TYPE IF EXISTS "chargingsessioneventtype"')
    op.execute('DROP TYPE IF EXISTS "chargingsessionstatus"')


def _create_topology_tables() -> None:
    """Tạo station, EVSE và connector theo thứ tự foreign key."""
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
            ["station_id"], ["charging_stations.station_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("evse_id"),
        sa.UniqueConstraint(
            "station_id", "ocpp_evse_id", name="uq_charging_evses_station_ocpp_id"
        ),
    )
    op.create_index(
        "ix_charging_evses_station_deleted", "charging_evses", ["station_id", "deleted_at"]
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


def _create_session_tables() -> None:
    """Tạo aggregate session, event history và meter history."""
    op.create_table(
        "charging_sessions",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("evse_id", sa.UUID(), nullable=False),
        sa.Column("connector_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_transaction_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "completed", name="chargingsessionstatus"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meter_start_wh", sa.Numeric(24, 3), nullable=True),
        sa.Column("meter_end_wh", sa.Numeric(24, 3), nullable=True),
        sa.Column("energy_delivered_wh", sa.Numeric(24, 3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"], ["charging_stations.station_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["evse_id"], ["charging_evses.evse_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"], ["charging_connectors.connector_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint(
            "station_id",
            "ocpp_transaction_id",
            name="uq_charging_sessions_station_transaction",
        ),
    )
    op.create_index(
        "ix_charging_sessions_status_updated",
        "charging_sessions",
        ["status", "updated_at"],
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
    op.create_index("ix_charging_sessions_started_at", "charging_sessions", ["started_at"])
    op.create_index("ix_charging_sessions_ended_at", "charging_sessions", ["ended_at"])

    op.create_table(
        "charging_session_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("event_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum("Started", "Updated", "Ended", name="chargingsessioneventtype"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["charging_sessions.session_id"], ondelete="RESTRICT"
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
        sa.Column("value_wh", sa.Numeric(24, 3), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["charging_sessions.session_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("meter_value_id", "sampled_at"),
    )
    op.create_index(
        "ix_charging_meter_session_sampled",
        "charging_session_meter_values",
        ["session_id", "sampled_at", "meter_value_id"],
    )
