"""Tạo bảng vehicles và telematics của baseline backend."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_vehicles_telematics"
down_revision: str | None = "0001_reset_application_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Tạo bảng xe trước rồi tạo bảng thiết bị có foreign key tới xe."""
    op.create_table(
        "vehicles",
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("license_plate", sa.String(length=20), nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("make", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=50), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "INACTIVE",
                "MAINTENANCE",
                "DECOMMISSIONED",
                name="vehiclestatus",
            ),
            nullable=False,
        ),
        sa.Column("fleet_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("vehicle_id"),
    )
    op.create_index("ix_vehicles_license_plate", "vehicles", ["license_plate"], unique=True)
    op.create_index("ix_vehicles_vin", "vehicles", ["vin"], unique=True)
    op.create_index("ix_vehicles_status", "vehicles", ["status"])

    op.create_table(
        "telematics",
        sa.Column("telematic_id", sa.UUID(), nullable=False),
        sa.Column("telematic_serial", sa.String(length=50), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "INACTIVE", "MAINTENANCE", name="telematicstatus"),
            nullable=False,
        ),
        sa.Column("firmware_version", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("telematic_id"),
        sa.UniqueConstraint("vehicle_id", name="uq_telematics_vehicle_id"),
    )
    op.create_index(
        "ix_telematics_telematic_serial", "telematics", ["telematic_serial"], unique=True
    )
    op.create_index("ix_telematics_vehicle_id", "telematics", ["vehicle_id"])
    op.create_index("ix_telematics_status", "telematics", ["status"])
    op.create_index("ix_telematics_deleted_at", "telematics", ["deleted_at"])


def downgrade() -> None:
    """Xóa telematics, vehicles và hai enum của baseline."""
    op.drop_table("telematics")
    op.drop_table("vehicles")
    op.execute('DROP TYPE IF EXISTS "telematicstatus"')
    op.execute('DROP TYPE IF EXISTS "vehiclestatus"')
