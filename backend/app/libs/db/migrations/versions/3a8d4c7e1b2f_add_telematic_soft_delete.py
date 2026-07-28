"""Thêm deleted_at cho soft delete thiết bị Telematic."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3a8d4c7e1b2f"
down_revision: str | None = "c0f4a8b6e2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Thêm cột nullable để đánh dấu xoá mềm."""
    op.add_column("telematics", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_telematics_deleted_at", "telematics", ["deleted_at"], unique=False)


def downgrade() -> None:
    """Gỡ cột và index soft delete."""
    op.drop_index("ix_telematics_deleted_at", table_name="telematics")
    op.drop_column("telematics", "deleted_at")
