"""Add live OCPP-reported connector status to charging_connectors (F-C2)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_charging_connector_status"
down_revision: str | None = "0009_anomaly_notification_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Labels are OCPP 2.0.1's ConnectorStatusEnumType *values* (e.g. "Available"),
# not the enum's Python member names - matches how ChargingConnectorStatus
# is declared with values_callable=enum_values in models.py, and diverges
# deliberately from charging_stations.maintenance_status, which stores
# member names instead.
_CONNECTOR_STATUS_ENUM = sa.Enum(
    "Available",
    "Occupied",
    "Reserved",
    "Unavailable",
    "Faulted",
    name="chargingconnectorstatus",
)


def upgrade() -> None:
    """Add status and status_updated_at, both nullable, no default.

    Nullable with no server_default is deliberate: a pre-provisioned
    connector that has never received a StatusNotification has no status
    to report, and a default of e.g. "Available" would falsely assert
    every existing connector is available as of this migration.
    """
    _CONNECTOR_STATUS_ENUM.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "charging_connectors",
        sa.Column(
            "status",
            sa.Enum(
                "Available",
                "Occupied",
                "Reserved",
                "Unavailable",
                "Faulted",
                name="chargingconnectorstatus",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "charging_connectors",
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the two status columns and the connector-status enum."""
    op.drop_column("charging_connectors", "status_updated_at")
    op.drop_column("charging_connectors", "status")
    _CONNECTOR_STATUS_ENUM.drop(op.get_bind(), checkfirst=True)
