"""Add idTag, stop reason and meterStop to charging_sessions, plus a 1.6J transaction-ID sequence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_charging_session_fields"
down_revision: str | None = "0023_charging_status_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEQUENCE_NAME = "charging_ocpp16_transaction_id_seq"


def upgrade() -> None:
    """Add three nullable session columns and the OCPP 1.6J transaction-ID sequence.

    ``id_tag`` (the RFID/idTag that started the session, at most 20
    characters in OCPP 1.6), ``stop_reason`` and ``meter_stop_wh`` (the
    charger's authoritative closing meter reading, kept separately from
    ``meter_end_wh``, which follows the latest sample) are nullable with no
    server default: an existing session, and any 2.0.1 session, genuinely has
    no such value (``0012``/``0017`` rationale).

    In OCPP 1.6J the *backend* assigns the integer ``transactionId``, so the
    sequence is limited to the 32-bit range the protocol allows and never
    cycles. A sequence is not transactional: a rolled-back transaction leaves a
    gap in the numbers, which is harmless.
    """
    op.add_column(
        "charging_sessions",
        sa.Column("id_tag", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "charging_sessions",
        sa.Column("stop_reason", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "charging_sessions",
        sa.Column("meter_stop_wh", sa.Numeric(24, 3), nullable=True),
    )
    op.execute(
        f"CREATE SEQUENCE {_SEQUENCE_NAME} AS integer "
        "START WITH 1 INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 NO CYCLE"
    )


def downgrade() -> None:
    """Drop the sequence and the three columns; their values are lost."""
    op.execute(f"DROP SEQUENCE IF EXISTS {_SEQUENCE_NAME}")
    op.drop_column("charging_sessions", "meter_stop_wh")
    op.drop_column("charging_sessions", "stop_reason")
    op.drop_column("charging_sessions", "id_tag")
