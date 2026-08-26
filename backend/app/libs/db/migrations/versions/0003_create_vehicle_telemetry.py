"""Tạo hypertable vehicle_telemetry cho dữ liệu chuỗi thời gian của xe."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_create_vehicle_telemetry"
down_revision: str | None = "0002_create_vehicles_and_telematics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Tạo bảng telemetry, index truy vấn và hypertable TimescaleDB."""
    op.create_table(
        "vehicle_telemetry",
        sa.Column("message_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("message_uuid", sa.UUID(), nullable=False),
        sa.Column("telematic_id", sa.UUID(), nullable=False),
        sa.Column("telematic_serial", sa.String(length=50), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Double(), nullable=False),
        sa.Column("longitude", sa.Double(), nullable=False),
        sa.Column("speed", sa.Double(), nullable=True),
        sa.Column("heading", sa.Double(), nullable=True),
        sa.Column("soc", sa.Double(), nullable=False),
        sa.Column("battery_voltage", sa.Double(), nullable=True),
        sa.Column("battery_current", sa.Double(), nullable=True),
        sa.Column("battery_temperature", sa.Double(), nullable=True),
        sa.Column("motor_temperature", sa.Double(), nullable=True),
        sa.Column("odometer", sa.Double(), nullable=True),
        sa.Column("signal_strength", sa.BigInteger(), nullable=True),
        sa.Column("error_codes", postgresql.JSONB(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["telematic_id"], ["telematics.telematic_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("message_id", "recorded_at"),
        sa.UniqueConstraint(
            "telematic_id", "recorded_at", name="uq_telematic_recorded_at"
        ),
    )
    op.create_index(
        "ix_vehicle_telemetry_message_uuid",
        "vehicle_telemetry",
        ["message_uuid"],
    )
    op.create_index(
        "ix_vehicle_telemetry_vehicle_time",
        "vehicle_telemetry",
        ["vehicle_id", sa.literal_column("recorded_at DESC")],
    )
    op.execute(
        "SELECT create_hypertable('vehicle_telemetry', 'recorded_at', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    """Xóa hypertable telemetry và các index của nó."""
    op.drop_table("vehicle_telemetry")
