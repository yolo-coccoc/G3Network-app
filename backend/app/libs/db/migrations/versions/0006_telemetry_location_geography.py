"""Unify vehicle_telemetry location storage onto PostGIS geography.

Closes future.md item 9: replaces the two plain latitude/longitude
DOUBLE PRECISION columns with a single geography(Point, 4326) column,
matching charging_stations.location (F-C1). No spatial index is created
here (unlike charging_stations) - vehicle_telemetry is a high-frequency
hypertable write path and nothing currently runs a spatial query against
it; add an index later if/when that changes.
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "0006_telemetry_location_geo"
down_revision: str | None = "0005_station_directory_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCATION_TYPE = geoalchemy2.Geography(
    geometry_type="POINT", srid=4326, spatial_index=False
)


def upgrade() -> None:
    """Add location, backfill it from latitude/longitude, then drop the old columns."""
    op.add_column(
        "vehicle_telemetry",
        sa.Column("location", _LOCATION_TYPE, nullable=True),
    )
    # Backfill existing rows (a no-op on an empty table). ST_MakePoint takes
    # (x, y) = (longitude, latitude); ::geography casts from geometry.
    op.execute(
        "UPDATE vehicle_telemetry SET location = "
        "ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography"
    )
    op.alter_column("vehicle_telemetry", "location", nullable=False)
    op.drop_column("vehicle_telemetry", "latitude")
    op.drop_column("vehicle_telemetry", "longitude")


def downgrade() -> None:
    """Restore latitude/longitude, backfill from location, then drop it."""
    op.add_column(
        "vehicle_telemetry",
        sa.Column("latitude", sa.Double(), nullable=True),
    )
    op.add_column(
        "vehicle_telemetry",
        sa.Column("longitude", sa.Double(), nullable=True),
    )
    op.execute(
        "UPDATE vehicle_telemetry SET "
        "latitude = ST_Y(location::geometry), "
        "longitude = ST_X(location::geometry)"
    )
    op.alter_column("vehicle_telemetry", "latitude", nullable=False)
    op.alter_column("vehicle_telemetry", "longitude", nullable=False)
    op.drop_column("vehicle_telemetry", "location")
