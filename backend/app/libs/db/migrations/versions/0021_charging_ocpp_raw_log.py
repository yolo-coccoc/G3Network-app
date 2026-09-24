"""Create the verbatim OCPP message log as a TimescaleDB hypertable."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_charging_ocpp_raw_log"
down_revision: str | None = "0020_fleet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    """Create charging_ocpp_messages and turn it into a hypertable.

    Every OCPP frame exchanged with a station is stored as exact text so a
    real charger's behaviour (and any billing dispute) can be investigated
    from the original frames. The table is append-only: there is no update
    path and rows are never rewritten.

    The primary key is ``(message_id, occurred_at)`` because TimescaleDB
    requires the partitioning column in every unique key, and ``message_id``
    keeps rows unique when frames share a timestamp. The foreign key to
    ``charging_stations`` is ``RESTRICT``, like the session tables: a station
    that has evidence attached cannot be hard-deleted. One index serves the
    only planned read, "all frames of one station in time order".
    """
    op.create_table(
        "charging_ocpp_messages",
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("station_id", sa.UUID(), nullable=False),
        sa.Column("ocpp_subprotocol", sa.String(length=20), nullable=False),
        # Labels are the enum *values*, matching ChargingOcppMessageModel's
        # values_callable. create_table creates the type itself, like the
        # enums in 0004/0018/0019.
        sa.Column(
            "direction",
            sa.Enum("CP_TO_CSMS", "CSMS_TO_CP", name="chargingocppmessagedirection"),
            nullable=False,
        ),
        sa.Column("raw_frame", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"], ["charging_stations.station_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("message_id", "occurred_at"),
    )
    op.create_index(
        "ix_charging_ocpp_messages_station_time",
        "charging_ocpp_messages",
        ["station_id", "occurred_at"],
    )
    op.execute(
        "SELECT create_hypertable("
        "'charging_ocpp_messages', 'occurred_at', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    """Drop the raw message log and its direction enum.

    The stored frames are lost; this is only meant for development databases.
    """
    op.drop_table("charging_ocpp_messages")
    op.execute('DROP TYPE IF EXISTS "chargingocppmessagedirection"')
