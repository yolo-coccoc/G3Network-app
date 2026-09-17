"""Create support_cases (F-I1, F-I2)."""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "0019_support_cases"
down_revision: str | None = "0018_drivers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the support_cases table.

    One table discriminated by ``case_type`` (``TICKET``/``SOS``) rather
    than two tables - the spec ties an SOS case (F-I2) and a ticket (F-I1)
    into one lifecycle (an SOS is "forwarded" onward and stays linked to
    its ticket), so splitting them would force inventing a sync mechanism
    whose only job is to undo the split.

    ``location`` uses ``spatial_index=False`` and gets no GIST index -
    unlike ``charging_stations.location``, this column is only ever an
    input snapshot at case-creation time, never a search target (F-I4's
    nearest-partner routing, which would justify one, is deferred).

    ``response_due_at`` gets a partial index (``WHERE first_responded_at
    IS NULL``) - this domain's use of the pattern first established by
    ``driver_vehicle_assignments``: only cases still awaiting a first
    response have a meaningful "is this breaching" query target.
    """
    op.create_table(
        "support_cases",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column(
            "case_type",
            sa.Enum("TICKET", "SOS", name="supportcasetype"),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum(
                "TECHNICAL",
                "BATTERY",
                "CHARGING",
                "BREAKDOWN",
                "ACCIDENT",
                "BILLING",
                "OTHER",
                name="supportcasecategory",
            ),
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("IN_APP", "ZALO", "HOTLINE", name="supportcasechannel"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "ACKNOWLEDGED",
                "RESOLVED",
                "CLOSED",
                "CANCELLED",
                name="supportcasestatus",
            ),
            nullable=False,
        ),
        sa.Column("vehicle_id", sa.UUID(), nullable=True),
        sa.Column("driver_id", sa.UUID(), nullable=True),
        sa.Column("vin", sa.String(length=17), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column(
            "location",
            geoalchemy2.Geography(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sla_response_minutes", sa.Integer(), nullable=False),
        sa.Column("response_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["driver_id"], ["drivers.driver_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_index("ix_support_cases_status", "support_cases", ["status"])
    op.create_index("ix_support_cases_vehicle_id", "support_cases", ["vehicle_id"])
    op.create_index("ix_support_cases_driver_id", "support_cases", ["driver_id"])
    op.create_index("ix_support_cases_deleted_at", "support_cases", ["deleted_at"])
    op.create_index(
        "ix_support_cases_status_created_at",
        "support_cases",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_support_cases_vehicle_created_at",
        "support_cases",
        ["vehicle_id", "created_at"],
    )
    op.create_index(
        "ix_support_cases_response_due_pending",
        "support_cases",
        ["response_due_at"],
        postgresql_where=sa.text("first_responded_at IS NULL"),
    )


def downgrade() -> None:
    """Drop the support_cases table and its enum types."""
    op.drop_table("support_cases")
    op.execute('DROP TYPE IF EXISTS "supportcasestatus"')
    op.execute('DROP TYPE IF EXISTS "supportcasechannel"')
    op.execute('DROP TYPE IF EXISTS "supportcasecategory"')
    op.execute('DROP TYPE IF EXISTS "supportcasetype"')
