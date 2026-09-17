"""Create fleets, fleet_vehicle_memberships, and drop vehicles.fleet_id (F-E1)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_fleet"
down_revision: str | None = "0019_support_cases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the fleet domain's two tables and drop the dead vehicles.fleet_id column.

    ``fleets``/``fleet_vehicle_memberships`` follow the same open/close
    history-table shape as ``drivers``/``driver_vehicle_assignments``, with
    one structural difference: only ``vehicle_id`` gets a partial unique
    index (one active fleet per vehicle) - a fleet legitimately holds many
    vehicles at once, so ``fleet_id`` does not.

    ``vehicles.fleet_id`` (added in ``0002``) was always a dead
    ``String(36)`` column with no FK, no index, and no reader anywhere in
    the codebase - future.md item 10 planned to convert it to a UUID FK,
    but that plan predates this fleet-owned-membership-table pattern
    (established by ``drivers``) and is superseded by it here. No
    backfill: existing values are opaque non-UUID strings with no
    ``fleets`` row to point at.
    """
    op.create_table(
        "fleets",
        sa.Column("fleet_id", sa.UUID(), nullable=False),
        sa.Column("fleet_code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "INACTIVE", name="fleetstatus"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("fleet_id"),
    )
    op.create_index("ix_fleets_fleet_code", "fleets", ["fleet_code"], unique=True)
    op.create_index("ix_fleets_status", "fleets", ["status"])
    op.create_index("ix_fleets_deleted_at", "fleets", ["deleted_at"])

    op.create_table(
        "fleet_vehicle_memberships",
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("fleet_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["fleet_id"], ["fleets.fleet_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("membership_id"),
    )
    op.create_index(
        "ix_fleet_vehicle_memberships_fleet_id",
        "fleet_vehicle_memberships",
        ["fleet_id"],
    )
    op.create_index(
        "ix_fleet_vehicle_memberships_vehicle_id",
        "fleet_vehicle_memberships",
        ["vehicle_id"],
    )
    op.create_index(
        "ix_fleet_vehicle_memberships_fleet_time",
        "fleet_vehicle_memberships",
        ["fleet_id", "joined_at"],
    )
    op.create_index(
        "uq_fleet_vehicle_memberships_active_vehicle",
        "fleet_vehicle_memberships",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
    )

    op.drop_column("vehicles", "fleet_id")


def downgrade() -> None:
    """Drop both fleet tables/enum and re-add vehicles.fleet_id (data not restored)."""
    op.add_column(
        "vehicles",
        sa.Column("fleet_id", sa.String(length=36), nullable=True),
    )
    op.drop_table("fleet_vehicle_memberships")
    op.drop_table("fleets")
    op.execute('DROP TYPE IF EXISTS "fleetstatus"')
