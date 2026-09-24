"""Widen the connector status enum and add error/charger-level status columns."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_charging_status_details"
down_revision: str | None = "0022_charging_station_device"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Labels are the enum *values*, like 0010: OCPP 2.0.1's five statuses plus the
# five OCPP 1.6J statuses that have no 2.0.1 equivalent. "Occupied" stays
# because the 2.0.1 gateway still writes it.
_ENUM_NAME = "chargingconnectorstatus"
_OLD_VALUES = ("Available", "Occupied", "Reserved", "Unavailable", "Faulted")
_NEW_VALUES = (
    "Preparing",
    "Charging",
    "SuspendedEV",
    "SuspendedEVSE",
    "Finishing",
)
_ALL_VALUES = _OLD_VALUES + _NEW_VALUES


def upgrade() -> None:
    """Add the five 1.6J statuses and the error/charger-level columns.

    PostgreSQL can add enum values but never drop them, and ``ALTER TYPE …
    ADD VALUE`` cannot run inside a transaction block, so it runs in
    Alembic's ``autocommit_block``.

    New columns are all nullable with no server default (``0012``/``0017``
    rationale): a row that has never received a status genuinely has none.
    ``charging_stations.charger_*`` describes connector ``0`` of OCPP 1.6J, the
    whole charger, which has no topology row (decision D3 of the OCPP 1.6J
    planner).
    """
    with op.get_context().autocommit_block():
        for value in _NEW_VALUES:
            op.execute(f"ALTER TYPE {_ENUM_NAME} ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column(
        "charging_connectors",
        sa.Column("error_code", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "charging_connectors",
        sa.Column("vendor_error_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "charging_connectors",
        sa.Column("status_info", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "charging_stations",
        sa.Column(
            "charger_status",
            postgresql.ENUM(*_ALL_VALUES, name=_ENUM_NAME, create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "charging_stations",
        sa.Column("charger_status_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "charging_stations",
        sa.Column("charger_error_code", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "charging_stations",
        sa.Column("charger_vendor_error_code", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    """Drop the new columns and shrink the status enum back to five values.

    PostgreSQL cannot remove enum values, so the type is rebuilt: connector
    rows holding a 1.6J-only status are first converted to ``Occupied`` (the
    2.0.1 equivalent for every one of them), the old type is renamed, a new
    five-value type is created, the column is cast over, and the old type is
    dropped. The charger-level station columns, and with them their 1.6J
    statuses, are discarded.
    """
    for name in (
        "charger_vendor_error_code",
        "charger_error_code",
        "charger_status_updated_at",
        "charger_status",
    ):
        op.drop_column("charging_stations", name)
    for name in ("status_info", "vendor_error_code", "error_code"):
        op.drop_column("charging_connectors", name)

    new_values = ", ".join(f"'{value}'" for value in _NEW_VALUES)
    op.execute(
        f"UPDATE charging_connectors SET status = 'Occupied' "
        f"WHERE status IN ({new_values})"
    )
    op.execute(f"ALTER TYPE {_ENUM_NAME} RENAME TO {_ENUM_NAME}_old")
    postgresql.ENUM(*_OLD_VALUES, name=_ENUM_NAME).create(op.get_bind())
    op.execute(
        f"ALTER TABLE charging_connectors ALTER COLUMN status "
        f"TYPE {_ENUM_NAME} USING status::text::{_ENUM_NAME}"
    )
    op.execute(f"DROP TYPE {_ENUM_NAME}_old")
