"""Xóa metadata last_seen_at khỏi bảng thiết bị Telematic."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "5e7b1c9d2a44"
down_revision: str | None = "3a8d4c7e1b2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Xóa index và cột last_seen_at khỏi telematics."""
    op.drop_column("telematics", "last_seen_at")


def downgrade() -> None:
    """Khôi phục cột last_seen_at khi rollback migration."""
    op.add_column("telematics", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
