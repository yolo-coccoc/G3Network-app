"""Replace charging_session_meter_values with the unified charging_session_measurements."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_charging_measurements"
down_revision: str | None = "0024_charging_session_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENERGY_MEASURAND = "Energy.Active.Import.Register"


def upgrade() -> None:
    """Create the unified measurements hypertable, copy every energy row, drop the old table.

    ``charging_session_measurements`` holds every measurand of a session (the
    energy register and, later, SoC, power, voltage, current, temperature…), for
    both OCPP protocols. This migration only moves the existing energy samples:
    each row of ``charging_session_meter_values`` becomes a measurement with
    ``measurand = 'Energy.Active.Import.Register'``, ``unit = 'Wh'`` and the
    same ID, time and value, so no history is lost. The old table is then
    dropped.

    ``(measurement_id, sampled_at)`` is the primary key because TimescaleDB
    requires the partitioning column in every unique key. The foreign key to
    ``charging_sessions`` is ``RESTRICT`` like the tables it replaces. The value
    keeps six decimals because non-energy measurands (for example a voltage)
    are not whole numbers.
    """
    op.create_table(
        "charging_session_measurements",
        sa.Column("measurement_id", sa.UUID(), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("measurand", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("context", sa.String(length=30), nullable=True),
        sa.Column("phase", sa.String(length=10), nullable=True),
        sa.Column("location", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["charging_sessions.session_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("measurement_id", "sampled_at"),
    )
    op.create_index(
        "ix_charging_measurements_session_measurand_time",
        "charging_session_measurements",
        ["session_id", "measurand", "sampled_at"],
    )
    op.execute(
        "SELECT create_hypertable("
        "'charging_session_measurements', 'sampled_at', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
    )
    op.execute(
        "INSERT INTO charging_session_measurements "
        "(measurement_id, sampled_at, session_id, measurand, value, unit) "
        f"SELECT meter_value_id, sampled_at, session_id, '{_ENERGY_MEASURAND}', "
        "value_wh, 'Wh' FROM charging_session_meter_values"
    )
    op.drop_table("charging_session_meter_values")


def downgrade() -> None:
    """Recreate the old energy table, copy the energy rows back, drop the new table.

    Only rows with ``measurand = 'Energy.Active.Import.Register'`` fit the old
    table, so **every other measurement is lost** on downgrade (for example
    SoC or power stored by the OCPP 1.6J path). This is only meant for
    development databases.
    """
    op.create_table(
        "charging_session_meter_values",
        sa.Column("meter_value_id", sa.UUID(), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("value_wh", sa.Numeric(24, 3), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["charging_sessions.session_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("meter_value_id", "sampled_at"),
    )
    op.create_index(
        "ix_charging_meter_session_sampled",
        "charging_session_meter_values",
        ["session_id", "sampled_at", "meter_value_id"],
    )
    op.execute(
        "SELECT create_hypertable("
        "'charging_session_meter_values', 'sampled_at', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
    )
    op.execute(
        "INSERT INTO charging_session_meter_values "
        "(meter_value_id, sampled_at, session_id, value_wh) "
        "SELECT measurement_id, sampled_at, session_id, value "
        "FROM charging_session_measurements "
        f"WHERE measurand = '{_ENERGY_MEASURAND}'"
    )
    op.drop_table("charging_session_measurements")
