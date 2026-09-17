"""Create drivers and driver_vehicle_assignments (F-E4)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_drivers"
down_revision: str | None = "0017_charging_ingest_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the drivers domain's two tables.

    ``drivers`` follows the same shape as ``vehicles``/``telematics``:
    UUID PK, two unique natural-key columns (``phone_number``,
    ``license_number``), a status enum, and soft delete via ``deleted_at``.

    ``driver_vehicle_assignments`` is a genuine history table, not a
    single current-vehicle column like ``telematics.vehicle_id`` - a row
    is opened (``assigned_at``) and later closed (``unassigned_at``)
    rather than deleted or overwritten. Two **partial** unique indexes
    (``WHERE unassigned_at IS NULL``) enforce "at most one open assignment
    per vehicle" and "at most one open assignment per driver" without
    blocking on closed history - this is this backend's first use of a
    partial index. ``ondelete=RESTRICT`` on both FKs, matching
    ``charging_sessions``: a driver/vehicle is only ever soft-deleted in
    this backend, so a hard delete silently orphaning this history should
    never happen.
    """
    op.create_table(
        "drivers",
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column("license_number", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "INACTIVE", name="driverstatus"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("driver_id"),
    )
    op.create_index(
        "ix_drivers_phone_number", "drivers", ["phone_number"], unique=True
    )
    op.create_index(
        "ix_drivers_license_number", "drivers", ["license_number"], unique=True
    )
    op.create_index("ix_drivers_status", "drivers", ["status"])
    op.create_index("ix_drivers_deleted_at", "drivers", ["deleted_at"])

    op.create_table(
        "driver_vehicle_assignments",
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("driver_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["driver_id"], ["drivers.driver_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("assignment_id"),
    )
    op.create_index(
        "ix_driver_vehicle_assignments_driver_id",
        "driver_vehicle_assignments",
        ["driver_id"],
    )
    op.create_index(
        "ix_driver_vehicle_assignments_vehicle_id",
        "driver_vehicle_assignments",
        ["vehicle_id"],
    )
    op.create_index(
        "ix_driver_vehicle_assignments_driver_time",
        "driver_vehicle_assignments",
        ["driver_id", "assigned_at"],
    )
    op.create_index(
        "uq_driver_vehicle_assignments_active_vehicle",
        "driver_vehicle_assignments",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )
    op.create_index(
        "uq_driver_vehicle_assignments_active_driver",
        "driver_vehicle_assignments",
        ["driver_id"],
        unique=True,
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )


def downgrade() -> None:
    """Drop both tables (assignments first, FK order) and the enum type."""
    op.drop_table("driver_vehicle_assignments")
    op.drop_table("drivers")
    op.execute('DROP TYPE IF EXISTS "driverstatus"')
