"""Create the notifications table (F-A2: backend/operator-facing alerts)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_notifications"
down_revision: str | None = "0007_telemetry_schema_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create notifications with its two inline enums and a vehicle_id index."""
    op.create_table(
        "notifications",
        sa.Column(
            "notification_id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "notification_type",
            sa.Enum("BATTERY_ALERT", name="notificationtype"),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("INFO", "WARNING", "CRITICAL", name="notificationseverity"),
            nullable=False,
        ),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("notification_id"),
    )
    op.create_index("ix_notifications_vehicle_id", "notifications", ["vehicle_id"])


def downgrade() -> None:
    """Drop notifications and its two enum types."""
    op.drop_index("ix_notifications_vehicle_id", table_name="notifications")
    op.drop_table("notifications")
    op.execute('DROP TYPE IF EXISTS "notificationtype"')
    op.execute('DROP TYPE IF EXISTS "notificationseverity"')
