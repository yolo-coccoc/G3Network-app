"""Drop the old business schema before rebuilding the MVP database.

This migration only serves the local initialization phase. It drops the
tables and enums created by the application's old migrations, but keeps the
system databases, the PostGIS/TimescaleDB extensions, and the
``alembic_version`` table.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_reset_application_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APPLICATION_TABLES = (
    "charging_station_status_events",
    "charging_session_meter_values",
    "charging_session_events",
    "charging_sessions",
    "charging_connectors",
    "charging_evses",
    "charging_stations",
    "vehicle_telemetry",
    "telematics",
    "vehicles",
)

_APPLICATION_ENUMS = (
    "vehiclestatus",
    "telematicstatus",
    "chargingstationadministrativestatus",
    "chargingstationconnectionstatus",
    "chargingevseadministrativestatus",
    "chargingconnectoradministrativestatus",
    "chargingtechnicalstatus",
    "chargingsessionstatus",
    "chargingsessioneventtype",
    "chargingreconciliationstatus",
    "chargingendreason",
    "chargingstate",
    "chargingstationsourceaction",
)


def upgrade() -> None:
    """Drop the old business schema to prepare for the new baseline."""
    # CASCADE is necessary because the old schema has foreign keys,
    # hypertables, and constraints that are no longer part of the new
    # contract. The table list is fixed in code.
    for table_name in _APPLICATION_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')

    # Drop enums after tables so no column still references the old type.
    for enum_name in _APPLICATION_ENUMS:
        op.execute(f'DROP TYPE IF EXISTS "{enum_name}" CASCADE')


def downgrade() -> None:
    """Do not restore the old schema after resetting the initial database."""
    # The reset is the starting point of the new graph; subsequent migrations
    # are responsible for creating and dropping the current schema per
    # bounded context.
