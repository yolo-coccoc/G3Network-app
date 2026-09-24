"""Add append-only snapshots of a charger's configuration (OCPP GetConfiguration)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_charging_config_snapshots"
down_revision: str | None = "0025_charging_measurements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create charging_station_configuration_entries.

    After every boot the gateway asks the charger for its full configuration
    and stores one row per configuration key. All rows of one capture share a
    ``capture_id`` and ``captured_at``; a new capture is added on every boot
    and nothing is updated in place, so the history shows whether someone
    changed the charger's settings. ``value`` is nullable (a key can exist
    without a value) and ``is_readonly`` records which keys the charger refuses
    to change - a design constraint, not an error. The foreign key to
    ``charging_stations`` is ``RESTRICT``, like the other evidence tables.
    """
    op.create_table(
        "charging_station_configuration_entries",
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("capture_id", sa.UUID(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("is_readonly", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"], ["charging_stations.station_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("entry_id"),
    )
    op.create_index(
        "ix_charging_config_entries_station_captured",
        "charging_station_configuration_entries",
        ["station_id", "captured_at"],
    )


def downgrade() -> None:
    """Drop the configuration snapshots; the captured history is lost."""
    op.drop_table("charging_station_configuration_entries")
