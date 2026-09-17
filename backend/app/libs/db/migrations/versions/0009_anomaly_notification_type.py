"""Add ANOMALY_ALERT to the notificationtype enum (F-A4).

``notification_type`` is a native PostgreSQL enum created inline by
``0008_notifications``, so a new member needs explicit DDL rather than a
model change alone.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_anomaly_notification_type"
down_revision: str | None = "0008_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the ANOMALY_ALERT member to the notificationtype enum.

    ``ALTER TYPE ... ADD VALUE`` is transaction-safe on PostgreSQL 12+ (this
    repo targets 16) as long as the new value is not read/written within the
    same transaction that adds it - this migration only adds the label and
    does nothing else, so that restriction does not apply here.
    """
    op.execute("ALTER TYPE \"notificationtype\" ADD VALUE 'ANOMALY_ALERT'")


def downgrade() -> None:
    """Recreate notificationtype without ANOMALY_ALERT.

    PostgreSQL has no ``DROP VALUE`` for an enum, so the type must be
    recreated: rename the old type out of the way, create a new one with
    only the original member, repoint the column, then drop the old type.

    Side Effects:
        Destructive - any row with ``notification_type = 'ANOMALY_ALERT'``
        (i.e. any F-A4 anomaly notification) is deleted first, since the new
        type has no member to cast it to. This is only intended to be run
        while rolling back a not-yet-released migration, not against a
        database holding real anomaly notifications to keep.
    """
    op.execute("DELETE FROM notifications WHERE notification_type = 'ANOMALY_ALERT'")
    op.execute('ALTER TYPE "notificationtype" RENAME TO "notificationtype_old"')
    op.execute("CREATE TYPE \"notificationtype\" AS ENUM ('BATTERY_ALERT')")
    op.execute(
        "ALTER TABLE notifications "
        'ALTER COLUMN notification_type TYPE "notificationtype" '
        'USING notification_type::text::"notificationtype"'
    )
    op.execute('DROP TYPE "notificationtype_old"')
