"""Add the F-F2 device-activation state machine to vehicles."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_vehicle_activation_status"
down_revision: str | None = "0010_charging_connector_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVATION_STATUS_ENUM = sa.Enum(
    "PENDING",
    "DEVICE_ASSIGNED",
    "ACTIVATED",
    name="vehicleactivationstatus",
)


def upgrade() -> None:
    """Add activation_status, defaulting existing vehicles to PENDING.

    A server_default of PENDING is honest here (unlike F-C2's nullable
    connector status): every vehicle that predates this migration genuinely
    never went through the new provisioning flow, so PENDING is the
    correct value for them, not a guess.
    """
    _ACTIVATION_STATUS_ENUM.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "vehicles",
        sa.Column(
            "activation_status",
            sa.Enum(
                "PENDING",
                "DEVICE_ASSIGNED",
                "ACTIVATED",
                name="vehicleactivationstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
    )


def downgrade() -> None:
    """Drop activation_status and its enum type."""
    op.drop_column("vehicles", "activation_status")
    _ACTIVATION_STATUS_ENUM.drop(op.get_bind(), checkfirst=True)
