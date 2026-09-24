"""Add charger device info and liveness columns to charging_stations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_charging_station_device"
down_revision: str | None = "0021_charging_ocpp_raw_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STRING_COLUMNS = (
    ("ocpp_protocol_version", 20),
    ("vendor", 100),
    ("model", 100),
    ("serial_number", 100),
    ("firmware_version", 100),
)
_TIMESTAMP_COLUMNS = ("last_boot_at", "last_seen_at")


def upgrade() -> None:
    """Add seven nullable columns describing the physical charger.

    ``vendor``, ``model``, ``serial_number`` and ``firmware_version`` come from
    OCPP ``BootNotification`` (the firmware value is the baseline used to
    notice a firmware swap); ``ocpp_protocol_version`` is the subprotocol of
    the connection; ``last_boot_at`` is the last accepted boot and
    ``last_seen_at`` the last frame of any kind received from the charger,
    from which "online" is derived at read time.

    All columns are nullable with no server default, following ``0012``/
    ``0017``: an existing station that has never connected genuinely has no
    value, and inventing one (for example ``last_seen_at = now()``) would
    falsely claim the charger is alive.
    """
    for name, length in _STRING_COLUMNS:
        op.add_column(
            "charging_stations",
            sa.Column(name, sa.String(length=length), nullable=True),
        )
    for name in _TIMESTAMP_COLUMNS:
        op.add_column(
            "charging_stations",
            sa.Column(name, sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    """Drop the seven device/liveness columns; their values are lost."""
    for name in reversed(_TIMESTAMP_COLUMNS):
        op.drop_column("charging_stations", name)
    for name, _ in reversed(_STRING_COLUMNS):
        op.drop_column("charging_stations", name)
