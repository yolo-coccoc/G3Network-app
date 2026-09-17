"""Add battery State of Health and cycle count to vehicle_telemetry (F-A3)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_telemetry_battery_health"
down_revision: str | None = "0011_vehicle_activation_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add soh_percent and cycle_count, both nullable, no default.

    Nullable with no default is deliberate: a historical telemetry row
    genuinely has no SOH/cycle-count value - the device contract predates
    this field, the same way schema_version's addition handled devices
    that predate it (defaulting a version, not a physical measurement).
    """
    op.add_column(
        "vehicle_telemetry",
        sa.Column("soh_percent", sa.Double(), nullable=True),
    )
    op.add_column(
        "vehicle_telemetry",
        sa.Column("cycle_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop the two battery-health columns."""
    op.drop_column("vehicle_telemetry", "cycle_count")
    op.drop_column("vehicle_telemetry", "soh_percent")
