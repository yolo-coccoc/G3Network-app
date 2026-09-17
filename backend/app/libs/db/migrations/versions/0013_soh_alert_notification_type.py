"""Add SOH_ALERT to the notificationtype enum (F-A3)."""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_soh_alert_notification_type"
down_revision: str | None = "0012_telemetry_battery_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the SOH_ALERT member to the notificationtype enum.

    ``ALTER TYPE ... ADD VALUE`` is transaction-safe here for the same
    reason as ``0009_anomaly_notification_type``: the new value is never
    read/written within this same migration.
    """
    op.execute("ALTER TYPE \"notificationtype\" ADD VALUE 'SOH_ALERT'")


def downgrade() -> None:
    """Recreate notificationtype without SOH_ALERT.

    Same rename/recreate/repoint/drop idiom as ``0009``'s downgrade, this
    time restoring the two members that existed before this migration
    (``BATTERY_ALERT``, ``ANOMALY_ALERT``).

    Side Effects:
        Destructive - any row with ``notification_type = 'SOH_ALERT'`` is
        deleted first, since the restored type has no member to cast it
        to. Only intended for rolling back a not-yet-released migration.
    """
    op.execute("DELETE FROM notifications WHERE notification_type = 'SOH_ALERT'")
    op.execute('ALTER TYPE "notificationtype" RENAME TO "notificationtype_old"')
    op.execute(
        "CREATE TYPE \"notificationtype\" AS ENUM ('BATTERY_ALERT', 'ANOMALY_ALERT')"
    )
    op.execute(
        "ALTER TABLE notifications "
        'ALTER COLUMN notification_type TYPE "notificationtype" '
        'USING notification_type::text::"notificationtype"'
    )
    op.execute('DROP TYPE "notificationtype_old"')
