"""Add device config-push tracking to telematics (F-J2)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_telematic_config_push"
down_revision: str | None = "0014_device_offline_alert"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add telemetry_interval_seconds and config_pushed_at, both nullable.

    Nullable with no default is deliberate, same rationale as ``0012``:
    NULL means "never pushed / device is on its firmware default". There
    is no MQTT ack topic for a config command (see mqtt-spec.md 2.3), so
    the only honest meaning available for these columns is "the last
    interval we successfully handed to the broker" - a default value
    would assert a push that never happened.
    """
    op.add_column(
        "telematics",
        sa.Column("telemetry_interval_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telematics",
        sa.Column(
            "config_pushed_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    """Drop the two config-push tracking columns."""
    op.drop_column("telematics", "config_pushed_at")
    op.drop_column("telematics", "telemetry_interval_seconds")
