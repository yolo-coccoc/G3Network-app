"""Bổ sung alert telemetry và tọa độ station cho các API monitoring MVP."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_create_monitoring_api_schema"
down_revision: str | None = "0004_create_charging_mvp_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Tạo bảng cảnh báo và thêm tọa độ station nullable."""
    op.add_column(
        "charging_stations",
        sa.Column("latitude", sa.Double(), nullable=True),
    )
    op.add_column(
        "charging_stations",
        sa.Column("longitude", sa.Double(), nullable=True),
    )

    op.create_table(
        "telemetry_alerts",
        sa.Column("alert_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column(
            "alert_type",
            sa.Enum(
                "battery_low",
                "battery_anomaly",
                name="telemetryalerttype",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("open", "resolved", name="telemetryalertstatus"),
            nullable=False,
        ),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("alert_id"),
    )
    op.create_index(
        "ix_telemetry_alerts_vehicle_time",
        "telemetry_alerts",
        ["vehicle_id", "triggered_at"],
    )
    op.create_index(
        "uq_telemetry_alerts_open",
        "telemetry_alerts",
        ["vehicle_id", "alert_type"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    """Xóa alert telemetry và tọa độ station."""
    op.drop_index("uq_telemetry_alerts_open", table_name="telemetry_alerts")
    op.drop_index("ix_telemetry_alerts_vehicle_time", table_name="telemetry_alerts")
    op.drop_table("telemetry_alerts")
    op.execute('DROP TYPE IF EXISTS "telemetryalertstatus"')
    op.execute('DROP TYPE IF EXISTS "telemetryalerttype"')
    op.drop_column("charging_stations", "longitude")
    op.drop_column("charging_stations", "latitude")
