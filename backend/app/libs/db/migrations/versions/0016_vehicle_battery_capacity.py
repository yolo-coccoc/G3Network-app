"""Add nominal battery capacity to vehicles (F-A6/F-C6)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_vehicle_battery_capacity"
down_revision: str | None = "0015_telematic_config_push"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add battery_capacity_kwh, nullable, no server default.

    Nullable with no default follows 0012's rationale: an existing
    vehicle genuinely has no recorded pack spec, and a server default
    would fabricate one for every row - making "nobody entered it"
    impossible to tell from "it really is that value." The F-A6/F-C6
    reports substitute a documented engineering default
    (telemetry.types.DEFAULT_BATTERY_CAPACITY_KWH) at read time instead,
    and flag in their response that they did so. No index: the column is
    never filtered or sorted on, only read alongside the row.
    """
    op.add_column(
        "vehicles",
        sa.Column("battery_capacity_kwh", sa.Double(), nullable=True),
    )


def downgrade() -> None:
    """Drop the battery capacity column."""
    op.drop_column("vehicles", "battery_capacity_kwh")
