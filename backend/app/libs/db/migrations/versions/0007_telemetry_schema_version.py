"""Add schema_version to vehicle_telemetry (F-A1: "schema is versioned")."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_telemetry_schema_version"
down_revision: str | None = "0006_telemetry_location_geo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add schema_version, defaulting existing/new rows to 1."""
    op.add_column(
        "vehicle_telemetry",
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    """Drop schema_version."""
    op.drop_column("vehicle_telemetry", "schema_version")
