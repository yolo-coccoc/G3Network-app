"""Add seq_no and meter_end_sampled_at for charging-ingest correctness (F-B2)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_charging_ingest_fields"
down_revision: str | None = "0016_vehicle_battery_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add charging_session_events.seq_no and charging_sessions.meter_end_sampled_at.

    Both nullable, no server default, following ``0012``'s rationale: a
    row written before these columns existed genuinely has no truthful
    value - backfilling ``seq_no`` with 0 would collide with a real OCPP
    ``seqNo`` of 0, and there is no way to reconstruct a historical
    meter reading's true sample time. ``charging_session_events`` is a
    TimescaleDB hypertable; a nullable column with no default is a
    catalog-only change Timescale propagates without a rewrite (same as
    ``0012`` on the ``vehicle_telemetry`` hypertable).

    No unique constraint on ``seq_no``: enforcing dedup on it is the
    deferred reliability path (``future.md`` item 27), not this
    migration's job - adding the index now would optimize a query that
    doesn't exist yet.
    """
    op.add_column(
        "charging_session_events",
        sa.Column("seq_no", sa.Integer(), nullable=True),
    )
    op.add_column(
        "charging_sessions",
        sa.Column(
            "meter_end_sampled_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    """Drop the two correctness-tracking columns."""
    op.drop_column("charging_sessions", "meter_end_sampled_at")
    op.drop_column("charging_session_events", "seq_no")
