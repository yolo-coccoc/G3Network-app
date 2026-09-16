"""Add F-C1 directory/descriptive metadata to charging_stations."""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "0005_station_directory_fields"
down_revision: str | None = "0004_create_charging_mvp_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAINTENANCE_STATUS_ENUM = sa.Enum(
    "OPERATIONAL",
    "UNDER_MAINTENANCE",
    "OUT_OF_SERVICE",
    name="chargingstationmaintenancestatus",
)


def upgrade() -> None:
    """Add location, power rating, connector standard, hours, and maintenance status."""
    # Unlike op.create_table, op.add_column does not auto-create an inline
    # sa.Enum's PostgreSQL type - it must be created explicitly first, then
    # referenced with create_type=False so add_column doesn't try again.
    _MAINTENANCE_STATUS_ENUM.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "charging_stations",
        sa.Column(
            "location",
            # spatial_index=False: the GIST index is created explicitly
            # below instead of relying on GeoAlchemy2's automatic DDL hook,
            # which would otherwise create a second, redundant index.
            geoalchemy2.Geography(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "charging_stations",
        sa.Column("power_rating_kw", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "charging_stations",
        sa.Column("connector_standard", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "charging_stations",
        sa.Column("operating_hours", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "charging_stations",
        sa.Column(
            "maintenance_status",
            sa.Enum(
                "OPERATIONAL",
                "UNDER_MAINTENANCE",
                "OUT_OF_SERVICE",
                name="chargingstationmaintenancestatus",
                create_type=False,
            ),
            nullable=False,
            server_default="OPERATIONAL",
        ),
    )
    op.create_index(
        "ix_charging_stations_location",
        "charging_stations",
        ["location"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    """Drop the F-C1 directory columns, their index, and the maintenance-status enum."""
    op.drop_index("ix_charging_stations_location", table_name="charging_stations")
    op.drop_column("charging_stations", "maintenance_status")
    op.drop_column("charging_stations", "operating_hours")
    op.drop_column("charging_stations", "connector_standard")
    op.drop_column("charging_stations", "power_rating_kw")
    op.drop_column("charging_stations", "location")
    _MAINTENANCE_STATUS_ENUM.drop(op.get_bind(), checkfirst=True)
